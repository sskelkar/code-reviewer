"""Validation and deterministic grounding of Reviewer findings.

A finding is only trusted if its evidence can be found: an exact quote in the code (`present`, `cross_file`),
or an anchor over a changed hunk (`absent`). Findings that fail are set aside and counted, never merged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .diffview import DiffIndex, is_excluded

SEVERITIES = ("high", "medium", "low", "nit")
KINDS = ("present", "absent", "cross_file")
SEVERITY_RANK = {name: rank for rank, name in enumerate(reversed(SEVERITIES))}  # nit=0 ... high=3


@dataclass
class PreparedFindings:
    """One pass's findings, sorted by what the harness could verify."""

    ok: List[Dict[str, Any]] = field(default_factory=list)
    ungrounded: List[Dict[str, Any]] = field(default_factory=list)
    malformed: List[Dict[str, Any]] = field(default_factory=list)
    out_of_scope: List[Dict[str, Any]] = field(default_factory=list)  # findings located in excluded files
    self_check_missing: int = 0
    line_corrected: int = 0
    rule_repaired: int = 0  # rule citations that only needed reformatting, e.g. "[GEN-010]" -> "GEN-010"
    rule_unknown: int = 0   # rule citations that matched no rule: the finding is kept with rule "none"

    def all_records(self) -> List[Dict[str, Any]]:
        return self.ok + self.ungrounded + self.malformed + self.out_of_scope


# --- small helpers ---------------------------------------------------------------

def _norm(text: str) -> str:
    return " ".join(text.split())


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _rel(value: Any) -> str:
    path = _text(value)
    return path[2:] if path.startswith("./") else path


_CITATION_WRAPPERS = "[]()`'\" \t"


def resolve_rule_id(raw: str, rule_index: Dict[str, Any]) -> Tuple[str, str]:
    """Match a rule citation from the model to the criteria index.

    Returns (rule_id, outcome). The outcome is "none" (no rule cited), "exact", "repaired" (only the formatting was off: surrounding
    brackets or backticks, a missing `P:` prefix, different case, trailing text) or "unknown" (no rule matches; the rule is "none").
    """
    text = (raw or "").strip()
    if not text or text.lower() == "none":
        return "none", "none"
    if text in rule_index:
        return text, "exact"
    by_lower = {rule_id.lower(): rule_id for rule_id in rule_index}
    cleaned = text.strip(_CITATION_WRAPPERS)
    for candidate in (cleaned, cleaned.split()[0] if cleaned.split() else cleaned):
        candidate = candidate.strip(_CITATION_WRAPPERS).rstrip(".,;:")
        for variant in (candidate, "P:" + candidate):
            if variant in rule_index:
                return variant, "repaired"
            if variant.lower() in by_lower:
                return by_lower[variant.lower()], "repaired"
    return "none", "unknown"


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --- reading the checkout --------------------------------------------------------

def _safe_path(root: Path, rel: str) -> Optional[Path]:
    """Resolve `rel` inside `root`; None for absolute paths and traversal."""
    rel = rel.strip()
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return None
    try:
        candidate = (root / rel).resolve()
        root_resolved = root.resolve()
    except (OSError, ValueError):
        return None  # malformed model paths must fail grounding, not crash validation
    if candidate != root_resolved and root_resolved not in candidate.parents:
        return None
    return candidate


class FileCache:
    """Reads files from the checkout once and keeps their non-empty, whitespace-normalized lines."""

    def __init__(self, root: Path):
        self.root = root
        self._cache: Dict[str, Optional[List[Tuple[int, str]]]] = {}

    def lines(self, rel: str) -> Optional[List[Tuple[int, str]]]:
        if rel not in self._cache:
            path = _safe_path(self.root, rel)
            lines = None
            try:
                if path is not None and path.is_file():
                    text = path.read_text(encoding="utf-8", errors="replace")
                    lines = [(n, _norm(raw)) for n, raw in enumerate(text.splitlines(), start=1) if raw.strip()]
            except (OSError, ValueError):
                pass  # unreadable files are ungrounded evidence in either review flow
            self._cache[rel] = lines
        return self._cache[rel]


# --- grounding -------------------------------------------------------------------

def find_quote(file_lines: Sequence[Tuple[int, str]], quote: str) -> List[int]:
    """Line numbers where the quote starts. Each quote line only has to be a substring of the matching file line."""
    quote_lines = [_norm(q) for q in quote.splitlines() if q.strip()]
    if not quote_lines:
        return []
    return [
        file_lines[i][0]
        for i in range(len(file_lines) - len(quote_lines) + 1)
        if all(quote_lines[k] in file_lines[i + k][1] for k in range(len(quote_lines)))
    ]


