"""Exercise both real flows with a synthetic Git repository and fake headless executable."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from ..config import Config
from ..engine import Engine
from ..backends.claude import ClaudeRunner
from ..shared.models import PRContext

RESULT_PREFIX = 'REVIEWER_SMOKE_RESULT='


def git(repo, *args):
    result = subprocess.run(['git', '-c', 'user.name=Synthetic', '-c', 'user.email=fixture@example.invalid',
                             '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', '-C', str(repo), *args],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError('synthetic Git operation failed: ' + result.stderr[-500:])
    return result.stdout.strip()


def run_smoke(runner=None, *, model="haiku", expected_findings=1, provider="claude"):
    results = {}
    with tempfile.TemporaryDirectory(prefix='reviewer-vm-smoke-') as name:
        root = Path(name)
        repo = root / 'fixture'
        repo.mkdir()
        git(repo, 'init', '-q', '-b', 'main')
        (repo / 'app.py').write_text('def value():\n    return 1\n')
        git(repo, 'add', '.')
        git(repo, 'commit', '-qm', 'Synthetic base')
        base = git(repo, 'rev-parse', 'HEAD')
        (repo / 'app.py').write_text('def value():\n    return 2\n')
        git(repo, 'commit', '-qam', 'Synthetic head')
        head = git(repo, 'rev-parse', 'HEAD')
        ctx = PRContext(repo='example/synthetic', number=1, url='', title='Synthetic smoke test', body='',
                        commit_messages='', base_ref='main', base_sha=base, head_sha=head, merge_base=base, git_dir=repo)
        fake = root / 'fake-claude'
        fake.write_bytes(Path(__file__).with_name('fake_claude.py').read_bytes())
        fake.chmod(0o700)
        for mode in ('single-agent', 'agent-personas'):
            cfg = Config(provider=provider, model_reviewer=model, model_comparator=model, model_resolver=model, model_verifier=model,
                         review_mode=mode, criteria_mode='defaults_only', min_passes=1, max_passes=2,
                         persona_passes=1, claude_bin=str(fake), runs_dir=str(root / mode))
            result = Engine(cfg, runner if runner is not None else ClaudeRunner(str(fake))).review(ctx)
            expected = 2 if mode == 'single-agent' else 4
            if (result.passes_completed != expected
                    or (expected_findings is not None and len(result.master.scored()) != expected_findings)):
                raise RuntimeError('%s review/consolidation did not complete' % mode)
            if result.stats.retries_and_failures()['failed_calls'] or not result.report_path.is_file():
                raise RuntimeError('%s failed calls or report missing' % mode)
            results[mode] = {'completed': result.passes_completed, 'findings': len(result.master.scored()),
                             'calls': len(result.stats.calls), 'stop_reason': result.stop_reason}
    return {'format': 1, 'synthetic': True, 'flows': results}


if __name__ == '__main__':
    # No target repository, inherited credentials, gh invocation or network is needed.
    print(RESULT_PREFIX + json.dumps(run_smoke(), sort_keys=True), flush=True)
