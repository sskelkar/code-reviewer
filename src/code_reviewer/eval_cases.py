"""Pinned local review cases. Answer keys never enter the Reviewer context."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from .config import REPO_ROOT
from .models import PRContext


class EvalError(ValueError):
    """An eval input cannot be used without guessing its meaning."""


def read_object(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise EvalError("%s must contain a JSON object" % path)
    return data


def object_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


suite_hash = object_hash


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def outside(path: Path, root: Path) -> bool:
    path, root = path.resolve(), root.resolve()
    return path != root and root not in path.parents


def external_output(path: Path, repo: Path) -> None:
    if not outside(path, repo) or not outside(path, REPO_ROOT):
        raise EvalError("eval artifacts must be outside both the reviewed repository and code-reviewer")


def local_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise EvalError("artifact paths must be relative")
    path = (root / relative).resolve()
    if outside(path, root) or path == root.resolve():
        raise EvalError("artifact path escapes the experiment")
    return path


def validate_suite(suite: Dict[str, Any]) -> Dict[str, Any]:
    if type(suite.get("version")) is not int or suite["version"] != 1:
        raise EvalError("suite needs version 1")
    if not isinstance(suite.get("cases"), list) or not suite["cases"]:
        raise EvalError("suite needs a non-empty cases list")
    seen = set()
    for case in suite["cases"]:
        if not isinstance(case, dict):
            raise EvalError("each case must be an object")
        ident = case.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", ident) or ident in seen:
            raise EvalError("case IDs must be unique lowercase names")
        seen.add(ident)
        for key in ("base", "head"):
            if not isinstance(case.get(key), str) or not re.fullmatch(r"[0-9a-f]{40}", case[key]):
                raise EvalError("%s: %s must be a full commit SHA" % (ident, key))
        for key in ("title", "description"):
            if not isinstance(case.get(key), str) or not case[key].strip():
                raise EvalError("%s needs %s" % (ident, key))
        status = case.get("expectation_status", "provisional")
        if status not in ("provisional", "confirmed"):
            raise EvalError("expectation_status must be provisional or confirmed")
        if status == "confirmed" and (not isinstance(case.get("expectation_reason"), str)
                                      or not case["expectation_reason"].strip()):
            raise EvalError("confirmed expectations need an expectation_reason explaining human verification")
        expected = case.get("expected")
        if not isinstance(expected, list):
            raise EvalError("%s needs an expected list (empty is not proof of a clean diff)" % ident)
        issue_ids = set()
        for issue in expected:
            if not isinstance(issue, dict) or not all(isinstance(issue.get(k), str) and issue[k].strip()
                                                     for k in ("id", "criterion", "file", "description", "evidence")):
                raise EvalError("expected issues need id, criterion, file, description and evidence")
            if issue["id"] in issue_ids:
                raise EvalError("expected issue IDs must be unique within a case")
            issue_ids.add(issue["id"])
    return suite


def load_suite(path: Path) -> Dict[str, Any]:
    return validate_suite(read_object(path))


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)
    if proc.returncode:
        raise EvalError("git failed: %s" % proc.stderr.strip())
    return proc.stdout.strip()


def pinned_commit(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")


def case_context(repo: Path, case: Dict[str, Any], number: int) -> PRContext:
    for sha in (case["base"], case["head"]):
        if pinned_commit(repo, sha) != sha:
            raise EvalError("case does not resolve to its pinned commit")
    if git(repo, "merge-base", case["base"], case["head"]) != case["base"]:
        raise EvalError("case base must be an ancestor of head; supply the PR merge-base")
    # Commit messages, IDs, notes and answers can reveal a later repair or the expected defect.
    return PRContext(
        repo="eval/project", number=number, url="", title=case["title"], body=case["description"],
        commit_messages="", base_ref="eval-base", base_sha=case["base"], head_sha=case["head"],
        merge_base=case["base"], git_dir=repo,
    )


def select_cases(suite: Dict[str, Any], selected: List[str]) -> List[Dict[str, Any]]:
    known = {c["id"] for c in suite["cases"]}
    if set(selected) - known:
        raise EvalError("unknown case IDs: %s" % ", ".join(sorted(set(selected) - known)))
    return [c for c in suite["cases"] if not selected or c["id"] in selected]