def _quote_in_removed(removed: Sequence[str], quote: str) -> bool:
    removed_norm = [_norm(r) for r in removed]
    quote_lines = [_norm(q) for q in quote.splitlines() if q.strip()]
    return bool(quote_lines) and all(any(q in r for r in removed_norm) for q in quote_lines)


def _ground_quote(finding: Dict[str, Any], cache: FileCache, diff: DiffIndex) -> Tuple[bool, str, Optional[int]]:
    file_lines = cache.lines(finding["file"])
    if file_lines is not None:
        starts = find_quote(file_lines, finding["quote"])
        if starts:
            stated = finding.get("line") or starts[0]
            nearest = min(starts, key=lambda s: abs(s - stated))
            return True, "quote found at line %d" % nearest, nearest
    if _quote_in_removed(diff.removed_texts(finding["file"]), finding["quote"]):
        return True, "quote found in removed lines of the diff", None
    if file_lines is None:
        return False, "file not found in the checkout: %s" % finding["file"], None
    return False, "quote not found in %s" % finding["file"], None


def _ground_anchor(finding: Dict[str, Any], diff: DiffIndex) -> Tuple[bool, str, Optional[int]]:
    anchor = finding["anchor"]
    anchor_file = _text(anchor.get("file")) or finding["file"]
    start, end = _int(anchor.get("line_start")), _int(anchor.get("line_end"))
    if start is None or end is None or end < start:
        return False, "anchor has no valid line range", None
    if anchor_file not in diff.paths():
        return False, "anchor file is not part of the reviewed diff: %s" % anchor_file, None
    if any(start <= high and end >= low for low, high in diff.changed_ranges(anchor_file)):
        return True, "anchor overlaps a changed hunk", None
    return False, "anchor does not overlap any changed hunk in %s" % anchor_file, None


def ground(finding: Dict[str, Any], cache: FileCache, diff: DiffIndex) -> Tuple[bool, str, Optional[int]]:
    """Check a finding's evidence. Returns (grounded, reason, corrected_line)."""
    kind = finding["kind"]
    if kind in ("present", "cross_file") and finding.get("quote"):
        return _ground_quote(finding, cache, diff)
    if kind in ("absent", "cross_file") and isinstance(finding.get("anchor"), dict):
        return _ground_anchor(finding, diff)
    return False, "no usable evidence for kind %s" % kind, None


# --- preparing a pass's findings ---------------------------------------------------

