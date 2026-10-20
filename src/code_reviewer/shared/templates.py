"""Generic prompt rendering and delimiter escaping; asset loading belongs to each flow."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


_IF = re.compile(r"\{\{#if (\w+)\}\}\n?(.*?)\{\{/if\}\}\n?", re.S)
_VAR = re.compile(r"\{\{(\w+)\}\}")
_BLANKS = re.compile(r"\n{3,}")

_CLOSING_TAGS = re.compile(
    r"</\s*(untrusted_pr_metadata|diff|review_criteria|agents_md|changed_files|excluded_files"
    r"|new_findings|candidate_findings|candidate_paths)\s*>",
    re.I,
)

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
