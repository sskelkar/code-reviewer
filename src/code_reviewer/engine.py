"""Stable review entry point; select one flow before loading its assets or doing work."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from .config import Config
from .flows import create_flow
from .shared.events import EventSink
from .shared.models import PRContext

if TYPE_CHECKING:
    from .shared.git_ops import PRRef

__all__ = ["Engine"]


class Engine:
    def __init__(self, cfg: Config, runner: Any, emit: Optional[EventSink] = None,
                 prompts_dir: Optional[Path] = None, defaults_dir: Optional[Path] = None):
        self._flow = create_flow(cfg, runner, emit, prompts_dir, defaults_dir)

    def prepare(self, ref: PRRef, local_path: Optional[Path] = None) -> PRContext:
        return self._flow.prepare(ref, local_path)

    def review(self, ctx: PRContext, keep_checkout: bool = False) -> Any:
        return self._flow.review(ctx, keep_checkout=keep_checkout)

    def dry_run(self, ctx: PRContext) -> Any:
        return self._flow.dry_run(ctx)

    def __getattr__(self, name: str) -> Any:
        # Preserve inspection hooks such as Engine.inputs for existing callers.
        return getattr(object.__getattribute__(self, "_flow"), name)
