"""The master finding list: merge results of independent passes and compute confidence."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

from .findings import SEVERITY_RANK

CANONICAL_FIELDS = ("title", "description", "severity", "kind", "rule_id", "file", "line", "quote", "anchor", "expected", "suggestion")
MAX_VARIANTS = 6


@dataclass
class PassOutcome:
    new_ids: List[str] = field(default_factory=list)       # master entries created in this pass
    matched_ids: List[str] = field(default_factory=list)   # pre-existing entries that were matched
    new_scored: List[str] = field(default_factory=list)    # entries that became scored (first confirmed sighting)
    assigned: Dict[str, str] = field(default_factory=dict)  # local finding ID -> master finding ID it was merged into


def _canonical(finding: Dict[str, Any]) -> Dict[str, Any]:
    return {key: copy.deepcopy(finding.get(key)) for key in CANONICAL_FIELDS}


class MasterList:
    def __init__(self) -> None:
        self.items: Dict[str, Dict[str, Any]] = {}
        self._counter = 0

    # --- queries -----------------------------------------------------------------

    def scored_ids(self) -> Set[str]:
        return {i for i, item in self.items.items() if item["seen_in_passes"]}

    def scored(self) -> List[Dict[str, Any]]:
        return [item for item in self.items.values() if item["seen_in_passes"]]

    def rejected_only(self) -> List[Dict[str, Any]]:
        return [item for item in self.items.values() if not item["seen_in_passes"]]

    def candidates(self, findings: Sequence[Dict[str, Any]], window: int, all_threshold: int, max_items: int) -> List[Dict[str, Any]]:
        """Master entries the Comparator may match against."""
        items = list(self.items.values())
        if len(items) <= all_threshold:
            return items
        scored: List[Tuple[int, Dict[str, Any]]] = []
        for item in items:
            canon = item["canonical"]
            best = 3
            for finding in findings:
                same_file = canon["file"] == finding["file"]
                near = same_file and abs((canon["line"] or 0) - (finding["line"] or 0)) <= window
                same_rule = finding["rule_id"] != "none" and canon["rule_id"] == finding["rule_id"]
                if near:
                    best = min(best, 0)
                elif same_rule:
                    best = min(best, 1)
                elif same_file and "cross_file" in (canon["kind"], finding["kind"]):
                    best = min(best, 2)
            if best < 3:
                scored.append((best, item))
        scored.sort(key=lambda pair: pair[0])
        return [item for _, item in scored[:max_items]]

    # --- updates -----------------------------------------------------------------

    def _new_item(self, pass_no: int, finding: Dict[str, Any]) -> str:
        self._counter += 1
        ident = "F-%03d" % self._counter
        self.items[ident] = {
            "id": ident,
            "canonical": _canonical(finding),
            "seen_in_passes": [],
            "rejected_in_passes": [],
            "rejected_reasons": {},
            "severities_seen": [],
            "variants": [],
            "first_seen": pass_no,
            "canonical_from_rejected": False,
        }
        return ident

    def _record(self, ident: str, pass_no: int, finding: Dict[str, Any]) -> None:
        item = self.items[ident]
        canon = item["canonical"]
        if finding["verdict"] == "confirmed":
            if pass_no not in item["seen_in_passes"]:
                item["seen_in_passes"].append(pass_no)
                item["severities_seen"].append(finding["severity"])
            if item["canonical_from_rejected"]:
                item["canonical"] = canon = _canonical(finding)
                item["canonical_from_rejected"] = False
        else:
            if pass_no not in item["rejected_in_passes"]:
                item["rejected_in_passes"].append(pass_no)
            item["rejected_reasons"][str(pass_no)] = finding.get("self_check_reason", "")
            if not item["seen_in_passes"]:
                item["canonical_from_rejected"] = True

        if item["severities_seen"]:
            canon["severity"] = max(item["severities_seen"], key=lambda s: SEVERITY_RANK.get(s, 0))
        if finding["title"] != canon["title"] and len(item["variants"]) < MAX_VARIANTS:
            if not any(v["pass"] == pass_no and v["title"] == finding["title"] for v in item["variants"]):
                item["variants"].append({
                    "pass": pass_no, "title": finding["title"], "description": finding["description"],
                    "file": finding["file"], "line": finding["line"],
                })

    def apply(self, pass_no: int, findings: Sequence[Dict[str, Any]], decisions: Dict[str, Dict[str, Any]]) -> PassOutcome:
        outcome = PassOutcome()
        scored_before = self.scored_ids()
        resolved: Dict[str, str] = {}
        for finding in findings:
            decision = decisions.get(finding["local_id"], {"match": "new", "same_as": None})
            match = decision.get("match")
            same_as = decision.get("same_as")
            if match and match != "new" and match in self.items:
                ident = match
                if ident not in outcome.matched_ids and ident not in outcome.new_ids:
                    outcome.matched_ids.append(ident)
            elif same_as and same_as in resolved:
                ident = resolved[same_as]
            else:
                ident = self._new_item(pass_no, finding)
                outcome.new_ids.append(ident)
            resolved[finding["local_id"]] = ident
            self._record(ident, pass_no, finding)
        outcome.new_scored = sorted(self.scored_ids() - scored_before)
        outcome.assigned = dict(resolved)
        return outcome

    # --- serialization -----------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {"findings": list(self.items.values())}


def validate_decisions(
    findings: Sequence[Dict[str, Any]],
    candidate_ids: Iterable[str],
    payload: Any,
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Check Comparator output against the harness rules. Returns (decisions by local_id, errors)."""
    errors: List[str] = []
    decisions: Dict[str, Dict[str, Any]] = {}
    allowed = set(candidate_ids)
    order = [f["local_id"] for f in findings]
    entries = payload.get("decisions") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return {}, ["output has no 'decisions' list"]
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("a decision is not an object")
            continue
        local_id = entry.get("local_id")
        match = entry.get("match")
        if local_id not in order:
            errors.append("unknown local_id: %s" % local_id)
            continue
        if local_id in decisions:
            errors.append("duplicate decision for %s" % local_id)
            continue
        if match != "new" and match not in allowed:
            errors.append("%s: match %r is neither 'new' nor a candidate id" % (local_id, match))
            continue
        same_as = entry.get("same_as")
        if same_as is not None:
            earlier = order[: order.index(local_id)]
            if match != "new" or same_as not in earlier:
                same_as = None  # ignored: only valid for 'new' findings pointing at an earlier finding
        decisions[local_id] = {"match": match, "same_as": same_as}
    for local_id in order:
        if local_id not in decisions:
            errors.append("missing decision for %s" % local_id)
    return decisions, errors


