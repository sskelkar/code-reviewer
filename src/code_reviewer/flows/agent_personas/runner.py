"""Pass the persona run cancellation signal to adapters that support it."""
from __future__ import annotations


class ParallelRunner:
    def __init__(self, runner, budget):
        self.runner, self.budget = runner, budget

    def run(self, **kwargs):
        if self.budget.cancelled.is_set():
            raise KeyboardInterrupt
        if hasattr(self.runner, 'run_cancellable'):
            return self.runner.run_cancellable(self.budget.cancelled, **kwargs)
        return self.runner.run(**kwargs)
