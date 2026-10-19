"""Shared test helpers: a scripted fake Claude runner and a throwaway git repository."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from code_reviewer.claude_cli import ClaudeResult
from code_reviewer.models import PRContext

APP_BASE = 'def get(user_id):\n    return db.run("SELECT 1")\n'
APP_HEAD = (
    'def get(user_id):\n'
    '    query = "SELECT * FROM t WHERE id=" + user_id\n'
    '    return db.run(query)\n'
)
AGENTS_MD = "# Agents\n\nBuild with make.\n\nReview criteria live in docs/review.md.\nSee also https://wiki.example.com/review.\n"
REVIEW_MD = "## Retry policy\n\nRetries must use bounded exponential backoff.\n\n## Logging\n\nNever log tokens.\n"


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def make_repo(root: Path) -> PRContext:
    repo = root / "project"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "app.py").write_text(APP_BASE)
    (repo / "AGENTS.md").write_text(AGENTS_MD)
    (repo / "docs").mkdir()
    (repo / "docs" / "review.md").write_text(REVIEW_MD)
    (repo / "package-lock.json").write_text("{}\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "app.py").write_text(APP_HEAD)
    (repo / "package-lock.json").write_text('{"changed": true}\n')
    git(repo, "commit", "-qam", "Build query by concatenation")
    head = git(repo, "rev-parse", "HEAD")
    return PRContext(
        repo="acme/project", number=7, url="https://github.com/acme/project/pull/7", title="Speed up lookups",
        body="Replaces the prepared statement with a faster query.", commit_messages="Build query by concatenation",
        base_ref="main", base_sha=base, head_sha=head, merge_base=base, git_dir=repo,
    )


def finding(**overrides: Any) -> Dict[str, Any]:
    data = {
        "title": "SQL built by concatenation", "description": "user_id is concatenated into SQL, allowing injection.",
        "severity": "high", "kind": "present", "rule_id": "GEN-004", "file": "app.py", "line": 2,
        "quote": 'query = "SELECT * FROM t WHERE id=" + user_id', "suggestion": "Use a parameterized query.",
        "self_check": {"verdict": "confirmed", "reason": "no validation upstream"},
    }
    data.update(overrides)
    return data


class FakeRunner:
    """Scripted stand-in for ClaudeRunner. Roles are told apart by their system prompts."""

    def __init__(self, reviewer: List[Any], comparator: Optional[List[Any]] = None,
                 resolver: Optional[Dict[str, Any]] = None, cost: float = 0.01, tokens: Optional[Dict[str, int]] = None):
        self.tokens = tokens or {"input": 1000, "output": 200, "cache_creation": 0, "cache_read": 500}
        self.reviewer = reviewer
        self.comparator = comparator or []
        self.resolver = resolver or {"criteria_files": [], "inline_sections": [], "organization_notes": "none stated", "ignored_references": []}
        self.cost = cost
        self.calls: List[Dict[str, Any]] = []
        self.counts = {"reviewer": 0, "comparator": 0, "resolver": 0}

    def version(self) -> str:
        return "fake-1.0"

    def _next(self, role: str, queue: List[Any]) -> Any:
        index = min(self.counts[role], len(queue) - 1)
        self.counts[role] += 1
        return queue[index]

    def run(self, **kwargs: Any) -> ClaudeResult:
        system = kwargs["system_prompt"]
        if "identify which files" in system:
            role, payload = "resolver", self._next("resolver", [self.resolver])
        elif "deduplicate code-review findings" in system:
            role, payload = "comparator", self._next("comparator", self.comparator)
        else:
            role, payload = "reviewer", self._next("reviewer", self.reviewer)
        self.calls.append({"role": role, **kwargs})
        if isinstance(payload, ClaudeResult):
            return payload
        return ClaudeResult(
            ok=True, parsed=payload, text=json.dumps(payload), cost_usd=self.cost,
            tokens=dict(self.tokens),
            raw={"fake": True, "role": role}, duration_ms=1200, num_turns=3, model="fake-haiku",
        )

    def calls_for(self, role: str) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["role"] == role]


class TempDirTestMixin:
    def make_tmp(self) -> Path:
        path = tempfile.mkdtemp(prefix="code-reviewer-test-")
        self.addCleanup(shutil.rmtree, path, True)  # type: ignore[attr-defined]
        return Path(path)
