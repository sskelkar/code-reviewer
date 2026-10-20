"""The steps the one-shot CLI and the interactive REPL share when you ask for a review.

Each frontend parses its own input and decides how to show results and errors; everything in between is here, so both
behave the same: pin the PR, then either show a dry run or run the review.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .shared.claude_cli import ClaudeError
from .config import Config, ConfigError
from .flows.single_agent.dry_run import DryRun
from .engine import Engine
from .shared.git_ops import GitError, PRRef
from .shared.models import ReviewRefused
from .flows.single_agent.models import RunResult
from .progress import ProgressPrinter

# What a review can fail with in an expected way. Frontends catch these and report them as an error message.
REVIEW_ERRORS = (GitError, ConfigError, ReviewRefused, ClaudeError)


@dataclass
class ReviewOutcome:
    """Exactly one of the two is set: `dry_run` for --dry-run, otherwise `result`."""

    dry_run: Optional[DryRun] = None
    result: Optional[RunResult] = None


def run_review(cfg: Config, runner: Any, ref: PRRef, options: Any, local_path: Path) -> ReviewOutcome:
    """Pin the PR, then show a dry run or run the review. `cfg` already has the command-line options applied;
    `options` supplies `verbose`, `dry_run` and `keep_checkout`."""
    engine = Engine(cfg, runner, emit=ProgressPrinter(verbose=options.verbose))
    ctx = engine.prepare(ref, local_path)
    if options.dry_run:
        return ReviewOutcome(dry_run=engine.dry_run(ctx))
    return ReviewOutcome(result=engine.review(ctx, keep_checkout=options.keep_checkout))
