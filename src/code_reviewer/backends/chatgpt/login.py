"""Explicit local browser consent and bounded loopback callback handling."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import socket
import time
import webbrowser
from urllib.parse import parse_qs, urlencode, urlsplit

from ...config import ConfigError
from . import oidc
from .store import Store, account_name, runtime_key, token_record

CALLBACK_BYTES = 16384


class ConsentDeclined(ConfigError):
    pass


def callback(target, expected_state, issued=None):
    parsed=urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.path!='/auth/callback' or parsed.fragment or len(target)>CALLBACK_BYTES:
        raise ConfigError('invalid ChatGPT callback path')
    try:
        values=parse_qs(parsed.query,keep_blank_values=True,max_num_fields=12,strict_parsing=True)
    except ValueError:
        raise ConfigError('invalid ChatGPT callback') from None
    if any(len(v)!=1 for v in values.values()):
        raise ConfigError('duplicate ChatGPT callback parameters')
    state=values.get('state',[''])[0]
    if not state.isascii() or not hmac.compare_digest(state,expected_state):
        raise ConfigError('ChatGPT callback state mismatch')
    if 'error' in values:
        raise ConsentDeclined('ChatGPT sign-in was declined or failed; no code was exchanged')
    code=oidc.text(values.get('code',[''])[0],4096)
    client=values.get('client_id',[issued])[0]
    oidc.text(client,256)
    if client=='dynamic_agent_client' or issued is not None and client!=issued:
        raise ConfigError('ChatGPT callback changed the registered client')
    return code,client


def read_request(client, deadline):
    data=bytearray()
    while b'\r\n\r\n' not in data:
        remaining=deadline-time.monotonic()
        if remaining<=0:
            raise ConfigError('ChatGPT callback request timed out')
        client.settimeout(remaining)
        chunk=client.recv(min(4096,CALLBACK_BYTES+1-len(data)))
        if not chunk:
            raise ConfigError('incomplete ChatGPT callback request')
        data.extend(chunk)
        if len(data)>CALLBACK_BYTES:
            raise ConfigError('ChatGPT callback exceeds its byte limit')
    try:
        lines=data.decode('ascii').split('\r\n')
        method,target,version=lines[0].split(' ')
        if method!='GET' or version not in ('HTTP/1.1','HTTP/1.0'):
            raise ValueError
        headers={}
        for line in lines[1:]:
            if not line:
                break
            name,value=line.split(':',1)
            name=name.lower()
            if name in headers:
                raise ValueError
            headers[name]=value.strip()
        return target,headers
    except (ValueError,UnicodeError):
        raise ConfigError('invalid ChatGPT callback HTTP request') from None


def _reply(client,ok):
    body=b'Sign-in response received. Return to your terminal.' if ok else b'Invalid sign-in callback.'
    client.sendall(('HTTP/1.1 %s\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n'
                    'Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\n'
                    'Content-Security-Policy: default-src \'none\'\r\nConnection: close\r\n\r\n' %
                    ('200 OK' if ok else '400 Bad Request',len(body))).encode()+body)


def authorize(host_id,issued=None,*,no_browser=False,open_browser=webbrowser.open,clock=time.monotonic):
    verifier=secrets.token_urlsafe(48)
    state,nonce=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as server:
        server.bind(('127.0.0.1',0))
        server.listen(1)
        server.settimeout(1)
        port=server.getsockname()[1]
        redirect='http://127.0.0.1:%d/auth/callback' % port
        params=dict(client_id=issued or 'dynamic_agent_client',ext_agent_host_id=host_id,
                    response_type='code',redirect_uri=redirect,scope=oidc.SCOPES,resource=oidc.RESOURCE,
                    state=state,nonce=nonce,code_challenge_method='S256',
                    code_challenge=oidc.b64(hashlib.sha256(verifier.encode()).digest()))
        if issued is None:
            params['agent_name_hint']='code-reviewer'
        url=oidc.AUTHORIZE+'?'+urlencode(params)
        # No ID-token/email hints in the URL. The callback state/PKCE are local
        # one-time transactions; access/refresh/identity tokens never appear here.
        print('Continue with ChatGPT. Waiting for browser consent (up to five minutes).',flush=True)
        if no_browser or not open_browser(url):
            print(url,flush=True)
        deadline=clock()+300
        for _ in range(128):
            if clock()>=deadline:
                break
            try:
                client,address=server.accept()
            except socket.timeout:
                continue
            with client:
                try:
                    target,headers=read_request(client,min(deadline,clock()+5))
                    if address[0]!='127.0.0.1' or headers.get('host')!='127.0.0.1:%d' % port:
                        raise ConfigError('invalid ChatGPT callback host')
                    code,client_id=callback(target,state,issued)
                    _reply(client,True)
                    return dict(code=code,client_id=client_id,nonce=nonce,code_verifier=verifier,redirect_uri=redirect)
                except ConsentDeclined:
                    _reply(client,False)
                    raise
                except (ConfigError,OSError):
                    try:
                        _reply(client,False)
                    except OSError:
                        pass
        raise ConfigError('ChatGPT sign-in expired or callback limit reached; start again')


def login(cfg,account,*,store=None,no_browser=False):
    store=store or Store()
    account_name(account)
    runtime=runtime_key(cfg)
    if cfg.sandbox:
        from pathlib import Path
        import re
        if not cfg.vm_lima_home or not Path(cfg.vm_lima_home).is_absolute() or not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', cfg.vm_instance):
            raise ConfigError('set absolute vm_lima_home and a valid vm_instance before VM sign-in, or select --no-sandbox')
    host,previous=store.prepare(runtime,account)
    oidc.discovery()
    issued=previous['client_id'] if previous else None
    for attempt in range(2):
        pending=authorize(host,issued,no_browser=no_browser)
        issued=pending['client_id']
        try:
            reply=oidc.request(oidc.TOKEN_PATH,dict(grant_type='authorization_code',client_id=issued,
                    code=pending['code'],code_verifier=pending['code_verifier'],
                    redirect_uri=pending['redirect_uri'],resource=oidc.RESOURCE))
        except oidc.AuthError as exc:
            if exc.code=='invalid_grant' and attempt==0:
                continue
            raise
        record=token_record(reply,issued,host,nonce=pending['nonce'],previous=previous)
        store.install(runtime,account,record)
        return


def cli(args,cfg):
    store=Store()
    account=args.account or cfg.chatgpt_account
    runtime=runtime_key(cfg)
    if args.no_sandbox:
        cfg.sandbox=False
        runtime=runtime_key(cfg)
    if args.auth_command=='login':
        login(cfg,account,store=store,no_browser=args.no_browser)
        print('ChatGPT account %s is signed in for %s execution.' % (account,'VM' if cfg.sandbox else 'host'))
        print('Select an account-supported model, then run review with --provider openai --auth subscription.')
        return 0
    if args.auth_command=='models':
        from .models import catalog
        token = store.access(runtime, account)
        for model in catalog(token):
            print(model)
        return 0
    if args.auth_command=='logout':
        confirmed=store.logout(runtime,account)
        print('Local tokens cleared. '+('Remote revocation confirmed.' if confirmed else
              'Remote revocation was not confirmed; disconnect the app in ChatGPT settings.'))
        return 0 if confirmed else 1
    records=store.records(runtime)
    print(json.dumps({'runtime':'vm' if cfg.sandbox else 'host','selected_account':account,'offline':True,
          'accounts':[dict(account=name,has_credentials=bool(r.get('refresh_token')),expires_at=r.get('expires_at'))
                      for name,r in sorted(records.items())]},indent=2))
    return 0
