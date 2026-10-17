"""Command line entry point: `reviewer` (REPL), `reviewer review <url>`, `reviewer doctor`."""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from . import git_ops
from .claude_cli import ClaudeError, ClaudeRunner
from .config import ConfigError, load_config
from .doctor import run_doctor
from .engine import Engine, ReviewRefused
from .post import format_comment, post_comment
from .progress import ProgressPrinter
from .repl import Session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reviewer", description="Multi-pass pull request review agent.")
    parser.add_argument("--config", help="path to a review.toml (default: ./review.toml or ~/.code-reviewer/review.toml)")
    sub = parser.add_subparsers(dest="command")

    review = sub.add_parser("review", help="review a pull request without the interactive prompt")
    review.add_argument("pr", help="PR URL or owner/repo#number")
    review.add_argument("--post", action="store_true", help="post the summary comment after the review (explicit opt-in)")
    review.add_argument("--passes", type=int, help="maximum number of passes")
    review.add_argument("--model", help="model alias for all roles (for example haiku or sonnet)")
    review.add_argument("--no-self-check", action="store_true")
    review.add_argument("--keep-checkout", action="store_true")
    review.add_argument("--verbose", action="store_true")
    review.add_argument("--local-path", help="local clone to use as the object store (default: current directory)")

    doctor = sub.add_parser("doctor", help="check the environment and the headless Claude Code setup")
    doctor.add_argument("--live", action="store_true", help="also make one small real call to test isolation")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print("config error: %s" % exc, file=sys.stderr)
        return 2
    runner = ClaudeRunner(cfg.claude_bin, cfg.isolation_flags)

    if args.command == "doctor":
        return run_doctor(cfg, runner, live=args.live)

    if args.command == "review":
        try:
            ref = git_ops.parse_pr_ref(args.pr)
            cfg = replace(cfg)
            if args.passes:
                cfg.max_passes = args.passes
                cfg.min_passes = min(cfg.min_passes, args.passes)
            if args.no_self_check:
                cfg.self_check = False
            if args.model:
                cfg.model_reviewer = cfg.model_comparator = cfg.model_resolver = args.model
            cfg.validate()
            engine = Engine(cfg, runner, emit=ProgressPrinter(verbose=args.verbose))
            local = Path(args.local_path) if args.local_path else Path.cwd()
            ctx = engine.prepare(ref, local)
            result = engine.review(ctx, keep_checkout=args.keep_checkout)
        except (git_ops.GitError, ConfigError, ReviewRefused, ClaudeError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1
        print("report: %s" % result.report_path)
        if args.post:
            body = format_comment(result, cfg.post_tiers, [])
            try:
                print("posted: %s" % post_comment(ref, body))
            except git_ops.GitError as exc:
                print("posting failed: %s" % exc, file=sys.stderr)
                return 1
        return 0 if result.stop_reason in ("converged", "max_passes", "cancelled", "budget_exceeded") else 1

    Session(cfg, runner).loop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