class FindingsPreparer:
    """Normalizes Reviewer output and sorts each finding into ok / ungrounded / malformed / out of scope.

    A finding is only set aside when its evidence is missing or cannot be found. A badly written rule citation is repaired or
    reset to "none", never a reason to drop an otherwise grounded finding."""

    def __init__(self, rule_index: Dict[str, Any], self_check_on: bool, cache: FileCache, diff: DiffIndex, line_window: int,
                 excluded_paths: Sequence[str] = ()):
        self.excluded_paths = list(excluded_paths)
        self.rule_index = rule_index
        self.self_check_on = self_check_on
        self.cache = cache
        self.diff = diff
        self.line_window = line_window

    def prepare(self, raw_findings: Any, pass_no: int) -> PreparedFindings:
        prepared = PreparedFindings()
        if not isinstance(raw_findings, list):
            return prepared
        for position, raw in enumerate(raw_findings, start=1):
            local_id = "p%d-f%d" % (pass_no, position)  # IDs are assigned here, never trusted from the model
            if not isinstance(raw, dict):
                prepared.malformed.append({"local_id": local_id, "status": "malformed", "reason": "not an object", "raw": raw})
                continue
            finding = self._normalize(raw, local_id, pass_no)
            problem = self._shape_problem(finding)
            if problem:
                finding.update(status="malformed", reason=problem)
                prepared.malformed.append(finding)
                continue
            if self._is_excluded(finding):
                finding.update(status="out_of_scope", reason="the file is excluded from the review: findings are not reported on it")
                prepared.out_of_scope.append(finding)
                continue
            self._apply_self_check(finding, raw, prepared)
            self._sort(finding, prepared)
        return prepared

    def _is_excluded(self, finding: Dict[str, Any]) -> bool:
        """Is the finding located in a file that is excluded from the review (lockfiles, generated files, planning documents)?"""
        if not self.excluded_paths:
            return False
        anchor = finding["anchor"] or {}
        return any(is_excluded(path, self.excluded_paths) for path in (finding["file"], _rel(anchor.get("file"))) if path)

    @staticmethod
    def _normalize(raw: Dict[str, Any], local_id: str, pass_no: int) -> Dict[str, Any]:
        finding = {
            "local_id": local_id,
            "pass": pass_no,
            "title": _text(raw.get("title")),
            "description": _text(raw.get("description")),
            "severity": _text(raw.get("severity")).lower(),
            "kind": _text(raw.get("kind")).lower(),
            "rule_id": _text(raw.get("rule_id")) or "none",
            "file": _rel(raw.get("file")),
            "line": _int(raw.get("line")) or 0,
            "quote": _text(raw.get("quote")),
            "anchor": raw.get("anchor") if isinstance(raw.get("anchor"), dict) else None,
            "expected": _text(raw.get("expected")),
            "suggestion": _text(raw.get("suggestion")),
        }
        if finding["severity"] not in SEVERITIES:
            finding["severity"] = "low"
        return finding

    @staticmethod
    def _shape_problem(finding: Dict[str, Any]) -> str:
        problems = []
        if not finding["title"]:
            problems.append("missing title")
        if not finding["description"]:
            problems.append("missing description")
        if finding["kind"] not in KINDS:
            problems.append("invalid kind")
        if not finding["file"]:
            problems.append("missing file")
        return ", ".join(problems)

    def _apply_self_check(self, finding: Dict[str, Any], raw: Dict[str, Any], prepared: PreparedFindings) -> None:
        verdict, reason = "confirmed", ""
        if self.self_check_on:
            check = raw.get("self_check")
            if isinstance(check, dict) and check.get("verdict") in ("confirmed", "rejected"):
                verdict, reason = check["verdict"], _text(check.get("reason"))
            else:
                prepared.self_check_missing += 1  # treated as confirmed, but counted
        finding["verdict"] = verdict
        finding["self_check_reason"] = reason

    @staticmethod
    def _evidence_problem(finding: Dict[str, Any]) -> Optional[str]:
        """Is the evidence the finding's kind requires present at all? (Whether it is *true* is checked by `ground`.)"""
        kind = finding["kind"]
        if kind == "present" and not finding["quote"]:
            return "kind present requires a quote"
        if kind == "absent":
            anchor = finding["anchor"] or {}
            if not (anchor.get("file") and anchor.get("line_start") and anchor.get("line_end")) or not finding["expected"]:
                return "kind absent requires an anchor and an expected description"
        if kind == "cross_file" and not (finding["quote"] or finding["anchor"]):
            return "kind cross_file requires a quote or an anchor"
        return None

    def _sort(self, finding: Dict[str, Any], prepared: PreparedFindings) -> None:
        problem = self._evidence_problem(finding)
        if problem:
            finding.update(status="ungrounded", reason=problem)
            prepared.ungrounded.append(finding)
            return
        rule_id, outcome = resolve_rule_id(finding["rule_id"], self.rule_index)
        if outcome == "repaired":
            prepared.rule_repaired += 1
        elif outcome == "unknown":
            finding["rule_citation"] = finding["rule_id"]  # keep what the model wrote, for auditing
            prepared.rule_unknown += 1
        finding["rule_id"] = rule_id
        if finding["kind"] == "absent" and not finding["line"]:
            finding["line"] = _int(finding["anchor"].get("line_start")) or 0

        grounded, why, corrected = ground(finding, self.cache, self.diff)
        if not grounded:
            finding.update(status="ungrounded", reason=why)
            prepared.ungrounded.append(finding)
            return
        if corrected is not None and corrected != finding["line"]:
            if finding["line"] and abs(corrected - finding["line"]) > self.line_window:
                prepared.line_corrected += 1
            finding["line"] = corrected
        finding.update(status="ok", reason=why)
        prepared.ok.append(finding)


def compact(finding: Dict[str, Any], ident: str, id_key: str, max_description: int = 600) -> Dict[str, Any]:
    """Compact finding view sent to the Comparator (`id_key` is `local_id` for new findings, `id` for candidates)."""
    view: Dict[str, Any] = {
        id_key: ident,
        "title": finding["title"],
        "description": finding["description"][:max_description],
        "file": finding["file"],
        "line": finding["line"],
        "kind": finding["kind"],
        "rule_id": finding["rule_id"],
        "severity": finding["severity"],
    }
    if finding.get("quote"):
        view["quote"] = finding["quote"][:300]
    if finding.get("anchor"):
        view["anchor"] = finding["anchor"]
    return view
