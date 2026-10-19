"""Local evals: init a pinned case, plan/run Claude reviews, label findings, grade.

    python -m code_reviewer.evals --help
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .claude_cli import ClaudeError, ClaudeRunner
from .config import ConfigError, load_config
from .eval_cases import (EvalError, external_output, git, load_suite, local_file, outside,
                         pinned_commit, read_object, object_hash, validate_suite)
from .eval_grading import load_experiment, render_evaluation, summarize_many, trial_documents
from .eval_judgments import CHECKS, LABELS, judgment_template, make_judgment, validate_judgments
from .eval_runner import run_matrix
from .git_ops import GitError
from .models import ReviewRefused
from .run_store import dump_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init", help="create a local case with pinned merge-base/head; no model calls")
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--base", required=True, help="local base ref; pinned to its merge-base with head")
    p.add_argument("--head", required=True, help="local head ref")
    p.add_argument("--output", type=Path, required=True, help="new suite JSON outside both repositories")
    p.add_argument("--id", default="case-1")
    p.add_argument("--title", required=True, help="neutral PR title, supplied to the Reviewer")
    p.add_argument("--description", required=True, help="neutral PR description; do not disclose expected bugs")
    for command in ("plan", "run"):
        p = sub.add_parser(command, help="no model calls" if command == "plan" else "make paid Claude review calls")
        p.add_argument("suite", type=Path)
        p.add_argument("--repo", type=Path, required=True, help="existing local clone; never fetched or modified")
        p.add_argument("--output", type=Path, required=True, help="new directory outside both repositories")
        p.add_argument("--models", nargs="+", default=["haiku"])
        p.add_argument("--passes", nargs="+", type=int, default=[1, 3])
        p.add_argument("--repeat", type=int, default=1)
        p.add_argument("--case", action="append", default=[])
        p.add_argument("--config", help="review settings; evals use defaults_only criteria and fixed attempts")
        p.add_argument("--max-total-cost", type=float, help="soft experiment USD cap; stops if cost becomes unknown")
    p = sub.add_parser("rekey", help="copy saved reviews with revised expectations and reset labels; no model calls")
    p.add_argument("experiment", type=Path)
    p.add_argument("suite", type=Path, help="same pinned cases and Reviewer metadata, revised expectations only")
    p.add_argument("--output", type=Path, required=True, help="new external experiment directory")
    p = sub.add_parser("label", help="record a human judgment locally; no model calls")
    p.add_argument("experiment", type=Path)
    p.add_argument("trial", help="trial-0001 etc.")
    p.add_argument("finding", help="F-001 etc.")
    p.add_argument("verdict", choices=LABELS)
    p.add_argument("--issue", default="", help="expected or newly assigned stable issue ID")
    p.add_argument("--duplicate-of", default="", help="original finding ID")
    p.add_argument("--reason", required=True, help="evidence supporting this judgment")
    p.add_argument("--fail", choices=CHECKS, default="", help="required for wrong")
    p.add_argument("--uncertain", choices=CHECKS, default="", help="required for uncertain")
    p = sub.add_parser("grade", help="score human labels; no model calls")
    p.add_argument("experiments", type=Path, nargs="+")
    p.add_argument("--output", type=Path, help="required when combining experiments; outside both repositories")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            repo, output = args.repo.expanduser().resolve(), args.output.expanduser().resolve()
            external_output(output, repo)
            head = pinned_commit(repo, args.head)
            base = git(repo, "merge-base", pinned_commit(repo, args.base), head)
            suite = validate_suite({"version": 1, "cases": [{
                "id": args.id, "base": base, "head": head, "title": args.title,
                "description": args.description, "expected": [], "expectation_status": "provisional",
            }]})
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(suite, indent=2) + "\n")
            print("Pinned case written to %s. Add/verify expectations locally before running if recall is needed." % output)
            return 0
        if args.command == "rekey":
            root, output = args.experiment.expanduser().resolve(), args.output.expanduser().resolve()
            experiment, original = load_experiment(root)
            external_output(output, Path(experiment["repo"]))
            if not outside(output, root):
                raise EvalError("rekey output must not be inside its source experiment")
            revised = load_suite(args.suite.expanduser().resolve())
            def without_answers(suite):
                return {**suite, "cases": [{k: v for k, v in c.items() if k not in
                        ("expected", "expectation_status", "expectation_reason")} for c in suite["cases"]]}
            if without_answers(original) != without_answers(revised):
                raise EvalError("rekey may only change expected, expectation_status and expectation_reason")
            shutil.copytree(root, output)
            experiment["suite_hash"] = object_hash(revised)
            experiment["rekeyed_from"] = str(root)
            dump_json(output / "suite.json", revised)
            dump_json(output / "experiment.json", experiment)
            for trial in experiment["trials"]:
                if "judgments" in trial:
                    master = read_object(local_file(output, trial["run"]) / "master.json")
                    dump_json(local_file(output, trial["judgments"]),
                              judgment_template(master, experiment["rubric_hash"]))
            for name in ("evaluation.json", "evaluation.md"):
                (output / name).unlink(missing_ok=True)
            print("Saved reviews copied; all human labels reset. Regrade every compared configuration in %s." % output)
            return 0
        if args.command == "label":
            root = args.experiment.expanduser().resolve()
            experiment, suite = load_experiment(root)
            external_output(root, Path(experiment["repo"]))
            trial = next((t for t in experiment["trials"] if t["id"] == args.trial), None)
            if trial is None:
                raise EvalError("unknown trial")
            case = next(c for c in suite["cases"] if c["id"] == trial["case"])
            master, document = trial_documents(root, experiment, trial, case)
            if args.finding not in document["findings"]:
                raise EvalError("unknown scored finding ID")
            document["findings"][args.finding] = make_judgment(
                args.verdict, args.issue, args.reason, args.duplicate_of, args.fail, args.uncertain)
            validate_judgments(document, master, case, experiment["rubric_hash"])
            dump_json(local_file(root, trial["judgments"]), document)
            print("Saved %s / %s judgment." % (args.trial, args.finding))
            return 0
        if args.command == "grade":
            roots = [p.expanduser().resolve() for p in args.experiments]
            if len(set(roots)) != len(roots):
                raise EvalError("duplicate experiment paths")
            if len(roots) > 1 and args.output is None:
                raise EvalError("combining experiments requires --output")
            loaded = [(*load_experiment(root), root) for root in roots]
            output = args.output.expanduser().resolve() if args.output else roots[0]
            for experiment, suite, root in loaded:
                external_output(root, Path(experiment["repo"]))
                external_output(output, Path(experiment["repo"]))
            summary = summarize_many(loaded)
            output.mkdir(parents=True, exist_ok=True)
            dump_json(output / "evaluation.json", summary)
            report = render_evaluation(summary)
            (output / "evaluation.md").write_text(report, encoding="utf-8")
            print(report)
            return 0
        suite_path, repo = args.suite.expanduser().resolve(), args.repo.expanduser().resolve()
        if not outside(suite_path, repo):
            raise EvalError("keep suite answers outside the reviewed repository")
        suite = load_suite(suite_path)
        cfg = load_config(args.config)
        runner = ClaudeRunner(cfg.claude_bin, cfg.isolation_flags)
        experiment = run_matrix(suite, repo, args.output.expanduser(), cfg, runner,
                                args.models, args.passes, args.repeat, args.case, args.command == "run",
                                args.max_total_cost)
        print("%d trials recorded in %s" % (len(experiment["trials"]), args.output / "experiment.json"))
        return 1 if any(t["status"] in ("failed", "incomplete", "cancelled", "skipped")
                        for t in experiment["trials"]) else 0
    except (EvalError, GitError, ConfigError, ReviewRefused, ClaudeError, OSError, ValueError,
            subprocess.TimeoutExpired) as exc:
        print("eval error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
