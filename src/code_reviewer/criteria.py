"""Review criteria: default rules, project rules discovered through AGENTS.md, rule IDs and prompt rendering."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from .templates import escape_untrusted

REGULAR_MODES = ("100644", "100755")
DOC_EXTENSIONS = (".md", ".mdx", ".rst", ".txt", ".adoc")
MAX_CANDIDATES = 200
RULES_DIR = ".claude/rules/"
NESTED_AGENTS = "AGENTS.md"

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
    source: str = "none"  # "resolver", "override", "auto" (no resolver call was needed) or "none"


@dataclass
class CriteriaSet:
    rules: List[Rule]
    project: ProjectCriteria
    mode: str
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


def scoped_files(tree: Dict[str, str], changed_paths: Sequence[str],
                 show: Callable[[str], Optional[str]]) -> List[Tuple[str, str, List[Rule]]]:
    """Criteria files that cover the changed paths, found by location instead of by the AGENTS.md links:
    an AGENTS.md in a directory that contains changed files (or one of its ancestors), and every `.claude/rules/*.md`
    file that is not scoped by a `paths:` key or whose `paths:` globs match a changed file. Returns (path, why, rules)."""
    regular = {p for p, mode in tree.items() if mode in REGULAR_MODES}
    found: List[Tuple[str, str, List[Rule]]] = []
    candidates: List[Tuple[str, str]] = []
    for directory in _directories_of(changed_paths):
        path = "%s/%s" % (directory, NESTED_AGENTS)
        if path in regular:
            candidates.append((path, "AGENTS.md in a directory that contains changed files (%s/)" % directory))
    for path in sorted(p for p in regular if p.startswith(RULES_DIR) and p.lower().endswith(".md")):
        candidates.append((path, ""))
    for path, why in candidates:
        text = show(path)
        if text is None:
            continue
        globs, body = split_front_matter(text)
        if path.startswith(RULES_DIR):
            if globs:
                regexes = [glob_to_regex(g) for g in globs]
                if not any(r.match(c) for r in regexes for c in changed_paths):
                    continue
                why = "rule file scoped to %s, which matches changed files" % ", ".join(globs)
            else:
                why = "rule file that applies to all files"
        rules = rules_from_markdown(path, body)
        if rules:
            found.append((path, why, rules))
    return found


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


def _clean_ignored(items: Any) -> List[Dict[str, str]]:
    cleaned: List[Dict[str, str]] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                cleaned.append({"reference": str(item.get("reference", "")), "reason": str(item.get("reason", ""))})
    return cleaned


def resolve_project(
    agents_text: Optional[str],
    tree: Dict[str, str],
    show: Callable[[str], Optional[str]],
    call_resolver: Callable[[List[str], str], Dict[str, Any]],
    manifest_override: Optional[Dict[str, Any]],
    max_chars: int,
    changed_paths: Sequence[str] = (),
) -> ProjectCriteria:
    """Resolve project criteria from the base commit. The LLM only proposes; this function validates.

    Files that cover the changed paths (nested AGENTS.md files, `.claude/rules`) are added without asking the model."""
    pc = ProjectCriteria()
    scoped = scoped_files(tree, changed_paths, show)
    if manifest_override is None and not agents_text:
        if not scoped:
            pc.warnings.append("no AGENTS.md at the base commit; using default criteria only")
            return pc
        pc.source = "auto"
        pc.rules = _apply_size_cap(_add_scoped(scoped, set(), pc), max_chars, pc)
        return pc

    candidates: List[str] = []
    if agents_text:
        candidates, external = extract_candidates(agents_text, tree)
        for ref in external:
            pc.ignored.append({"reference": ref, "reason": "external reference (not in the repository)"})

    if manifest_override is not None:
        manifest, pc.source = manifest_override, "override"
        allowed = {p for p, mode in tree.items() if mode in REGULAR_MODES}
    else:
        try:
            manifest = call_resolver(candidates, agents_text or "")
        except Exception as exc:  # the review continues with defaults only
            pc.warnings.append("criteria resolver failed (%s); using %s" % (exc, "only the files that cover the changed paths" if scoped else "default criteria only"))
            pc.rules = _apply_size_cap(_add_scoped(scoped, set(), pc), max_chars, pc)
            return pc
        pc.source = "resolver"
        allowed = set(candidates)
    pc.manifest = manifest
    pc.notes = str(manifest.get("organization_notes", "") or "")
    pc.ignored.extend(_clean_ignored(manifest.get("ignored_references")))

    rules = _rules_from_files(manifest, allowed, tree, show, pc) + _rules_from_inline_sections(manifest, agents_text, pc)
    rules = _add_scoped(scoped, set(pc.files_used), pc) + rules
    pc.rules = _apply_size_cap(rules, max_chars, pc)
    if not pc.rules:
        pc.warnings.append("the project's criteria resolved to nothing; using default criteria only")
    return pc


def _add_scoped(scoped: List[Tuple[str, str, List[Rule]]], already: Set[str], pc: ProjectCriteria) -> List[Rule]:
    """Rules of the files that cover the changed paths, except those the resolver already chose. Records why each was added."""
    rules: List[Rule] = []
    for path, why, file_rules in scoped:
        if path in already:
            continue
        rules.extend(file_rules)
        pc.files_used.append(path)
        pc.auto_included.append({"path": path, "reason": why})
    return rules


def _rules_from_files(manifest: Dict[str, Any], allowed: Set[str], tree: Dict[str, str],
                      show: Callable[[str], Optional[str]], pc: ProjectCriteria) -> List[Rule]:
    """Rules from the files the resolver named. Anything outside the allowed set is dropped and reported."""
    rules: List[Rule] = []
    for item in manifest.get("criteria_files") or []:
        path = str(item.get("path", "")) if isinstance(item, dict) else ""
        if path not in allowed or tree.get(path) not in REGULAR_MODES:
            pc.ignored.append({"reference": path, "reason": "not a regular in-repo file from the candidate list"})
            continue
        text = show(path)
        if text is None:
            pc.ignored.append({"reference": path, "reason": "could not be read at the base commit"})
            continue
        file_rules = rules_from_markdown(path, text)
        if file_rules:
            rules.extend(file_rules)
            pc.files_used.append(path)
    return rules


def _rules_from_inline_sections(manifest: Dict[str, Any], agents_text: Optional[str], pc: ProjectCriteria) -> List[Rule]:
    """Rules from sections of AGENTS.md itself that the resolver identified as criteria."""
    wanted = [str(i.get("heading", "")).strip().lower() for i in manifest.get("inline_sections") or [] if isinstance(i, dict)]
    if not wanted or not agents_text:
        return []
    _, sections = split_sections(agents_text)
    by_heading = {s.heading.strip().lower(): idx for idx, s in enumerate(sections)}
    rules: List[Rule] = []
    for heading in wanted:
        idx = by_heading.get(heading)
        rule = _inline_rule("AGENTS.md", sections, idx) if idx is not None else None
        if rule is None:
            pc.ignored.append({"reference": "AGENTS.md#%s" % heading, "reason": "inline section not found or empty"})
        else:
            rules.append(rule)
    if rules and "AGENTS.md" not in pc.files_used:
        pc.files_used.append("AGENTS.md")
    return rules


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


def assemble(defaults: List[Rule], project: ProjectCriteria, mode: str, changed_paths: Sequence[str]) -> CriteriaSet:
    warnings = list(project.warnings)
    if mode == "defaults_only":
        rules = list(defaults)
    elif mode == "project_only":
        rules = list(project.rules)
        if not rules:
            warnings.append("criteria_mode is project_only but no project criteria were found")
    else:
        rules = list(project.rules) + list(defaults)
    touched = sorted(set(project.files_used + ["AGENTS.md"]) & set(changed_paths))
    if touched:
        warnings.append("this PR modifies criteria files (read from the base branch instead): %s" % ", ".join(touched))
    return CriteriaSet(rules=rules, project=project, mode=mode, warnings=warnings, changed_by_pr=touched)


def render_criteria_block(rules: Sequence[Rule]) -> str:
    if not rules:
        return "(no criteria available: review for correctness and security only and use rule_id \"none\")"
    blocks = []
    for rule in rules:
        blocks.append("### [%s] (%s) %s\n%s" % (
            rule.rule_id, rule.source, escape_untrusted(rule.heading), escape_untrusted(rule.text)))
    return "\n\n".join(blocks)
