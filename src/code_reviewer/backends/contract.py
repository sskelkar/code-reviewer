"""Provider-neutral runner contract used by review algorithms and accounting."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Dict, List, Optional, Protocol, Sequence

TOKEN_KEYS = ('input', 'output', 'cache_creation', 'cache_read')


class RunnerError(RuntimeError):
    """The selected adapter could not be started or used; never select a fallback."""


@dataclass
class LLMResult:
    ok: bool
    parsed: Optional[Dict[str, Any]] = None
    text: str = ''
    error: Optional[str] = None
    cost_usd: Optional[float] = None
    tokens: Dict[str, int] = field(default_factory=lambda: {k: 0 for k in TOKEN_KEYS})
    duration_ms: Optional[int] = None
    num_turns: Optional[int] = None
    model: Optional[str] = None
    budget_exceeded: bool = False
    timed_out: bool = False
    raw: Any = None
    retryable: bool = True
    http_errors: List[Dict[str, Any]] = field(default_factory=list)
    billing_mode: str = 'unknown'

    @property
    def total_tokens(self) -> int:
        return sum(self.tokens.values())


class Runner(Protocol):
    """One fresh, structured conversation; adapters enforce only the allowed tools.

    No adapter chooses a review strategy, reads prior findings, changes criteria,
    posts comments or selects another provider/authentication method on failure.
    """
    def run(self, *, system_prompt: str, user_prompt: str, schema: Dict[str, Any], model: str,
            tools: str, cwd: Path, budget_usd: float, timeout_s: float,
            add_dirs: Sequence[Path] = ()) -> LLMResult:
        ...

    def run_cancellable(self, cancelled: Optional[Event], **kwargs: Any) -> LLMResult:
        ...

    def version(self) -> str:
        ...
