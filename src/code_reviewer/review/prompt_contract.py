"""Only this flow owns its role contracts and assets."""
from __future__ import annotations

import hashlib
import copy
import json
import re
from pathlib import Path
from typing import Any, Dict

from ..shared.templates import PromptSet, escape_untrusted

ROLES = ('resolver', 'classifier', 'reviewer', 'comparator', 'verifier')
_EXTRA_TAGS = re.compile(r'</\s*(project_rules|persona_definitions|conflict_claims)\s*>', re.I)


def protect(text: str) -> str:
    return _EXTRA_TAGS.sub('[removed]', escape_untrusted(text))


def json_data(value: Any) -> str:
    return protect(json.dumps(value, ensure_ascii=False, indent=2))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def load_prompts(directory: Path) -> Dict[str, PromptSet]:
    def schema(role):
        value = json.loads((directory / 'schemas' / (role + '.schema.json')).read_text(encoding='utf-8'))
        ref = value.get('$ref', '')
        if ref.startswith('evidence.schema.json#/$defs/'):
            definitions = json.loads((directory / 'schemas/evidence.schema.json').read_text(encoding='utf-8'))['$defs']
            value = copy.deepcopy(definitions[ref.rsplit('/', 1)[1]])
            needed = {}
            def collect(node):
                if isinstance(node, dict):
                    if '$ref' in node:
                        key = node['$ref'].rsplit('/', 1)[1]
                        if key not in needed:
                            needed[key] = copy.deepcopy(definitions[key])
                            collect(needed[key])
                    for child in node.values():
                        collect(child)
                elif isinstance(node, list):
                    for child in node:
                        collect(child)
            collect(value)
            value['$defs'] = needed
        return value
    return {r: PromptSet(r, (directory / (r + '.system.md')).read_text(encoding='utf-8'),
                         (directory / (r + '.user.md')).read_text(encoding='utf-8'),
                         schema(r))
            for r in ROLES}
