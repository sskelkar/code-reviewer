"""Validate model references and evidence before any persona state is changed."""
from __future__ import annotations

from typing import Any, List

from ...shared.findings import FileCache, find_quote
from .criteria import PERSONAS


def categories(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) and v in PERSONAS for v in value) and len(set(value)) == len(value)


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_comparison(payload: Any, findings: Any, master: Any) -> List[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get('decisions'), list) or not isinstance(payload.get('conflicts'), list):
        return ['comparison requires decisions and conflicts lists']
    entries = payload['decisions']
    order = [f['local_id'] for f in findings]
    if len(entries) != len(order) or any(not isinstance(e, dict) for e in entries) or [e.get('local_id') for e in entries] != order:
        return ['one decision per incoming local_id, in input order, is required']
    resolved = {}
    actionable = {k for k, i in master.items.items() if i['seen_in_passes']}
    for finding, entry in zip(findings, entries):
        match, same = entry.get('match'), entry.get('same_as')
        if not isinstance(match, str) or (match != 'new' and match not in master.items) or not categories(entry.get('categories')):
            return ['invalid match or category']
        if same is not None and (not isinstance(same, str) or match != 'new' or same not in resolved):
            return ['same_as must reference an earlier incoming finding with match=new']
        ident = match if match != 'new' else resolved[same] if same else 'new:' + finding['local_id']
        resolved[finding['local_id']] = ident
        if finding['verdict'] == 'confirmed':
            actionable.add(ident)
    for flag in payload['conflicts']:
        if not isinstance(flag, dict) or not nonempty(flag.get('reason')) or not isinstance(flag.get('findings'), list):
            return ['invalid conflict group']
        refs = flag['findings']
        if any(not isinstance(r, str) or (r not in resolved and r not in master.items) for r in refs):
            return ['unknown conflict reference']
        ids = {resolved.get(r, r) for r in refs}
        if len(ids) < 2 or len(refs) != len(set(refs)) or not ids <= actionable:
            return ['conflicts require distinct actionable canonical claims; do not merge opposing claims']
    return []


def validate_annotation(payload: Any, group: Any, cache: FileCache) -> List[str]:
    if not isinstance(payload, dict) or payload.get('outcome') not in ('resolved', 'conditional', 'tradeoff', 'unresolved') or not nonempty(payload.get('explanation')):
        return ['invalid verification outcome or explanation']
    claims = payload.get('claims')
    if not isinstance(claims, list) or any(not isinstance(c, dict) for c in claims):
        return ['claims must be objects']
    ids = [c.get('finding_id') for c in claims]
    if any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)) or set(ids) != set(group['findings']):
        return ['annotate every flagged finding exactly once; no unknown IDs']
    for claim in claims:
        status, evidence = claim.get('status'), claim.get('evidence')
        if status not in ('supported', 'refuted', 'conditional', 'unresolved') or not nonempty(claim.get('explanation')) or not categories(claim.get('categories')):
            return ['invalid claim annotation']
        if not isinstance(evidence, list) or (status != 'unresolved' and not evidence):
            return ['non-unresolved claims require grounded evidence']
        for entry in evidence:
            if not isinstance(entry, dict) or not nonempty(entry.get('file')) or not nonempty(entry.get('quote')):
                return ['invalid verification evidence']
            line = entry.get('line')
            if not isinstance(line, int) or isinstance(line, bool) or line < 1:
                return ['evidence requires a positive line number']
            lines = cache.lines(entry['file'])
            if lines is None or line not in find_quote(lines, entry['quote']):
                return ['verification quote/line is not grounded inside the checkout']
        if payload['outcome'] == 'resolved' and status not in ('supported', 'refuted'):
            return ['resolved groups cannot contain unresolved or conditional claims']
    return []
