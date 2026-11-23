"""Pure unit tests, mock all HTTP calls. Run: python3 test_auth.py

Requires application deps inside the container, or use a local Python with httpx/cryptography.
No live API traffic is generated.
"""
import ast
import asyncio
import base64
import os
import secrets
from pathlib import Path
from cryptography.fernet import Fernet

source = Path(__file__).with_name('bot.py').read_text()
module = ast.parse(source)
names = {'normalize_cf_token', 'format_cf_errors', 'cf_request', 'list_zones_with_token', 'verify_token_for_account', 'decrypt_token'}
selected = [n for n in module.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
code = compile(ast.Module(body=selected, type_ignores=[]), 'bot_extract.py', 'exec')

class Response:
    status_code = 200
    headers = {'content-type': 'application/json'}
    is_error = False
    def json(self): return {'success': True, 'result': [{'id': 'ok'}]}

class FakeClient:
    def __init__(self, *args, **kwargs): self.kwargs = kwargs
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def request(self, method, path, **kwargs):
        assert kwargs['headers']['Authorization'] == 'Bearer valid_token'
        assert path.startswith('https://api.cloudflare.com')
        return Response()
    async def get(self, path, **kwargs):
        assert kwargs['headers']['Authorization'] == 'Bearer valid_token'
        assert kwargs['params']['account.id'] == 'accountid'
        return Response()

class FakeHttpx:
    AsyncClient = FakeClient

cipher = Fernet(Fernet.generate_key())
ns = {'httpx': FakeHttpx, 'CF_API':'https://api.cloudflare.com/client/v4',
      'FERNET':cipher, 'InvalidToken':Exception, 'RuntimeError':RuntimeError}
exec(code, ns)
normalize = ns['normalize_cf_token']
for inp in ['valid_token', 'Bearer valid_token', '  Bearer valid_token  ',
            'Authorization: Bearer valid_token', '"Bearer valid_token"',
            'Bearer "valid_token"', "'valid_token'"]:
    assert normalize(inp) == 'valid_token', repr(inp)
for inp in ['', 'Bearer ', 'valid token', 'token\nmore', 'cfk_abc']:
    try: normalize(inp)
    except ValueError: pass
    else: raise AssertionError('Expected invalid token: ' + repr(inp))

assert ns['format_cf_errors']({'errors':[{'code':6003,'message':'Invalid request headers',
       'error_chain':[{'code':6111,'message':'Invalid format for Authorization header'}]}]},400) == (
       'Invalid request headers (6003); Invalid format for Authorization header (6111)')

async def run():
    account = {'token_enc':cipher.encrypt(b'Bearer valid_token')}
    result = await ns['cf_request'](account,'GET','/zones')
    assert result['success'] is True
    n = await ns['verify_token_for_account']('Bearer valid_token','accountid')
    assert n == 1

asyncio.run(run())
print('PASS: 15 auth and API interface checks (no network, no credentials)')
