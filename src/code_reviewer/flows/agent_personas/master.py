"""One master list with source sightings, subject categories and canonical conflict groups."""
from __future__ import annotations

import copy
from typing import Any, Dict, List

from ...shared.findings import SEVERITY_RANK

FIELDS = ('title', 'description', 'severity', 'kind', 'rule_id', 'file', 'line', 'quote', 'anchor', 'expected', 'suggestion')


class MasterList:
    def __init__(self):
        self.items: Dict[str, Any] = {}
        self.conflicts: Dict[str, Any] = {}

    def scored(self) -> List[Any]:
        return [i for i in self.items.values() if i['seen_in_passes']]

    def rejected_only(self) -> List[Any]:
        return [i for i in self.items.values() if not i['seen_in_passes']]

    def apply(self, slot: Any, findings: List[Any], proposal: Any) -> Dict[str, str]:
        assigned: Dict[str, str] = {}
        for finding, decision in zip(findings, proposal['decisions']):
            ident = decision['match']
            if ident == 'new':
                ident = assigned.get(decision.get('same_as'))
            if ident is None or ident == 'new':
                ident = 'F-%03d' % (len(self.items) + 1)
                self.items[ident] = {
                    'id': ident, 'canonical': {k: copy.deepcopy(finding.get(k)) for k in FIELDS},
                    'seen_in_passes': [], 'rejected_in_passes': [], 'sightings': {}, 'rejected_sightings': {},
                    'categories': [], 'variants': [], 'canonical_from_rejected': finding['verdict'] != 'confirmed',
                }
            item = self.items[ident]
            confirmed = finding['verdict'] == 'confirmed'
            if confirmed and item['canonical_from_rejected']:
                item['canonical'] = {k: copy.deepcopy(finding.get(k)) for k in FIELDS}
                item['canonical_from_rejected'] = False
            if confirmed and SEVERITY_RANK[finding['severity']] > SEVERITY_RANK[item['canonical']['severity']]:
                item['canonical']['severity'] = finding['severity']
            key, sightings = ('seen_in_passes', 'sightings') if confirmed else ('rejected_in_passes', 'rejected_sightings')
            if slot.pass_no not in item[key]:
                item[key].append(slot.pass_no)
                item[sightings].setdefault(slot.persona, []).append(slot.pass_no)
            item['categories'] = sorted(set(item['categories']) | set(decision['categories']))
            item['variants'].append({'pass': slot.pass_no, 'persona': slot.persona, 'verdict': finding['verdict'],
                                     'finding': copy.deepcopy(finding)})
            assigned[finding['local_id']] = ident
        for flag in proposal['conflicts']:
            ids = sorted({assigned.get(ref, ref) for ref in flag['findings']})
            if any(c['findings'] == ids for c in self.conflicts.values()):
                continue
            key = 'C-%03d' % (len(self.conflicts) + 1)
            self.conflicts[key] = {'id': key, 'findings': ids, 'reason': flag['reason'], 'status': 'pending',
                                   'original_references': list(flag['findings']), 'annotation': None, 'error': ''}
        return assigned

    def to_dict(self) -> Dict[str, Any]:
        return {'findings': list(self.items.values()), 'conflicts': list(self.conflicts.values())}


def agreement(item: Any, completed: Any, cfg: Any) -> Dict[str, Any]:
    result = {}
    for persona, sightings in item['sightings'].items():
        denominator = completed[persona]
        value = len(sightings) / denominator if denominator else 0
        tier = 'Unrated' if denominator < 2 else 'High' if value >= cfg.tier_high else 'Medium' if value >= cfg.tier_medium else 'Low'
        result[persona] = {'seen': len(sightings), 'completed': denominator, 'tier': tier}
    return result
