"""Structured progress events emitted by the engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict
import unicodedata


@dataclass
class Event:
    type: str
    data: Dict[str, Any] = field(default_factory=dict)


EventSink = Callable[[Event], None]


def null_sink(event: Event) -> None:
    return None


def display_text(value: str, limit: int = 240) -> str:
    """Keep model/repository text on one terminal-safe line."""
    return ''.join(c if c.isprintable() and unicodedata.category(c) != 'Cf' else ' '
                   for c in value[:limit])


def round_preview(number, master, previous, completed, failed):
    """Count grounded sightings after consolidation without exposing unverified claims."""
    items = master.scored()
    new = [item for item in items if item['id'] not in previous]
    return dict(round=number, completed=completed, failed=failed, total=len(items), new=len(new), findings=[])


def reviewer_preview(payload):
    """Count returned proposals without exposing their unverified titles or locations."""
    findings = payload.get('findings', []) if isinstance(payload, dict) else []
    if not isinstance(findings, list):
        findings = []
    rejected = sum(isinstance(finding, dict) and isinstance(finding.get('self_check'), dict)
                   and finding['self_check'].get('verdict') == 'rejected' for finding in findings)
    return dict(raised=len(findings), self_rejected=rejected, findings=[])
