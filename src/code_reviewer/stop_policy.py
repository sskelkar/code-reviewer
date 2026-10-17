"""When the multi-pass loop stops. Pure logic computed by the harness, never decided by a model."""
from __future__ import annotations

from typing import Optional

from .config import Config

CONVERGED = "converged"
MAX_PASSES = "max_passes"
BUDGET_EXCEEDED = "budget_exceeded"
FAILURES = "failures"
CANCELLED = "cancelled"


class StopPolicy:
    """
    A pass is *quiet* when it adds no new scored finding. The loop stops when:
      - `quiet_k` consecutive quiet passes have happened after at least `min_passes` completed passes (converged),
      - the run budget is exhausted,
      - `max_consecutive_failures` passes failed in a row, or
      - `max_passes` is reached.
    Failed passes do not count as completed and do not touch the quiet streak.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.completed = 0
        self.quiet_streak = 0
        self.failures_in_row = 0

    def before_pass(self, over_budget: bool) -> Optional[str]:
        return BUDGET_EXCEEDED if over_budget else None

    def after_success(self, new_scored: int) -> Optional[str]:
        self.failures_in_row = 0
        self.completed += 1
        self.quiet_streak = self.quiet_streak + 1 if new_scored == 0 else 0
        if self.completed >= self.cfg.min_passes and self.quiet_streak >= self.cfg.quiet_k:
            return CONVERGED
        return None

    def after_failure(self) -> Optional[str]:
        self.failures_in_row += 1
        return FAILURES if self.failures_in_row >= self.cfg.max_consecutive_failures else None

    def final_reason(self, reason: Optional[str]) -> str:
        """The stop reason to report. Running out of passes with nothing completed counts as failure."""
        if reason:
            return reason
        return FAILURES if self.completed == 0 else MAX_PASSES
