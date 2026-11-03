"""Stable review entry point; run the multi-perspective review."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from .backends.contract import Runner
from .config import Config
from .review.flow import ReviewFlow
from .shared.events import EventSink
from .shared.models import PRContext
from .tracing import traced

if TYPE_CHECKING:
    from .shared.git_ops import PRRef

__all__ = ["Engine"]


class Engine:
    def __init__(self, cfg: Config, runner: Optional[Runner], emit: Optional[EventSink] = None,
                 prompts_dir: Optional[Path] = None, defaults_dir: Optional[Path] = None):
        self._flow = ReviewFlow(cfg, runner, emit, prompts_dir, defaults_dir)

    @traced('review.prepare')
    def prepare(self, ref: PRRef, local_path: Optional[Path] = None) -> PRContext:
        return self._flow.prepare(ref, local_path)

    @traced('review.flow')
    def review(self, ctx: PRContext, keep_checkout: bool = False, prepared_checkout: Optional[Path] = None) -> Any:
        if prepared_checkout is not None:
            return self._flow.review(ctx, keep_checkout=keep_checkout, prepared_checkout=prepared_checkout)
        return self._flow.review(ctx, keep_checkout=keep_checkout)

    def dry_run(self, ctx: PRContext) -> Any:
        return self._flow.dry_run(ctx)

    def __getattr__(self, name: str) -> Any:
        # Preserve inspection hooks such as Engine.inputs for existing callers.
        return getattr(object.__getattribute__(self, "_flow"), name)
