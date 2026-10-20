"""The single-agent flow's prompt/schema contract and hashes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict

from ...config import PROMPTS_DIR
from ...shared.templates import PromptSet

ROLES = ("resolver", "reviewer", "comparator")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_prompt(role: str, prompts_dir: Path = PROMPTS_DIR) -> PromptSet:
    if role not in ROLES:
        raise ValueError("unknown role: %s" % role)
    system = (prompts_dir / ("%s.system.md" % role)).read_text(encoding="utf-8")
    user = (prompts_dir / ("%s.user.md" % role)).read_text(encoding="utf-8")
    schema = json.loads((prompts_dir / "schemas" / ("%s.schema.json" % role)).read_text(encoding="utf-8"))
    return PromptSet(role=role, system=system, user=user, schema=schema)


def prompt_hashes(prompts_dir: Path = PROMPTS_DIR) -> Dict[str, str]:
    hashes: Dict[str, str] = {}
    for role in ROLES:
        for name in ("%s.system.md" % role, "%s.user.md" % role, "schemas/%s.schema.json" % role):
            hashes[name] = _sha256(prompts_dir / name)
    return hashes
