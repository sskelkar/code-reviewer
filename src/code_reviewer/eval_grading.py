"""Deterministic scores over human labels, with incomplete evidence kept visible."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from .eval_cases import EvalError, file_hash, load_suite, local_file, object_hash, read_object
from .eval_judgments import validate_judgments


def load_experiment(root: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    experiment = read_object(root / "experiment.json")
    if type(experiment.get("version")) is not int or experiment["version"] != 1 or experiment.get("mode") != "run":
        raise EvalError("only a version 1 run has review outputs to grade")
    if not isinstance(experiment.get("repo"), str) or not experiment["repo"]:
        raise EvalError("experiment needs its source clone path")
    suite = load_suite(root / "suite.json")
    if object_hash(suite) != experiment.get("suite_hash"):
        raise EvalError("suite changed after the experiment was created; create a new experiment to change expectations")
    if file_hash(root / "rubric.md") != experiment.get("rubric_hash"):
        raise EvalError("saved rubric changed after the experiment was created")
    if object_hash(read_object(root / "environment.json")) != experiment.get("environment_hash"):
        raise EvalError("saved experiment environment changed")
    trials = experiment.get("trials")
    if not isinstance(trials, list) or not trials:
        raise EvalError("experiment needs trials")
    known = {c["id"]: c for c in suite["cases"]}
    ids = set()
    for trial in trials:
        if not isinstance(trial, dict) or not isinstance(trial.get("id"), str) or trial["id"] in ids:
            raise EvalError("trial IDs must be unique strings")
        ids.add(trial["id"])
        if trial.get("case") not in known:
            raise EvalError("unknown trial case")
        case = known[trial["case"]]
        if trial.get("base") != case["base"] or trial.get("head") != case["head"]:
            raise EvalError("trial commits do not match its pinned case")
        if (not isinstance(trial.get("model"), str) or type(trial.get("passes")) is not int
                or trial["passes"] < 1 or type(trial.get("repeat")) is not int or trial["repeat"] < 1):
            raise EvalError("invalid trial model/pass/repeat")
    return experiment, suite


def trial_documents(root: Path, experiment: Dict[str, Any], trial: Dict[str, Any],
                    case: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if "run" not in trial or "judgments" not in trial:
        raise EvalError("trial has no saved findings to label")
    run = local_file(root, trial["run"])
    master = read_object(run / "master.json")
    if object_hash(master) != trial.get("master_hash"):
        raise EvalError("saved master findings changed")
    document = read_object(local_file(root, trial["judgments"]))
    validate_judgments(document, master, case, experiment["rubric_hash"])
    return master, document


def grade_trial(trial: Dict[str, Any], case: Dict[str, Any], root: Path,
                experiment: Dict[str, Any]) -> Dict[str, Any]:
    result = {k: trial[k] for k in ("id", "case", "model", "passes", "repeat", "status")}
    result.update(cost_usd_estimated=trial.get("cost_usd_estimated", 0.0),
                  cost_known=trial.get("cost_known", trial["status"] == "skipped"),
                  duration_s=trial.get("duration_s", 0.0), error=trial.get("error", ""))
    if trial["status"] != "complete":
        return {**result, "eligible": False, "reason": "trial did not complete all requested passes"}
    master, document = trial_documents(root, experiment, trial, case)
    entries = document["findings"]
    expected = {issue["id"] for issue in case["expected"]}
    matched = {e["issue_id"] for e in entries.values() if e["verdict"] == "match"}
    additional = {e["issue_id"] for e in entries.values() if e["verdict"] == "additional"}
    counts = {label: sum(e["verdict"] == label for e in entries.values())
              for label in ("wrong", "duplicate", "pending", "uncertain")}
    total = len(entries)
    judged = total - counts["pending"] - counts["uncertain"]
    complete = judged == total
    confirmed = case.get("expectation_status", "provisional") == "confirmed"
    valid = len(matched) + len(additional)
    return {
        **result, "eligible": complete, "findings": total, "judged": judged,
        "label_coverage": judged / total if total else 1.0,
        "valid_unique": valid, "matched_expected": len(matched), "expected": len(expected), **counts,
        "expectations_confirmed": confirmed,
        "missed_expected": sorted(expected - matched) if complete and confirmed else None,
        "precision": valid / total if complete and total else None,
        "known_issue_recall": len(matched) / len(expected) if complete and confirmed and expected else None,
        "rejected_only": sum(not item["seen_in_passes"] for item in master["findings"]),
        "pass_records": trial.get("pass_records", []), "usage": trial.get("usage", {}),
    }


def summarize_many(experiments: List[Tuple[Dict[str, Any], Dict[str, Any], Path]]) -> Dict[str, Any]:
    if len({e[0]["environment_hash"] for e in experiments}) != 1:
        raise EvalError("cannot aggregate experiments with different code, settings, criteria, prompts or rubric")
    trials: List[Dict[str, Any]] = []
    seen = set()
    for experiment, suite, root in experiments:
        cases = {c["id"]: c for c in suite["cases"]}
        for trial in experiment["trials"]:
            case = cases[trial["case"]]
            case_key = object_hash({"source": suite.get("source", ""), "case": case})
            identity = (case_key, trial["model"], trial["passes"], trial["repeat"])
            if identity in seen:
                raise EvalError("overlapping trials; use unique repetition numbers or grade experiments separately")
            seen.add(identity)
            row = grade_trial(trial, case, root, experiment)
            row.update(experiment=str(root), case_key=case_key,
                       scenario=object_hash({"source": suite.get("source", ""), "pair": case.get("pair", case_key)}))
            trials.append(row)
    groups: List[Dict[str, Any]] = []
    coverage = []
    for model, passes in sorted({(t["model"], t["passes"]) for t in trials}):
        all_trials = [t for t in trials if (t["model"], t["passes"]) == (model, passes)]
        ready = [t for t in all_trials if t["eligible"]]
        complete = len(ready) == len(all_trials)
        coverage.append({(t["case_key"], t["repeat"]) for t in all_trials})
        count = sum(t["findings"] for t in ready)
        valid = sum(t["valid_unique"] for t in ready)
        confirmed = [t for t in ready if t["expectations_confirmed"]]
        expected = sum(t["expected"] for t in confirmed)
        cost = sum(t["cost_usd_estimated"] for t in all_trials)
        cost_known = all(t["cost_known"] for t in all_trials)
        groups.append({
            "model": model, "passes": passes, "trials": len(all_trials), "graded_complete_trials": len(ready),
            "quality_complete": complete, "precision": valid / count if complete and count else None,
            "known_issue_recall": sum(t["matched_expected"] for t in confirmed) / expected if complete and expected else None,
            "confirmed_expected": expected, "provisional_trials": sum(not t["expectations_confirmed"] for t in ready),
            "wrong": sum(t["wrong"] for t in ready), "duplicates": sum(t["duplicate"] for t in ready),
            "pending": sum(t.get("pending", 0) for t in all_trials),
            "uncertain": sum(t.get("uncertain", 0) for t in all_trials),
            "incomplete_trials": sum(t["status"] != "complete" for t in all_trials),
            "cost_usd_estimated": cost, "cost_known": cost_known,
            "cost_per_valid_issue": cost / valid if complete and valid and cost_known else None,
            "duration_s": sum(t["duration_s"] for t in all_trials),
        })
    balanced = all(c == coverage[0] for c in coverage)
    return {"environment_hash": experiments[0][0]["environment_hash"], "groups": groups, "trials": trials,
            "balanced_cases_and_repetitions": balanced,
            "comparison_ready": balanced and all(g["quality_complete"] for g in groups),
            "underlying_scenarios": len({t["scenario"] for t in trials})}


def summarize(experiment: Dict[str, Any], suite: Dict[str, Any], root: Path) -> Dict[str, Any]:
    return summarize_many([(experiment, suite, root)])


def render_evaluation(summary: Dict[str, Any]) -> str:
    lines = ["# Review evaluation", "",
             "Human labels; deterministic scoring. Agreement between review passes is not correctness.",
             "Group quality scores require every scheduled trial to complete and be fully adjudicated.",
             "Precision counts unique valid issues / findings, penalizing duplicates. Empty precision is undefined.",
             "Recall covers only confirmed expected issues; provisional keys never enter its denominator.",
             "Costs include incomplete trials; unknown costs are marked. Limits are soft, checked between calls/trials.",
             "", "Comparison ready: %s. Balanced cases/repetitions: %s. Underlying scenarios: %d." % (
                 summary["comparison_ready"], summary["balanced_cases_and_repetitions"], summary["underlying_scenarios"]), "",
             "| Model | Passes | Graded / trials | Precision | Known recall | Confirmed expected | Wrong | Duplicates | Cost* | Seconds |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    def percent(value: Any) -> str:
        return "—" if value is None else "%.1f%%" % (100 * value)
    for g in summary["groups"]:
        lines.append("| %s | %d | %d / %d | %s | %s | %d | %d | %d | $%.4f%s | %.1f |" % (
            g["model"], g["passes"], g["graded_complete_trials"], g["trials"], percent(g["precision"]),
            percent(g["known_issue_recall"]), g["confirmed_expected"], g["wrong"], g["duplicates"],
            g["cost_usd_estimated"], " ?" if not g["cost_known"] else "", g["duration_s"]))
    lines.append("")
    for g in summary["groups"]:
        lines.append("%s / %d passes: %d pending, %d uncertain, %d incomplete trials, %d provisionally keyed trials." % (
            g["model"], g["passes"], g["pending"], g["uncertain"], g["incomplete_trials"], g["provisional_trials"]))
    lines += ["", "* Estimated Claude-reported cost, not a billing guarantee. ? means some cost is unknown.", "",
              "Per-trial findings, missed confirmed issues, validation counters and usage are in evaluation.json.",
              "Pairs/repetitions are related observations; this small dataset does not establish broad model superiority."]
    return "\n".join(lines) + "\n"
