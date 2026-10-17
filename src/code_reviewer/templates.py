"""Prompt template loading and rendering."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .config import PROMPTS_DIR

_IF = re.compile(r"\{\{#if (\w+)\}\}\n?(.*?)\{\{/if\}\}\n?", re.S)
_VAR = re.compile(r"\{\{(\w+)\}\}")
_BLANKS = re.compile(r"\n{3,}")

_CLOSING_TAGS = re.compile(
    r"</\s*(untrusted_pr_metadata|diff|review_criteria|agents_md|changed_files|excluded_files"
    r"|new_findings|candidate_findings|candidate_paths)\s*>",
    re.I,
)

ROLES = ("resolver", "reviewer", "comparator")


def render(template: str, variables: Mapping[str, Any], flags: Optional[Mapping[str, bool]] = None) -> str:
    """Render a template. Values are inserted verbatim and never re-expanded."""
    flags = flags or {}

    def conditional(match: "re.Match[str]") -> str:
        return match.group(2) if flags.get(match.group(1)) else ""

    text = _IF.sub(conditional, template)
    text = _BLANKS.sub("\n\n", text)

    def variable(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in variables:
            raise KeyError("missing template variable: %s" % key)
        return str(variables[key])

    return _VAR.sub(variable, text)


def escape_untrusted(text: str) -> str:
    """Neutralize closing delimiter tags inside untrusted text."""
    return _CLOSING_TAGS.sub("[removed]", text or "")


@dataclass
class PromptSet:
    role: str
    system: str
    user: str
    schema: Dict[str, Any]


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
