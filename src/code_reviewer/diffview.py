"""Unified diff parsing, exclusion filtering and rendering for the Reviewer prompt."""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DIFF_GIT = re.compile(r"^diff --git a/(.*) b/(.*)$")
MAX_ROW_CHARS = 400


@dataclass
class DiffLine:
    kind: str  # "+", "-" or " "
    old_no: Optional[int]
    new_no: Optional[int]
    text: str


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: List[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    path: str
    old_path: Optional[str] = None
    status: str = "modified"  # added, deleted, modified, renamed
    binary: bool = False
    added: int = 0
    removed: int = 0
    hunks: List[Hunk] = field(default_factory=list)
    excluded: bool = False

    @property
    def changed_lines(self) -> int:
        return self.added + self.removed


def _clean_path(raw: str) -> str:
    raw = raw.split("\t")[0].strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        raw = raw[1:-1]
    return raw


def parse_unified_diff(text: str) -> List[FileDiff]:
    files: List[FileDiff] = []
    current: Optional[FileDiff] = None
    hunk: Optional[Hunk] = None
    old_no = new_no = old_rem = new_rem = 0

    for line in text.split("\n"):
        if hunk is not None and (old_rem > 0 or new_rem > 0):
            marker = line[:1]
            if marker == "+":
                hunk.lines.append(DiffLine("+", None, new_no, line[1:]))
                new_no += 1
                new_rem -= 1
                current.added += 1  # type: ignore[union-attr]
                continue
            if marker == "-":
                hunk.lines.append(DiffLine("-", old_no, None, line[1:]))
                old_no += 1
                old_rem -= 1
                current.removed += 1  # type: ignore[union-attr]
                continue
            if marker == " " or line == "":
                hunk.lines.append(DiffLine(" ", old_no, new_no, line[1:]))
                old_no += 1
                new_no += 1
                old_rem -= 1
                new_rem -= 1
                continue
            if marker == "\\":
                continue
            hunk = None  # malformed hunk: fall through to header handling

        match = _DIFF_GIT.match(line)
        if match:
            current = FileDiff(path=_clean_path(match.group(2)))
            files.append(current)
            hunk = None
            continue
        if current is None:
            continue
        if line.startswith("new file mode"):
            current.status = "added"
        elif line.startswith("deleted file mode"):
            current.status = "deleted"
        elif line.startswith("rename from "):
            current.old_path = _clean_path(line[len("rename from "):])
            current.status = "renamed"
        elif line.startswith("rename to "):
            current.path = _clean_path(line[len("rename to "):])
        elif line.startswith("Binary files") or line.startswith("GIT binary patch"):
            current.binary = True
        elif line.startswith("--- "):
            target = _clean_path(line[4:])
            if target != "/dev/null" and target.startswith("a/") and current.status != "renamed":
                current.old_path = current.old_path or target[2:]
        elif line.startswith("+++ "):
            target = _clean_path(line[4:])
            if target == "/dev/null":
                current.status = "deleted"
                if current.old_path:
                    current.path = current.old_path
            elif target.startswith("b/"):
                current.path = target[2:]
        else:
            hunk_match = _HUNK.match(line)
            if hunk_match:
                old_start = int(hunk_match.group(1))
                old_len = int(hunk_match.group(2)) if hunk_match.group(2) is not None else 1
                new_start = int(hunk_match.group(3))
                new_len = int(hunk_match.group(4)) if hunk_match.group(4) is not None else 1
                hunk = Hunk(old_start, old_len, new_start, new_len)
                current.hunks.append(hunk)
                old_no, new_no, old_rem, new_rem = old_start, new_start, old_len, new_len
    return files


def is_excluded(path: str, patterns: Sequence[str]) -> bool:
    parts = path.split("/")
    base = parts[-1]
    for pattern in patterns:
        if pattern.endswith("/"):
            if pattern[:-1] in parts[:-1]:
                return True
        elif fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(base, pattern):
            return True
    return False


def mark_exclusions(files: List[FileDiff], patterns: Sequence[str]) -> None:
    for file_diff in files:
        file_diff.excluded = is_excluded(file_diff.path, patterns)


def reviewable(files: Sequence[FileDiff]) -> List[FileDiff]:
    return [f for f in files if not f.excluded]


def changed_line_count(files: Sequence[FileDiff]) -> int:
    return sum(f.changed_lines for f in reviewable(files))


def _row(text: str) -> str:
    return text if len(text) <= MAX_ROW_CHARS else text[:MAX_ROW_CHARS] + "...[truncated]"


def render_diff_block(files: Sequence[FileDiff]) -> str:
    """Render the diff with explicit line markers: `L<new> <+| > text` and `R<old> - text`."""
    chunks: List[str] = []
    for file_diff in reviewable(files):
        header = "--- %s (%s)" % (file_diff.path, file_diff.status)
        if file_diff.status == "renamed" and file_diff.old_path:
            header = "--- %s (renamed from %s)" % (file_diff.path, file_diff.old_path)
        if file_diff.binary:
            chunks.append("--- %s (binary file, content not shown)" % file_diff.path)
            continue
        rows = [header]
        for index, hunk in enumerate(file_diff.hunks):
            if index > 0:
                rows.append("...")
            for line in hunk.lines:
                if line.kind == "-":
                    rows.append("R%d - %s" % (line.old_no, _row(line.text)))
                else:
                    rows.append("L%d %s %s" % (line.new_no, "+" if line.kind == "+" else " ", _row(line.text)))
        chunks.append("\n".join(rows))
    return "\n\n".join(chunks)


def render_changed_files(files: Sequence[FileDiff]) -> str:
    rows = []
    for f in reviewable(files):
        detail = "binary" if f.binary else "+%d/-%d" % (f.added, f.removed)
        rows.append("%s (%s, %s)" % (f.path, f.status, detail))
    return "\n".join(rows) if rows else "none"


def render_excluded(files: Sequence[FileDiff]) -> str:
    names = [f.path for f in files if f.excluded]
    return ", ".join(names) if names else "none"


class DiffIndex:
    """Lookup structure used by the grounding check."""

    def __init__(self, files: Sequence[FileDiff]):
        self.files: Dict[str, FileDiff] = {f.path: f for f in files if not f.excluded}

    def paths(self) -> Set[str]:
        return set(self.files)

    def changed_ranges(self, path: str) -> List[Tuple[int, int]]:
        """New-side line ranges covered by hunks (including context lines)."""
        file_diff = self.files.get(path)
        if file_diff is None:
            return []
        ranges = []
        for hunk in file_diff.hunks:
            length = max(hunk.new_len, 1)
            ranges.append((hunk.new_start, hunk.new_start + length - 1))
        return ranges

    def removed_texts(self, path: str) -> List[str]:
        file_diff = self.files.get(path)
        if file_diff is None:
            return []
        return [line.text for hunk in file_diff.hunks for line in hunk.lines if line.kind == "-"]
