"""One review pass: review -> check evidence -> compare with the master list -> merge."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from .config import Config
from .findings import FindingsPreparer, PreparedFindings
from .master import MasterList, PassOutcome
from .models import PassRecord
from .roles import Comparator, Reviewer
from .run_store import RunStore
from .stats import Stats


class PassRunner:
    def __init__(self, cfg: Config, reviewer: Reviewer, comparator: Comparator, preparer: FindingsPreparer,
                 master: MasterList, stats: Stats, store: RunStore, emit: Callable[..., None]):
        self.cfg, self.reviewer, self.comparator, self.preparer = cfg, reviewer, comparator, preparer
        self.master, self.stats, self.store, self.emit = master, stats, store, emit

    def run(self, pass_no: int) -> PassRecord:
        """Run one pass and merge its findings into the master list. Raises PassFailed if a model call cannot be used."""
        pass_dir = self.store.pass_dir(pass_no)
        prepared = self._review(pass_no, pass_dir)
        findings = prepared.ok
        decisions, compared = self._compare(pass_no, findings, pass_dir)
        outcome = self.master.apply(pass_no, findings, decisions)
        self.store.dump("%s/mapping.json" % pass_dir.name, {"compared": compared, "decisions": decisions, "outcome": outcome.__dict__})
        return self._record(pass_no, prepared, outcome)

    def _review(self, pass_no: int, pass_dir: Any) -> PreparedFindings:
        self.emit("reviewer_started", pass_no=pass_no, model=self.cfg.model_reviewer)
        result = self.reviewer.review(pass_no, pass_dir / "reviewer.raw.json")
        parsed = result.parsed or {}
        prepared = self.preparer.prepare(parsed.get("findings"), pass_no)
        self.store.dump("%s/findings.json" % pass_dir.name, {"notes": parsed.get("notes", ""), "findings": prepared.all_records()})
        self.emit(
            "reviewer_done", pass_no=pass_no, raised=len(prepared.all_records()), usable=len(prepared.ok),
            ungrounded=len(prepared.ungrounded), out_of_scope=len(prepared.out_of_scope),
            rule_fixed=prepared.rule_repaired + prepared.rule_unknown,
            malformed=len(prepared.malformed), rejected=_rejected(prepared.ok),
        )
        return prepared

    def _compare(self, pass_no: int, findings: List[Dict[str, Any]], pass_dir: Any) -> Tuple[Dict[str, Dict[str, Any]], bool]:
        cfg = self.cfg
        candidates = self.master.candidates(findings, cfg.candidate_window, cfg.candidate_all_threshold, cfg.candidate_max)
        if self.comparator.is_trivial(findings, candidates):
            return self.comparator.all_new(findings), False
        self.emit("comparator_started", pass_no=pass_no, new=len(findings), candidates=len(candidates))
        return self.comparator.compare(pass_no, findings, candidates, pass_dir / "comparator.raw.json"), True

    def _record(self, pass_no: int, prepared: PreparedFindings, outcome: PassOutcome) -> PassRecord:
        reviewer = self.stats.by_pass_role(pass_no, "reviewer")
        comparator = self.stats.by_pass_role(pass_no, "comparator")
        return PassRecord(
            pass_no=pass_no,
            raised=len(prepared.all_records()),
            usable=len(prepared.ok),
            rejected_by_self_check=_rejected(prepared.ok),
            ungrounded=len(prepared.ungrounded),
            out_of_scope=len(prepared.out_of_scope),
            rule_repaired=prepared.rule_repaired,
            rule_unknown=prepared.rule_unknown,
            malformed=len(prepared.malformed),
            self_check_missing=prepared.self_check_missing,
            line_corrected=prepared.line_corrected,
            new_total=len(outcome.new_ids),
            new_scored=len(outcome.new_scored),
            matched=len(outcome.matched_ids),
            reviewer_cost_usd=reviewer["cost_usd"],
            reviewer_tokens=reviewer["tokens"],
            comparator_cost_usd=comparator["cost_usd"],
            comparator_tokens=comparator["tokens"],
        )


def _rejected(findings: List[Dict[str, Any]]) -> int:
    return sum(1 for f in findings if f["verdict"] == "rejected")
