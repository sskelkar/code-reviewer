"""Command line entry point: `reviewer` (REPL), `reviewer review <url>`, `reviewer doctor`."""
from __future__ import annotations

import argparse
import sys
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
from .review_options import add_run_options, apply_run_options
from .summary import render_summary, run_line

MAIN_EPILOG = """\
examples:
  reviewer                                start the interactive prompt (run it inside the project you want to review)
  reviewer review <PR URL> --passes 1     one cheap pass over a pull request
  reviewer review <PR URL>                a full review (several passes)
  reviewer doctor --live                  check the environment with one small real call

Each subcommand has its own options, for example: reviewer review --help
"""

REVIEW_EPILOG = """\
examples:
  reviewer review https://github.com/OWNER/REPO/pull/123 --passes 1
  reviewer review OWNER/REPO#123 --model sonnet
  reviewer review https://github.com/OWNER/REPO/pull/123 --post

The review never posts to GitHub unless you pass --post. In the interactive prompt (`reviewer`), use `post` instead.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reviewer",
        description="Multi-pass pull request review agent. Run without a subcommand to start the interactive prompt.",
        epilog=MAIN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", help="path to a review.toml (default: ./review.toml or ~/.code-reviewer/review.toml)")
    sub = parser.add_subparsers(dest="command", metavar="{review,doctor}")

    review = sub.add_parser(
        "review", help="review a pull request without the interactive prompt",
        description="Review a pull request: several independent passes, then a merged report with findings, tokens and cost.",
        epilog=REVIEW_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    review.add_argument("pr", metavar="PR", help="pull request URL, or owner/repo#number")
    add_run_options(review)
    review.add_argument("--post", action="store_true",
                        help="post the summary comment to the pull request after the review (explicit opt-in; nothing is posted without it)")
    review.add_argument("--local-path", metavar="DIR",
                        help="local clone to use as the git object store (default: the current directory, if it is a clone of the same repository)")

    doctor = sub.add_parser("doctor", help="check the environment and the headless Claude Code setup")
    doctor.add_argument("--live", action="store_true", help="also make one small real call (a few cents) to test isolation")
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
            cfg = apply_run_options(cfg, args)
            engine = Engine(cfg, runner, emit=ProgressPrinter(verbose=args.verbose))
            local = Path(args.local_path) if args.local_path else Path.cwd()
            ctx = engine.prepare(ref, local)
            result = engine.review(ctx, keep_checkout=args.keep_checkout)
        except (git_ops.GitError, ConfigError, ReviewRefused, ClaudeError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1
        print("\n" + render_summary(result, cfg.post_tiers, []))
        print(run_line(result))
        print("full report: %s" % result.report_path)
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
