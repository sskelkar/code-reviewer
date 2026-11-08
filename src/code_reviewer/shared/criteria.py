"""Review criteria: baseline rules, explicit project review guidance, rule IDs and prompt rendering."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from .templates import escape_untrusted

REGULAR_MODES = ("100644", "100755")
DOC_EXTENSIONS = (".md", ".mdx", ".rst", ".txt", ".adoc")
MAX_CANDIDATES = 200

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_DEFAULT_ID = re.compile(r"^\[([A-Za-z][A-Za-z0-9]*-\d+)\]\s*(.*)$")
_URL = re.compile(r"https?://[^\s)>\]\"'`]+")
_TOKEN_SPLIT = re.compile(r"[\s()\[\]{}<>,;\"'`]+")
_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


@dataclass
class Rule:
    rule_id: str
    source: str  # "default" or "project"
    path: str
    heading: str
    text: str


@dataclass
class Section:
    level: int
    heading: str
    body: str


@dataclass
class ProjectCriteria:
    rules: List[Rule] = field(default_factory=list)
    manifest: Dict[str, Any] = field(default_factory=dict)
    files_used: List[str] = field(default_factory=list)
    ignored: List[Dict[str, str]] = field(default_factory=list)
    auto_included: List[Dict[str, str]] = field(default_factory=list)  # files added because they cover the changed paths
    notes: str = ""
    warnings: List[str] = field(default_factory=list)
    source: str = "none"  # "auto" for deterministic discovery, otherwise "none"


@dataclass
class CriteriaSet:
    rules: List[Rule]
    project: ProjectCriteria
    warnings: List[str] = field(default_factory=list)
    changed_by_pr: List[str] = field(default_factory=list)

    def index(self) -> Dict[str, Dict[str, str]]:
        return {
            r.rule_id: {"source": r.source, "path": r.path, "heading": r.heading, "text": r.text}
            for r in self.rules
        }


def slugify(heading: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", heading.lower(), flags=re.U)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug or "section"


def split_sections(text: str) -> Tuple[str, List[Section]]:
    """Split markdown into (preamble, sections). Headings inside code fences are ignored."""
    preamble: List[str] = []
    sections: List[Section] = []
    current: Optional[Section] = None
    buffer: List[str] = []
    in_fence = False
    fence_marker = ""

    def flush() -> None:
        body = "\n".join(buffer).strip("\n")
        if current is None:
            preamble.append(body)
        else:
            current.body = body

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence = False
        match = None if in_fence else _HEADING.match(line)
        if match:
            flush()
            buffer = []
            current = Section(len(match.group(1)), match.group(2).strip(), "")
            sections.append(current)
        else:
            buffer.append(line)
    flush()
    return "\n".join(p for p in preamble if p).strip(), sections


def load_default_rules(directory: Path, disabled: Sequence[str] = ()) -> List[Rule]:
    rules: List[Rule] = []
    disabled_set = {d.strip() for d in disabled}
    if not directory.is_dir():
        return rules
    for path in sorted(directory.glob("*.md")):
        _, sections = split_sections(path.read_text(encoding="utf-8"))
        for section in sections:
            match = _DEFAULT_ID.match(section.heading)
            if not match or not section.body.strip():
                continue
            rule_id = match.group(1).upper()
            if rule_id in disabled_set:
                continue
            rules.append(Rule(rule_id, "default", path.name, match.group(2).strip() or rule_id, section.body.strip()))
    return rules


def rules_from_markdown(path: str, text: str) -> List[Rule]:
    """One rule per heading section; a file without headings becomes a single rule."""
    preamble, sections = split_sections(text)
    if not sections:
        body = text.strip()
        return [Rule("P:%s" % path, "project", path, Path(path).name, body)] if body else []
    rules: List[Rule] = []
    seen: Dict[str, int] = {}
    if preamble:
        rules.append(Rule("P:%s#introduction" % path, "project", path, "(introduction)", preamble))
        seen["introduction"] = 1
    for section in sections:
        if not section.body.strip():
            continue
        slug = slugify(section.heading)
        count = seen.get(slug, 0) + 1
        seen[slug] = count
        slug_id = slug if count == 1 else "%s-%d" % (slug, count)
        rules.append(Rule("P:%s#%s" % (path, slug_id), "project", path, section.heading, section.body.strip()))
    return rules


def _inline_rule(path: str, sections: List[Section], index: int) -> Optional[Rule]:
    base = sections[index]
    parts = [base.body.strip()] if base.body.strip() else []
    for later in sections[index + 1:]:
        if later.level <= base.level:
            break
        parts.append("%s %s\n%s" % ("#" * later.level, later.heading, later.body.strip()))
    text = "\n\n".join(p for p in parts if p).strip()
    if not text:
        return None
    return Rule("P:%s#%s" % (path, slugify(base.heading)), "project", path, base.heading, text)


def glob_to_regex(pattern: str) -> "re.Pattern[str]":
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                if pattern[i:i + 1] == "/":
                    i += 1
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def split_front_matter(text: str) -> Tuple[List[str], str]:
    """Split a leading `---` block off a markdown file. Returns (the globs of its `paths:` key, the text without the block).
    A file without front matter, or without a `paths:` key, has no globs (it applies to every file)."""
    match = _FRONT_MATTER.match(text)
    if not match:
        return [], text
    globs: List[str] = []
    collecting = False
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if collecting and stripped.startswith("-"):
            globs.append(stripped[1:].strip().strip("\"'"))
            continue
        collecting = False
        if stripped.startswith("paths:"):
            value = stripped[len("paths:"):].strip()
            if value:
                globs += [g.strip().strip("\"'") for g in value.strip("[]").split(",")]
            else:
                collecting = True
    return [g for g in globs if g], text[match.end():]


def _directories_of(paths: Sequence[str]) -> List[str]:
    """Every directory (not the root) that contains one of the paths, itself or as an ancestor."""
    found: Set[str] = set()
    for path in paths:
        parts = path.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            found.add("/".join(parts[:depth]))
    return sorted(found)


def extract_candidates(agents_text: str, tree: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """Find in-repo files referenced from AGENTS.md. Returns (candidate paths, external references)."""
    external: List[str] = []
    for url in _URL.findall(agents_text):
        url = url.rstrip(".,:;!?")
        if url not in external:
            external.append(url)
    stripped = _URL.sub(" ", agents_text)

    regular = {p for p, mode in tree.items() if mode in REGULAR_MODES and p != "AGENTS.md"}
    found: List[str] = []

    def add(paths: Sequence[str]) -> None:
        for p in paths:
            if p not in found and len(found) < MAX_CANDIDATES:
                found.append(p)

    for token in _TOKEN_SPLIT.split(stripped):
        token = token.rstrip(".:!?").split("#")[0]
        if not token:
            continue
        if token.startswith("/") or token.startswith("~"):
            if "/" in token[1:]:
                ref = "absolute path: %s" % token
                if ref not in external:
                    external.append(ref)
            continue
        if token.startswith("./"):
            token = token[2:]
        if ".." in token.split("/"):
            continue
        has_glob = "*" in token
        if not ("/" in token or has_glob or token.lower().endswith(DOC_EXTENSIONS)):
            continue
        if has_glob:
            regex = glob_to_regex(token)
            add(sorted(p for p in regular if regex.match(p)))
        elif token.endswith("/") or any(p.startswith(token + "/") for p in regular):
            prefix = token if token.endswith("/") else token + "/"
            add(sorted(p for p in regular if p.startswith(prefix) and p.lower().endswith(DOC_EXTENSIONS)))
        elif token in regular:
            add([token])
    return sorted(found), external


def resolve_project(tree: Dict[str, str], show: Callable[[str], Optional[str]],
                    max_chars: int, changed_paths: Sequence[str] = ()) -> ProjectCriteria:
    """Load explicit base-commit review instructions without inference or execution."""
    from .review_guidance import discover
    pc = ProjectCriteria(source="auto")
    pc.rules = _apply_size_cap(discover(tree, show, changed_paths, pc, max_chars), max_chars, pc)
    if not pc.rules:
        pc.warnings.append("no explicit project code-review instructions loaded; using enabled baseline criteria")
    return pc


def _apply_size_cap(rules: List[Rule], max_chars: int, pc: ProjectCriteria) -> List[Rule]:
    kept: List[Rule] = []
    total = 0
    for rule in rules:
        size = len(rule.text) + len(rule.heading)
        if total + size <= max_chars:
            kept.append(rule)
            total += size
    if len(kept) < len(rules):
        pc.warnings.append("%d project rule(s) dropped: criteria size cap of %d characters reached" % (len(rules) - len(kept), max_chars))
    return kept


def assemble(defaults: List[Rule], project: ProjectCriteria, changed_paths: Sequence[str]) -> CriteriaSet:
    """Use explicit project criteria when available, otherwise the enabled baseline."""
    warnings = list(project.warnings)
    rules = list(project.rules or defaults)
    touched = sorted(set(project.files_used + ["AGENTS.md"]) & set(changed_paths))
    if touched:
        warnings.append("this PR modifies criteria files (read from the base branch instead): %s" % ", ".join(touched))
    return CriteriaSet(rules=rules, project=project, warnings=warnings, changed_by_pr=touched)


def render_criteria_block(rules: Sequence[Rule]) -> str:
    if not rules:
        return "(no criteria available: review for correctness and security only and use rule_id \"none\")"
    blocks = []
    for rule in rules:
        blocks.append("### [%s] (%s) %s\n%s" % (
            rule.rule_id, rule.source, escape_untrusted(rule.heading), escape_untrusted(rule.text)))
    return "\n\n".join(blocks)
