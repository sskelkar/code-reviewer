"""Structured progress events emitted by the engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict


@dataclass
class Event:
    type: str
    data: Dict[str, Any] = field(default_factory=dict)


EventSink = Callable[[Event], None]


def null_sink(event: Event) -> None:
    return None
