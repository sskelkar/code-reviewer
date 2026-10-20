"""A fixed-attempt matrix over the existing Engine, with frozen experiment inputs."""
from __future__ import annotations

import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from .claude_cli import ClaudeError
from .config import DEFAULT_CRITERIA_DIR, PROMPTS_DIR, REPO_ROOT, Config
from .engine import Engine
from .eval_cases import (EvalError, case_context, external_output, file_hash, object_hash,
                         select_cases, validate_suite)
from .eval_judgments import judgment_template
from .git_ops import GitError
from .models import ReviewRefused
from .run_store import dump_json


def environment(cfg: Config) -> Dict[str, Any]:
    fixed = cfg.to_dict()
    for name in ("model_reviewer", "min_passes", "max_passes", "runs_dir"):
        fixed.pop(name)
    fixed.update(criteria_mode="defaults_only", criteria_manifest="")
    paths = list(Path(__file__).parent.rglob("*.py"))
    paths += [p for p in PROMPTS_DIR.rglob("*") if p.is_file()]
    paths += list(DEFAULT_CRITERIA_DIR.glob("*.md"))
    paths += [REPO_ROOT / "evals" / "RUBRIC.md"]
    hashes = {str(p.relative_to(REPO_ROOT)): file_hash(p) for p in sorted(paths)}
    return {"config": fixed, "files": hashes}


def run_matrix(suite: Dict[str, Any], repo: Path, output: Path, cfg: Config, runner: Any,
               models: List[str], passes: List[int], repeat: int = 1,
               selected: Optional[List[str]] = None, execute: bool = False,
               max_total_cost: Optional[float] = None) -> Dict[str, Any]:
    validate_suite(suite)
    if (not models or any(not isinstance(m, str) or not m.strip() for m in models)
            or not passes or any(type(p) is not int or p < 1 for p in passes)
            or type(repeat) is not int or repeat < 1):
        raise EvalError("models must be non-empty; passes and repeat must be positive integers")
    if len(models) != len(set(models)) or len(passes) != len(set(passes)):
        raise EvalError("duplicate models or pass counts in matrix")
    if max_total_cost is not None and not (0 < max_total_cost < float("inf")):
        raise EvalError("max-total-cost must be finite and positive")
    repo, output = repo.resolve(), output.resolve()
    external_output(output, repo)
    cases = select_cases(suite, selected or [])
    contexts = {case["id"]: case_context(repo, case, n) for n, case in enumerate(cases, 1)}
    base_cfg = replace(cfg, criteria_mode="defaults_only", criteria_manifest="")
    base_cfg.validate()
    # Refuse missing commits, empty/oversized diffs and invalid settings before spending anything.
    engine = Engine(base_cfg, runner)
    for ctx in contexts.values():
        engine.inputs.load_diff(ctx)
    frozen = environment(base_cfg)
    rubric = REPO_ROOT / "evals" / "RUBRIC.md"
    output.mkdir(parents=True, exist_ok=False)
    (output / "rubric.md").write_bytes(rubric.read_bytes())
    dump_json(output / "suite.json", suite)
    dump_json(output / "environment.json", frozen)
    trials = [
        {"id": "trial-%04d" % n, "case": case["id"], "model": model, "passes": count,
         "repeat": repetition, "status": "planned", "base": case["base"], "head": case["head"]}
        for n, (case, model, count, repetition) in enumerate(
            ((c, m, p, r) for c in cases for m in models for p in passes for r in range(1, repeat + 1)), 1)
    ]
    experiment: Dict[str, Any] = {
        "version": 1, "suite_hash": object_hash(suite), "mode": "run" if execute else "plan",
        "environment_hash": object_hash(frozen), "rubric_hash": file_hash(output / "rubric.md"),
        "criteria_mode": "defaults_only", "pass_policy": "fixed_attempts", "repo": str(repo),
        "max_total_cost_usd": max_total_cost, "trials": trials,
    }
    dump_json(output / "experiment.json", experiment)
    spent, halt = 0.0, ""
    for trial in trials:
        if halt:
            trial.update(status="skipped", error=halt)
            dump_json(output / "experiment.json", experiment)
            continue
        if environment(base_cfg) != frozen:
            halt = "code, prompts, criteria or rubric changed during the experiment"
        if execute and max_total_cost is not None and spent >= max_total_cost:
            halt = "experiment cost limit reached"
        if halt:
            trial.update(status="skipped", error=halt)
            dump_json(output / "experiment.json", experiment)
            continue
        trial_dir = output / trial["id"]
        trial_dir.mkdir()
        budget = cfg.run_budget_usd
        if execute and max_total_cost is not None:
            budget = min(budget, max_total_cost - spent)
        trial_cfg = replace(base_cfg, model_reviewer=trial["model"],
                            min_passes=trial["passes"], max_passes=trial["passes"],
                            run_budget_usd=budget, call_budget_usd=min(cfg.call_budget_usd, budget),
                            runs_dir=str(trial_dir / "runs"))
        trial_cfg.validate()
        engine = Engine(trial_cfg, runner)
        trial.update(run_budget_usd=budget, call_budget_usd=trial_cfg.call_budget_usd,
                     status="running" if execute else "planning", cost_known=not execute,
                     cost_usd_estimated=0.0)
        dump_json(output / "experiment.json", experiment)
        started = time.monotonic()
        try:
            ctx = contexts[trial["case"]]
            if not execute:
                dry = engine.dry_run(ctx)
                trial.update(status="planned", run=str(dry.run_dir.relative_to(output)),
                             changed_lines=dry.changed_lines)
            else:
                result = engine.review(ctx)
                trial.update(
                    status="complete" if result.passes_completed == trial["passes"] else "incomplete",
                    completed=result.passes_completed, stop_reason=result.stop_reason,
                    run=str(result.run_dir.relative_to(output)),
                    cost_usd_estimated=result.stats.total_cost(), cost_known=result.stats.cost_known(),
                    model_duration_s=result.stats.duration_s(), usage=result.stats.to_dict(),
                    pass_records=[p.to_dict() for p in result.passes],
                )
                trial["master_hash"] = object_hash(result.master.to_dict())
                # Omit model/pass/tier metadata from this optional human grading view.
                lines = ["# Findings for human judgment", "", "Case: " + trial["case"], ""]
                for item in result.master.scored():
                    finding = item["canonical"]
                    lines += ["## " + item["id"] + ": " + finding["title"], "",
                              "%s:%s" % (finding["file"], finding["line"]), "",
                              finding["description"], "", "```", finding.get("quote") or "", "```", "",
                              "Suggestion: " + (finding.get("suggestion") or ""), ""]
                (trial_dir / "findings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
                judgments = trial_dir / "judgments.json"
                dump_json(judgments, judgment_template(result.master.to_dict(), experiment["rubric_hash"]))
                trial["judgments"] = str(judgments.relative_to(output))
                if result.stop_reason == "cancelled":
                    trial["cost_known"] = False
                    halt = "experiment interrupted"
        except KeyboardInterrupt:
            trial.update(status="cancelled", error="experiment interrupted")
            halt = "experiment interrupted"
        except (EvalError, GitError, ReviewRefused, ClaudeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            trial.update(status="failed", error=str(exc))
        finally:
            trial["duration_s"] = time.monotonic() - started
            spent += trial["cost_usd_estimated"]
            # An exception can follow a charged call. Never pretend that trial cost is zero/known.
            if execute and max_total_cost is not None and not trial["cost_known"]:
                halt = "trial cost unknown; cannot safely account for remaining experiment budget"
            dump_json(output / "experiment.json", experiment)
    return experiment
