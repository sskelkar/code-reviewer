"""Fixed-endpoint OAuth/OIDC transport and system-OpenSSL identity verification."""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

from ...config import ConfigError

ISSUER = 'https://auth.openai.com'
RESOURCE = 'https://api.openai.com/v1'
AUTHORIZE = ISSUER + '/api/accounts/authorize'
TOKEN_PATH = '/api/accounts/oauth/token'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
TOKEN_BYTES = 16384
HTTP_BYTES = 256 * 1024
ERRORS = frozenset(('invalid_grant', 'invalid_client', 'invalid_scope', 'access_denied',
                    'invalid_refresh_token', 'token_expired', 'refresh_token_expired',
                    'refresh_token_invalidated', 'refresh_token_reused', 'temporarily_unavailable'))
TERMINAL = ERRORS - {'invalid_client', 'invalid_scope', 'access_denied', 'temporarily_unavailable'}


class AuthError(ConfigError):
    def __init__(self, status, code=None):
        self.status = status
        self.code = code if isinstance(code, str) and code in ERRORS else None
        super().__init__('ChatGPT authentication HTTP %d%s' % (status, ' ('+self.code+')' if self.code else ''))


def request(path, form=None):
    if not isinstance(path, str) or not path.startswith('/') or len(path) > 256 or '?' in path or '#' in path:
        raise ConfigError('invalid fixed authentication endpoint')
    conn = http.client.HTTPSConnection('auth.openai.com', timeout=20)
    try:
        body = urlencode(form).encode() if form is not None else None
        if body is not None and len(body) > HTTP_BYTES:
            raise ConfigError('authentication request exceeds its byte limit')
        conn.request('POST' if form is not None else 'GET', path, body,
                     {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'})
        reply = conn.getresponse()
        data, start = bytearray(), time.monotonic()
        while len(data) <= HTTP_BYTES:
            remaining = 20 - (time.monotonic()-start)
            if remaining <= 0:
                raise TimeoutError
            if conn.sock is not None:
                conn.sock.settimeout(remaining)
            chunk = reply.read1(min(65536, HTTP_BYTES+1-len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > HTTP_BYTES:
            raise ConfigError('authentication response exceeds its byte limit')
        try:
            value = json.loads(data) if data else {}
        except (ValueError, RecursionError):
            value = {}
        if reply.status != 200:
            raise AuthError(reply.status, value.get('error') if isinstance(value, dict) else None)
        if not isinstance(value, dict):
            raise ConfigError('invalid authentication response')
        return value
    except (OSError, http.client.HTTPException, ValueError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError('ChatGPT authentication transport failed; credentials were not logged') from None
    finally:
        conn.close()


def discovery():
    value = request('/.well-known/openid-configuration')
    expected = {'issuer': ISSUER, 'authorization_endpoint': AUTHORIZE,
                'token_endpoint': ISSUER+TOKEN_PATH, 'jwks_uri': ISSUER+'/.well-known/jwks.json'}
    if any(value.get(k) != v for k,v in expected.items()):
        raise ConfigError('OpenAI discovery endpoints differ from the supported policy')
    revoke = value.get('revocation_endpoint')
    if not isinstance(revoke, str) or not revoke.startswith(ISSUER+'/') or len(revoke) > 256 or '?' in revoke or '#' in revoke:
        raise ConfigError('invalid OpenAI revocation endpoint')
    return value


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def unb64(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value) or len(value) > TOKEN_BYTES:
        raise ConfigError('invalid signed identity encoding')
    try:
        return base64.b64decode(value+'='*((-len(value))%4), altchars=b'-_', validate=True)
    except ValueError:
        raise ConfigError('invalid signed identity encoding') from None


def text(value, limit=TOKEN_BYTES):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c)<33 or ord(c)>126 for c in value):
        raise ConfigError('invalid bounded authentication value')
    return value


def _der(tag, value):
    n = len(value)
    length = bytes([n]) if n < 128 else bytes([128+(n.bit_length()+7)//8])+n.to_bytes((n.bit_length()+7)//8, 'big')
    return bytes([tag])+length+value


def public_key(jwk):
    if jwk.get('kty') != 'RSA' or jwk.get('alg', 'RS256') != 'RS256' or jwk.get('use', 'sig') != 'sig':
        raise ConfigError('unsupported OpenAI identity signing key')
    if 'key_ops' in jwk and jwk['key_ops'] != ['verify']:
        raise ConfigError('unsupported identity key operations')
    n, e = unb64(jwk.get('n')), unb64(jwk.get('e'))
    modulus, exponent = int.from_bytes(n,'big'), int.from_bytes(e,'big')
    if not 2048 <= modulus.bit_length() <= 8192 or not 3 <= exponent < 2**32 or exponent%2 != 1:
        raise ConfigError('unsupported RSA identity key size')
    integer = lambda data: _der(2, (b'\0' if data[0]&128 else b'')+data)
    rsa = _der(48, integer(n)+integer(e))
    spki = _der(48, bytes.fromhex('300d06092a864886f70d0101010500')+_der(3,b'\0'+rsa))
    return b'-----BEGIN PUBLIC KEY-----\n'+base64.encodebytes(spki)+b'-----END PUBLIC KEY-----\n'


def verify_id(token, client_id, *, nonce=None, subject=None, access_token=None, jwks=None):
    try:
        text(token)
        parts = token.split('.')
        if len(parts) != 3:
            raise ValueError
        header, claims = (json.loads(unb64(p)) for p in parts[:2])
        if not isinstance(header,dict) or not isinstance(claims,dict) or header.get('alg') != 'RS256' or header.get('crit'):
            raise ValueError
        kid = text(header.get('kid'), 256)
        keys = (request('/.well-known/jwks.json') if jwks is None else jwks).get('keys')
        if not isinstance(keys,list) or len(keys)>32:
            raise ValueError
        keys = [k for k in keys if isinstance(k,dict) and k.get('kid')==kid]
        if len(keys)!=1:
            raise ValueError
        signature = unb64(parts[2])
        if len(signature) != (int.from_bytes(unb64(keys[0]['n']),'big').bit_length()+7)//8:
            raise ValueError
        # OpenSSL supplies the cryptographic verification; only public key and
        # signature files are written, inside a private temporary directory.
        with tempfile.TemporaryDirectory(prefix='reviewer-oidc-') as folder:
            key, sig = Path(folder)/'public.pem', Path(folder)/'signature'
            for path,data in ((key,public_key(keys[0])),(sig,signature)):
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as out:
                    out.write(data)
            proc = subprocess.run(['/usr/bin/openssl','dgst','-sha256','-verify',str(key),'-signature',str(sig)],
                    input=(parts[0]+'.'+parts[1]).encode('ascii'),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                    timeout=5,env={'PATH':'/usr/bin:/bin','LANG':'C'})
            if proc.returncode:
                raise ValueError
        now=time.time()
        for name in ('exp','iat'):
            if type(claims.get(name)) is not int:
                raise ValueError
        aud=claims.get('aud')
        if not (aud==client_id or isinstance(aud,list) and 0<len(aud)<=8 and client_id in aud and all(isinstance(a,str) for a in aud)):
            raise ValueError
        if isinstance(aud,list) and len(aud)>1 and claims.get('azp')!=client_id:
            raise ValueError
        if claims.get('iss')!=ISSUER or claims['exp']<=now-5 or claims['iat']>now+5:
            raise ValueError
        if 'nbf' in claims and (type(claims['nbf']) is not int or claims['nbf']>now+5):
            raise ValueError
        text(claims.get('sub'),256)
        if subject is not None and claims['sub']!=subject:
            raise ValueError
        if nonce is not None and (not isinstance(claims.get('nonce'),str) or not hmac.compare_digest(claims['nonce'],nonce)):
            raise ValueError
        if 'at_hash' in claims and access_token is not None and claims['at_hash']!=b64(hashlib.sha256(access_token.encode()).digest()[:16]):
            raise ValueError
        return claims
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, subprocess.TimeoutExpired):
        raise ConfigError('ChatGPT identity verification failed; no credentials were saved') from None
