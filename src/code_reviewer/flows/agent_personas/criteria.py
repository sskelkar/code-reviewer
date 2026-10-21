"""Static defaults and coverage-checked distribution of accepted project rules."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

from ...config import Config, ConfigError
from ...shared.criteria import Rule, load_default_rules

PERSONAS = ('correctness', 'code-quality', 'test-quality', 'production-safety')
LEGACY_IDS = dict(zip(('GEN-%03d' % n for n in range(1, 11)), (
    'COR-001', 'CQ-001', 'CQ-002', 'SAFE-001', 'SAFE-002', 'SAFE-003', 'SAFE-004', 'COR-002', 'TEST-001', 'TEST-002')))


class Catalog:
    def __init__(self, root: Path, defaults_dir: Path):
        try:
            data = json.loads((root / 'personas.json').read_text(encoding='utf-8'))
            if data['common_instructions'] != 'review-standard.md':
                raise ValueError('unknown common instruction asset')
            self.personas = data['personas']
            self.standard = (root / 'review-standard.md').read_text(encoding='utf-8')
            self.rules = load_default_rules(defaults_dir)
            ids = {r.rule_id for r in self.rules}
            if data['version'] != 1 or tuple(p['id'] for p in self.personas) != PERSONAS:
                raise ValueError('unknown catalog version or personas')
            if len(ids) != len(self.rules):
                raise ValueError('duplicate canonical default IDs')
            assigned = set()
            for p in self.personas:
                rules = p['default_rule_ids']
                if not p['name'] or not p['focus'] or not rules or len(rules) != len(set(rules)):
                    raise ValueError('invalid persona definition')
                assigned.update(rules)
            if assigned != ids:
                raise ValueError('static default allocation must cover exactly the canonical rules')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ConfigError('invalid persona assets: %s' % exc) from exc

    def input_config(self, cfg: Config) -> Config:
        disabled = [LEGACY_IDS.get(r.strip(), r.strip()) for r in cfg.disabled_default_rules]
        unknown = set(disabled) - {r.rule_id for r in self.rules}
        if unknown:
            raise ConfigError('unknown disabled persona defaults: %s' % ', '.join(sorted(unknown)))
        return replace(cfg, disabled_default_rules=disabled)

    def allocate(self, rules: List[Rule], project_routes: Dict[str, List[str]]) -> Dict[str, List[Rule]]:
        defaults = {p['id']: set(p['default_rule_ids']) for p in self.personas}
        return {p: [r for r in rules if (r.source == 'default' and r.rule_id in defaults[p]) or
                    (r.source == 'project' and p in project_routes[r.rule_id])] for p in PERSONAS}


def routes(payload: Any, project_ids: List[str]) -> Dict[str, List[str]]:
    """Keep only unambiguous known assignments; missing/invalid assignments will fall back visibly."""
    result: Dict[str, List[str]] = {}
    seen = set()
    if not isinstance(payload, dict) or not isinstance(payload.get('assignments'), list):
        return result
    for entry in payload['assignments']:
        if not isinstance(entry, dict):
            continue
        key, values = entry.get('rule_id'), entry.get('personas')
        if not isinstance(key, str) or key not in project_ids:
            continue
        if key in seen:
            result.pop(key, None)
            continue
        seen.add(key)
        if isinstance(values, list) and values and all(isinstance(v, str) and v in PERSONAS for v in values) and len(set(values)) == len(values):
            result[key] = list(values)
    return result


def validate_routes(payload: Any, project_ids: List[str]) -> List[str]:
    valid = routes(payload, project_ids)
    entries = payload.get('assignments') if isinstance(payload, dict) else None
    if not isinstance(entries, list) or len(entries) != len(project_ids) or set(valid) != set(project_ids):
        return ['assign every known project rule exactly once to one or more allowed personas; no unknown IDs']
    if any(not isinstance(e.get('reason'), str) or not e['reason'].strip() for e in entries):
        return ['every assignment requires a reason']
    return []
