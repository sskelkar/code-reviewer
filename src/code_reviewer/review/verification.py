"""Independent candidate investigation, conflict grouping and one publication allowlist."""
from __future__ import annotations

import copy

from .evidence import evidence_errors, shape_errors


def groups(master):
    """Connected conflict groups plus singleton candidates; each candidate exactly once."""
    remaining = {item['id'] for item in master.scored()}
    edges = [set(group['findings']) & remaining for group in master.conflicts.values()]
    jobs = []
    while remaining:
        connected = {min(remaining)}
        while True:
            expanded = connected | set().union(*(edge for edge in edges if edge & connected))
            if expanded == connected:
                break
            connected = expanded
        remaining -= connected
        jobs.append(sorted(connected))
    return jobs


def candidate_inputs(master, ids):
    # Keep the argument intact, without counts, persona identities or self-check votes.
    return [{'id': ident, 'candidate': {k: copy.deepcopy(v) for k, v in master.items[ident]['canonical'].items()
                                      if k != 'self_check'}} for ident in ids]


def validate_assessments(payload, schema, ids, master, cache, criteria):
    errors = shape_errors(payload, schema)
    if errors:
        return errors
    claims = payload['assessments']
    actual = [claim['candidate_id'] for claim in claims]
    if len(actual) != len(set(actual)) or set(actual) != set(ids):
        return ['assess every requested candidate exactly once; no extra IDs']
    relation = payload['relationship']
    if (len(ids) == 1) != (relation == 'independent'):
        return ['singleton investigations require independent; conflict groups require a group relationship']
    if relation == 'contradictory' and sum(c['status'] == 'supported' for c in claims) > 1:
        return ['contradictory claims cannot both be supported under the same conditions']
    if relation == 'different_conditions':
        supported = [master.items[c['candidate_id']]['canonical']['conditions'] for c in claims if c['status'] == 'supported']
        if any(not conditions for conditions in supported) or len({tuple(c) for c in supported}) != len(supported):
            return ['different_conditions requires explicit distinct conditions in the original supported claims']
    for claim in claims:
        candidate = master.items[claim['candidate_id']]['canonical']
        errors = evidence_errors(claim['additional_evidence'], 'V', cache, criteria)
        if errors:
            return errors
        available = {'primary'} | {e['id'] for e in candidate.get('evidence', []) + claim['additional_evidence']}
        if not set(claim['evidence_refs']) <= available or not set(claim['remedy']['evidence_refs']) <= available:
            return ['unknown or cross-candidate evidence reference']
        if claim['status'] != 'unresolved' and not claim['evidence_refs']:
            return ['supported/refuted claims require grounded evidence references']
        if claim['status'] == 'supported' and (candidate['assumptions'] or claim['remaining_uncertainties']):
            return ['material assumptions or remaining uncertainties require unresolved status']
        if claim['status'] == 'supported' and candidate['kind'] == 'absent' and not (candidate['evidence'] or claim['additional_evidence']):
            return ['an absence anchor alone cannot support a missing-behavior claim']
        remedy = claim['remedy']
        if remedy['status'] == 'not_assessed':
            if remedy['explanation'] is not None or remedy['evidence_refs']:
                return ['unassessed remedies require null explanation and empty references']
        elif not remedy['explanation'] or (remedy['status'] in ('supported', 'refuted') and not remedy['evidence_refs']):
            return ['assessed remedies require explanation and supported/refuted remedies require evidence']
        if not candidate.get('suggestion') and remedy['status'] != 'not_assessed':
            return ['a missing suggestion cannot receive a remedy assessment']
    return []


def publishable(item):
    assessment = item.get('verification')
    return bool(item['seen_in_passes'] and assessment and assessment['status'] == 'supported'
                and item.get('verification_relationship') not in (None, 'unresolved'))


def counts(master):
    result = dict(candidates=0, supported=0, refuted=0, unresolved=0, pending=0, published=0)
    for item in master.scored():
        result['candidates'] += 1
        assessment = item.get('verification')
        result[assessment['status'] if assessment else 'pending'] += 1
        result['published'] += publishable(item)
    return result


def artifact(result):
    """Private audit and supported-only automation view, with pinned identities."""
    findings = []
    for item in result.master.scored():
        if publishable(item):
            candidate = copy.deepcopy(item['canonical'])
            if item['verification']['remedy']['status'] != 'supported':
                candidate['suggestion'] = None
            findings.append(dict(id=item['id'], candidate=candidate, categories=item['categories'],
                                 verification=copy.deepcopy(item['verification'])))
    return dict(version=1, head_sha=result.ctx.head_sha, base_sha=result.ctx.base_sha,
                merge_base=result.ctx.merge_base, stop_reason=result.stop_reason,
                verification=counts(result.master), criteria=result.criteria.index(),
                findings=findings, audit=result.master.to_dict())
