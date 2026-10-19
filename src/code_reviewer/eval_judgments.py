"""Human labels with explicit rubric checks; no model is asked to establish truth."""
from __future__ import annotations

from typing import Any, Dict

from .eval_cases import EvalError, object_hash

CHECKS = ("correct", "introduced", "useful")
LABELS = ("match", "additional", "wrong", "duplicate", "uncertain", "pending")


def judgment_template(master: Dict[str, Any], rubric_hash: str) -> Dict[str, Any]:
    return {
        "version": 1, "rubric_hash": rubric_hash, "master_hash": object_hash(master),
        "findings": {
            item["id"]: {
                "checks": {key: "uncertain" for key in CHECKS}, "verdict": "pending",
                "issue_id": "", "duplicate_of": "", "reason": "",
            } for item in master["findings"] if item["seen_in_passes"]
        },
    }


def validate_judgments(document: Dict[str, Any], master: Dict[str, Any], case: Dict[str, Any],
                       rubric_hash: str) -> Dict[str, Any]:
    template = judgment_template(master, rubric_hash)
    if any(document.get(k) != template[k] for k in ("version", "rubric_hash", "master_hash")):
        raise EvalError("judgments do not match the saved rubric/master version")
    entries = document.get("findings")
    if not isinstance(entries, dict) or set(entries) != set(template["findings"]):
        raise EvalError("judgments must cover exactly the scored finding IDs")
    expected = {issue["id"] for issue in case["expected"]}
    originals: Dict[str, str] = {}
    for ident, entry in entries.items():
        if not isinstance(entry, dict):
            raise EvalError("each judgment must be an object")
        verdict = entry.get("verdict")
        checks = entry.get("checks")
        if verdict not in LABELS or not isinstance(checks, dict) or set(checks) != set(CHECKS):
            raise EvalError("each judgment needs a known verdict and all three rubric checks")
        if any(value not in ("pass", "fail", "uncertain") for value in checks.values()):
            raise EvalError("rubric checks must be pass, fail or uncertain")
        if not all(isinstance(entry.get(k), str) for k in ("issue_id", "duplicate_of", "reason")):
            raise EvalError("issue_id, duplicate_of and reason must be strings")
        if verdict != "pending" and not entry["reason"].strip():
            raise EvalError("judged findings need an evidence-based reason")
        values = set(checks.values())
        if verdict in ("match", "additional", "duplicate") and values != {"pass"}:
            raise EvalError("valid findings must pass all three checks")
        if verdict == "wrong" and "fail" not in values:
            raise EvalError("wrong findings need a demonstrated failing check")
        if verdict == "uncertain" and ("uncertain" not in values or "fail" in values):
            raise EvalError("uncertain needs an unresolved check and no demonstrated failure")
        issue = entry["issue_id"]
        if verdict in ("match", "additional", "duplicate") and not issue.strip():
            raise EvalError("valid findings need a stable issue_id")
        if verdict == "match" and issue not in expected:
            raise EvalError("match needs a known expected issue_id")
        if verdict == "additional" and issue in expected:
            raise EvalError("use match for an expected issue")
        if verdict in ("match", "additional"):
            if issue in originals:
                raise EvalError("repeated valid issues must be explicitly labeled duplicate")
            originals[issue] = ident
        if verdict != "duplicate" and entry["duplicate_of"]:
            raise EvalError("only duplicate judgments may specify duplicate_of")
        if verdict in ("wrong", "uncertain", "pending") and issue:
            raise EvalError("unresolved/wrong judgments must leave issue_id empty")
    for ident, entry in entries.items():
        if entry["verdict"] == "duplicate" and originals.get(entry["issue_id"]) != entry["duplicate_of"]:
            raise EvalError("duplicate_of must identify a match/additional finding for the same issue")
    return entries


def make_judgment(verdict: str, issue: str, reason: str, duplicate_of: str = "",
                  fail: str = "", uncertain: str = "") -> Dict[str, Any]:
    checks = {key: "pass" for key in CHECKS}
    if verdict == "pending":
        checks = {key: "uncertain" for key in CHECKS}
    if fail:
        checks[fail] = "fail"
    if uncertain:
        checks[uncertain] = "uncertain"
    return {"checks": checks, "verdict": verdict, "issue_id": issue,
            "duplicate_of": duplicate_of, "reason": reason}
