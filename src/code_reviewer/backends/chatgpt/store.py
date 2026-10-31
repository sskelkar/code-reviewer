"""Private, atomic account records with serialized rotating-token refresh."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from ...config import ConfigError, DATA_DIR
from . import oidc

MAX_STORE = 1024 * 1024
REQUIRED_SCOPES = {'openid','offline_access','resource.invoke','chatgpt.tokens.use.direct'}


def account_name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}',value):
        raise ConfigError('ChatGPT account must be a local label of 1..64 letters, digits, underscores or hyphens')
    return value


def runtime_key(cfg):
    if not cfg.sandbox:
        return 'local'
    identity=str(Path(cfg.vm_lima_home).expanduser().absolute())+'\0'+cfg.vm_instance
    return 'vm-'+hashlib.sha256(identity.encode()).hexdigest()[:32]


def token_record(reply, client_id, host_id, *, nonce=None, previous=None, verify=None):
    verify = verify or oidc.verify_id
    if not isinstance(reply,dict) or not isinstance(reply.get('token_type'),str) or reply['token_type'].lower()!='bearer':
        raise ConfigError('invalid ChatGPT token response')
    access=oidc.text(reply.get('access_token'))
    refresh=oidc.text(reply.get('refresh_token'))
    expires=reply.get('expires_in')
    if type(expires) is not int or not 1 <= expires <= 3600:
        raise ConfigError('invalid ChatGPT token expiry')
    scope=reply.get('scope')
    if not isinstance(scope,str) or len(scope)>4096:
        raise ConfigError('missing ChatGPT granted scopes')
    scopes=scope.split()
    if len(scopes)>32 or not REQUIRED_SCOPES.issubset(scopes):
        raise ConfigError('ChatGPT plan permission was not granted; sign in and approve plan usage')
    identity=reply.get('id_token')
    if identity is None and previous is None:
        raise ConfigError('missing ChatGPT identity token')
    claims=None
    if identity is not None:
        claims=verify(identity,client_id,nonce=nonce,subject=previous['subject'] if previous else None,access_token=access)
        if previous and nonce is None and 'nonce' in claims and claims['nonce']!=previous['nonce']:
            raise ConfigError('refreshed ChatGPT identity nonce mismatch')
    elif previous:
        identity=previous.get('id_token','')
    record=dict(previous or {})
    record.update(client_id=oidc.text(client_id,256),host_id=host_id,issuer=oidc.ISSUER,
                  subject=claims['sub'] if claims else previous['subject'],
                  email=claims.get('email','') if claims else previous.get('email',''),
                  nonce=nonce if nonce is not None else previous.get('nonce',''),
                  access_token=access,refresh_token=refresh,id_token=identity,
                  scopes=scopes,expires_at=time.time()+expires)
    if not isinstance(record['email'],str) or len(record['email'])>256:
        raise ConfigError('invalid ChatGPT account metadata')
    return record


class Store:
    def __init__(self,root=None):
        self.root=Path(root) if root is not None else DATA_DIR/'auth'

    @contextmanager
    def locked(self):
        root=self.root.expanduser().absolute()
        if root.is_symlink():
            raise ConfigError('ChatGPT authentication directory must not be a symlink')
        root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        root = root.parent.resolve(strict=True) / root.name
        if any((parent/'.git').exists() for parent in (root,*root.parents)):
            raise ConfigError('authentication storage must be outside Git working trees')
        root.mkdir(mode=0o700,parents=True,exist_ok=True)
        directory=os.open(root.anchor,os.O_RDONLY|os.O_DIRECTORY)
        try:
            for part in root.parts[1:]:
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
                os.close(directory)
                directory=child
        except BaseException:
            os.close(directory)
            raise
        lock=None
        try:
            self._private(os.fstat(directory),directory=True)
            lock=os.open('chatgpt.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=directory)
            self._private(os.fstat(lock))
            deadline=time.monotonic()+30
            while True:
                try:
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic()>=deadline:
                        raise ConfigError('ChatGPT credentials are busy; retry later') from None
                    time.sleep(.05)
            yield directory
        except OSError:
            raise ConfigError('ChatGPT credential store requires private owned directories and regular files') from None
        finally:
            if lock is not None:
                os.close(lock)
            os.close(directory)

    @staticmethod
    def _private(info,directory=False):
        valid=stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink==1
        if not valid or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise ConfigError('ChatGPT credential storage must be owned by you with owner-only permissions')

    def load(self,fd):
        try:
            source=os.open('chatgpt.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        except FileNotFoundError:
            return {'format':1,'hosts':{},'accounts':{}}
        with os.fdopen(source,'rb') as stream:
            self._private(os.fstat(stream.fileno()))
            data=stream.read(MAX_STORE+1)
        try:
            value=json.loads(data)
            if len(data)>MAX_STORE or not isinstance(value,dict) or set(value)!={'format','hosts','accounts'} or value['format']!=1:
                raise ValueError
            if not isinstance(value['hosts'],dict) or not isinstance(value['accounts'],dict) or len(value['accounts'])>32 or len(value['hosts'])>32:
                raise ValueError
            for runtime, host in value['hosts'].items():
                if not isinstance(runtime, str) or len(runtime) > 128 or not isinstance(host, str) or not host.startswith('urn:uuid:'):
                    raise ValueError
                uuid.UUID(host[9:])
            for runtime, accounts in value['accounts'].items():
                if runtime not in value['hosts'] or not isinstance(accounts, dict) or len(accounts) > 16:
                    raise ValueError
                for alias, record in accounts.items():
                    account_name(alias)
                    if not isinstance(record, dict) or record.get('host_id') != value['hosts'][runtime] or record.get('issuer') != oidc.ISSUER:
                        raise ValueError
                    for field in ('client_id', 'subject'):
                        oidc.text(record.get(field), 256)
                    for field in ('access_token', 'refresh_token', 'id_token', 'nonce', 'email'):
                        if not isinstance(record.get(field), str) or len(record[field]) > oidc.TOKEN_BYTES:
                            raise ValueError
                    if type(record.get('expires_at')) not in (int, float) or not math.isfinite(record['expires_at']):
                        raise ValueError
                    scopes = record.get('scopes')
                    if not isinstance(scopes, list) or len(scopes) > 32 or any(not isinstance(x, str) for x in scopes):
                        raise ValueError
            return value
        except (ValueError, TypeError, RecursionError):
            raise ConfigError('invalid or oversized private ChatGPT credential store') from None

    def save(self,fd,value):
        data=json.dumps(value,ensure_ascii=True).encode()
        if len(data)>MAX_STORE:
            raise ConfigError('ChatGPT credential store byte limit reached')
        name='chatgpt-'+uuid.uuid4().hex+'.tmp'
        target=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        try:
            with os.fdopen(target,'wb') as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name,'chatgpt.json',src_dir_fd=fd,dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(name,dir_fd=fd)
            except FileNotFoundError:
                pass

    def prepare(self,runtime,account):
        account_name(account)
        with self.locked() as fd:
            data=self.load(fd)
            if runtime not in data['hosts']:
                if len(data['hosts'])>=32:
                    raise ConfigError('ChatGPT host limit reached')
                data['hosts'][runtime]='urn:uuid:'+str(uuid.uuid4())
                self.save(fd,data)
            previous=data['accounts'].get(runtime,{}).get(account)
            return data['hosts'][runtime],previous

    def install(self,runtime,account,record):
        account_name(account)
        with self.locked() as fd:
            data=self.load(fd)
            accounts=data['accounts'].setdefault(runtime,{})
            if len(accounts)>=16 and account not in accounts:
                raise ConfigError('ChatGPT account limit reached')
            previous=accounts.get(account)
            if previous and (previous['client_id'],previous['subject'])!=(record['client_id'],record['subject']):
                raise ConfigError('selected ChatGPT identity changed; use another account label')
            if data['hosts'].get(runtime)!=record['host_id']:
                raise ConfigError('ChatGPT runtime identity changed during sign-in')
            accounts[account]=record
            self.save(fd,data)

    def records(self,runtime):
        with self.locked() as fd:
            return self.load(fd)['accounts'].get(runtime,{})

    def access(self,runtime,account,min_ttl=60):
        account_name(account)
        if not isinstance(min_ttl,(int,float)) or not math.isfinite(min_ttl) or not 0<min_ttl<3500:
            raise ConfigError('ChatGPT calls/jobs must leave time within the one-hour access-token lifetime')
        with self.locked() as fd:
            data=self.load(fd)
            record=data['accounts'].get(runtime,{}).get(account)
            if not record or not record.get('refresh_token'):
                raise ConfigError('ChatGPT account is not signed in for this runtime; run reviewer auth login')
            if record.get('issuer')!=oidc.ISSUER or record.get('host_id')!=data['hosts'].get(runtime):
                raise ConfigError('ChatGPT stored identity does not match this runtime')
            if type(record.get('expires_at')) not in (int,float) or not math.isfinite(record['expires_at']):
                raise ConfigError('invalid saved ChatGPT expiry')
            if not REQUIRED_SCOPES.issubset(record.get('scopes',[])):
                raise ConfigError('ChatGPT plan permission is missing; sign in again')
            if record['expires_at']-time.time()<=min_ttl:
                try:
                    reply=oidc.request(oidc.TOKEN_PATH,dict(grant_type='refresh_token',client_id=record['client_id'],
                                       refresh_token=record['refresh_token'],resource=oidc.RESOURCE))
                except oidc.AuthError as exc:
                    if exc.code in oidc.TERMINAL:
                        for name in ('access_token','refresh_token','id_token'):
                            record[name]=''
                        self.save(fd,data)
                    raise
                renewed=token_record(reply,record['client_id'],record['host_id'],previous=record)
                data['accounts'][runtime][account]=record=renewed
                self.save(fd,data)
            if record['expires_at']-time.time()<=min_ttl:
                raise ConfigError('ChatGPT access token cannot cover this bounded call/job; sign in again')
            return oidc.text(record['access_token'])

    def logout(self,runtime,account):
        account_name(account)
        confirmed=True
        with self.locked() as fd:
            data=self.load(fd)
            record=data['accounts'].get(runtime,{}).get(account)
            if record and record.get('refresh_token'):
                try:
                    path=oidc.discovery()['revocation_endpoint'][len(oidc.ISSUER):]
                    oidc.request(path,dict(token=record['refresh_token'],token_type_hint='refresh_token',client_id=record['client_id']))
                except ConfigError:
                    confirmed=False
                for name in ('access_token','refresh_token','id_token'):
                    record[name]=''
                self.save(fd,data)
        return confirmed
