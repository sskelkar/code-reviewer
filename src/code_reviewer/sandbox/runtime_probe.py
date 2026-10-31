"""Check the real Linux CLI offline with a fresh home and no credentials."""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

RESULT_PREFIX = 'REVIEWER_RUNTIME_RESULT='
VERSION = '2.1.301 (Claude Code)'


def parse_result(output):
    records = [line[len(RESULT_PREFIX):] for line in output.splitlines() if line.startswith(RESULT_PREFIX)]
    if len(records) != 1:
        raise ValueError('guest must return exactly one runtime result')
    data = json.loads(records[0])
    expected = {'format': 1, 'version': VERSION, 'authenticated': False,
                'auth_method': 'none', 'headless_auth_refused': True}
    if (data != expected or type(data.get('format')) is not int
            or data.get('authenticated') is not False or data.get('headless_auth_refused') is not True):
        raise ValueError('unexpected offline runtime result')
    return data


def run_probe(binary=Path('/usr/bin/claude')):
    # Import lazily so offline result validation does not load process helpers.
    from .runtime import collect
    with tempfile.TemporaryDirectory(prefix='reviewer-runtime-') as name:
        root = Path(name)
        home = root / 'home'
        home.mkdir(mode=0o700)
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'TMPDIR': str(root), 'LANG': 'C.UTF-8',
               'CLAUDE_CONFIG_DIR': str(home / '.claude'), 'DISABLE_AUTOUPDATER': '1',
               'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'ENABLE_CLAUDEAI_MCP_SERVERS': 'false'}
        def run(*args, allowed=(0,)):
            return collect([str(binary), *args], root, 45, env=env, allowed_codes=allowed)
        version = run('--version').strip()
        if version != VERSION:
            raise ValueError('unexpected pinned Claude version')
        help_text = run('--help')
        for flag in ('--strict-mcp-config', '--disable-slash-commands', '--no-session-persistence'):
            if flag not in help_text:
                raise ValueError('required CLI flag missing: ' + flag)
        auth = json.loads(run('auth', 'status', '--json', allowed=(1,)))
        if auth.get('loggedIn') is not False or auth.get('authMethod') != 'none':
            raise ValueError('offline probe must not have credentials')
        from ..backends.claude import build_command
        cmd = build_command(str(binary), system_prompt='Reply OK.', schema={'type': 'object'},
                            model='haiku', tools='', budget_usd=.01,
                            isolation_flags=['--safe-mode', '--restricted'])
        reply = run(*cmd[1:], '--setting-sources', '', 'Reply OK.', allowed=(1,))
        if not re.search(r'not logged in|authentication required|please (?:run|log in)', reply, re.I):
            raise ValueError('expected an unauthenticated headless refusal')
        return {'format': 1, 'version': version, 'authenticated': False,
                'auth_method': 'none', 'headless_auth_refused': True}


if __name__ == '__main__':
    print(RESULT_PREFIX + json.dumps(run_probe(), sort_keys=True), flush=True)