def confidence(item: Dict[str, Any], passes_completed: int) -> float:
    if passes_completed <= 0:
        return 0.0
    return len(item["seen_in_passes"]) / float(passes_completed)


def tier(value: float, high: float, medium: float) -> str:
    if value >= high:
        return "High"
    if value >= medium:
        return "Medium"
    return "Low"


UNRATED = "Unrated"
MIN_PASSES_FOR_CONFIDENCE = 2


def rate(item: Dict[str, Any], passes_completed: int, high: float, medium: float) -> str:
    """The confidence tier of a finding, or UNRATED when fewer than two passes completed.

    Confidence is agreement between independent passes. With a single pass every finding found "1 of 1" would score 100%,
    which says nothing, so those findings are left unrated instead of being called High.
    """
    if passes_completed < MIN_PASSES_FOR_CONFIDENCE:
        return UNRATED
    return tier(confidence(item, passes_completed), high, medium)


def pass_overlap(master: MasterList, passes: Sequence[int]) -> Dict[str, Any]:
    """Pairwise Jaccard overlap of the finding sets seen by each pair of passes."""
    sets = {p: {i for i, item in master.items.items() if p in item["seen_in_passes"]} for p in passes}
    pairs = []
    for a, b in combinations(passes, 2):
        union = sets[a] | sets[b]
        if union:
            pairs.append({"a": a, "b": b, "jaccard": len(sets[a] & sets[b]) / float(len(union))})
    mean = sum(p["jaccard"] for p in pairs) / len(pairs) if pairs else None
    return {"pairs": pairs, "mean": mean}
