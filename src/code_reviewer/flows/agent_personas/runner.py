"""Persona subprocesses poll cancellation; stop their process groups before checkout cleanup."""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time

from ...shared.claude_cli import ClaudeError, ClaudeResult, ClaudeRunner, build_command, parse_envelope


class ParallelRunner:
    def __init__(self, runner, budget):
        self.runner, self.budget = runner, budget

    def run(self, **kwargs):
        if self.budget.cancelled.is_set():
            raise KeyboardInterrupt
        if hasattr(self.runner, 'run_cancellable'):
            return self.runner.run_cancellable(self.budget.cancelled, **kwargs)
        if not isinstance(self.runner, ClaudeRunner):
            return self.runner.run(**kwargs)
        cmd = build_command(self.runner.claude_bin, system_prompt=kwargs['system_prompt'], schema=kwargs['schema'],
                            model=kwargs['model'], tools=kwargs['tools'], budget_usd=kwargs['budget_usd'],
                            isolation_flags=self.runner.isolation_flags)
        # Repeated communicate(input=None) polls do not resume a partially written
        # stdin pipe. Supply the complete prompt before polling for cancellation.
        with tempfile.TemporaryFile(mode='w+b') as prompt:
            prompt.write(kwargs['user_prompt'].encode('utf-8'))
            prompt.seek(0)
            return self._run(cmd, prompt, kwargs)

    def _run(self, cmd, prompt, kwargs):
        try:
            proc = subprocess.Popen(cmd, stdin=prompt, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding='utf-8', errors='replace', cwd=kwargs['cwd'],
                                    start_new_session=os.name == 'posix', env=self.runner.env)
        except FileNotFoundError as exc:
            raise ClaudeError('claude binary not found: %s' % self.runner.claude_bin) from exc
        started = time.monotonic()
        try:
            while True:
                if self.budget.cancelled.is_set():
                    raise KeyboardInterrupt
                if time.monotonic() - started >= kwargs['timeout_s']:
                    return ClaudeResult(ok=False, error='timed out after %ss' % kwargs['timeout_s'], timed_out=True)
                try:
                    out, err = proc.communicate(timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    pass
            result = parse_envelope(out)
            if proc.returncode and result.ok:
                result.ok, result.error = False, 'claude exited with status %d: %s' % (proc.returncode, err[-300:])
            elif result.raw is None and not result.ok and err:
                result.error = '%s (stderr: %s)' % (result.error, err[-300:])
            return result
        finally:
            # A child can retain pipes after the group leader exits.
            if os.name == 'posix':
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif proc.poll() is None:
                proc.kill()
            proc.communicate()
