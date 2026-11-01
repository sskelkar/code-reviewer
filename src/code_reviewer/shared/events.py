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


def round_preview(mode, number, master, previous, completed, failed):
    """Preview only accepted sightings, after consolidation; never self-rejected claims."""
    items = master.scored()
    new = [item for item in items if item['id'] not in previous]
    return dict(mode=mode, round=number, completed=completed, failed=failed, total=len(items), new=len(new),
                findings=[dict(id=item['id'], title=display_text(item['canonical']['title']),
                               severity=item['canonical']['severity'], file=display_text(item['canonical']['file']),
                               line=item['canonical']['line']) for item in new[:10]])


def reviewer_preview(payload):
    """Small raw-output glimpse; this is not evidence validation or consolidation."""
    findings = payload.get('findings', []) if isinstance(payload, dict) else []
    if not isinstance(findings, list):
        findings = []
    rows, rejected = [], 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        check = finding.get('self_check')
        if isinstance(check, dict) and check.get('verdict') == 'rejected':
            rejected += 1
            continue
        if (finding.get('severity') not in ('high', 'medium', 'low', 'nit')
                or type(finding.get('line')) is not int or not 1 <= finding['line'] <= 10**9
                or any(not isinstance(finding.get(k), str) or not finding[k] for k in ('title', 'file'))):
            continue
        if len(rows) < 3:
            rows.append(dict(title=display_text(finding['title']), severity=finding['severity'],
                             file=display_text(finding['file']), line=finding['line']))
    return dict(raised=len(findings), self_rejected=rejected, findings=rows)
