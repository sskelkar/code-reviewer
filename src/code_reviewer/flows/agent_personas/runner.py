"""Persona subprocesses poll cancellation; stop their process groups before checkout cleanup."""
from __future__ import annotations

import os
import signal
import subprocess
import time

from ...shared.claude_cli import ClaudeError, ClaudeResult, ClaudeRunner, build_command, parse_envelope


class ParallelRunner:
    def __init__(self, runner, budget):
        self.runner, self.budget = runner, budget

    def run(self, **kwargs):
        if self.budget.cancelled.is_set():
            raise KeyboardInterrupt
        if not isinstance(self.runner, ClaudeRunner):
            return self.runner.run(**kwargs)
        cmd = build_command(self.runner.claude_bin, system_prompt=kwargs['system_prompt'], schema=kwargs['schema'],
                            model=kwargs['model'], tools=kwargs['tools'], budget_usd=kwargs['budget_usd'],
                            isolation_flags=self.runner.isolation_flags)
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding='utf-8', errors='replace', cwd=kwargs['cwd'],
                                    start_new_session=os.name == 'posix')
        except FileNotFoundError as exc:
            raise ClaudeError('claude binary not found: %s' % self.runner.claude_bin) from exc
        started, first = time.monotonic(), True
        try:
            while True:
                if self.budget.cancelled.is_set():
                    raise KeyboardInterrupt
                if time.monotonic() - started >= kwargs['timeout_s']:
                    return ClaudeResult(ok=False, error='timed out after %ss' % kwargs['timeout_s'], timed_out=True)
                try:
                    out, err = proc.communicate(input=kwargs['user_prompt'] if first else None, timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    first = False
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
