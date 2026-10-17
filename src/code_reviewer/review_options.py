"""Options shared by `reviewer review` and the REPL's `review` command, so both accept and document the same things."""
from __future__ import annotations

import argparse
from dataclasses import replace
from typing import Any

from .config import Config


class OptionError(ValueError):
    """A bad command-line option typed into the REPL (raised instead of exiting the process)."""


class ReplArgumentParser(argparse.ArgumentParser):
    """An argparse parser that raises OptionError on bad input instead of printing usage and exiting."""

    def error(self, message: str) -> Any:
        raise OptionError(message)


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("%r is not a whole number" % text) from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def add_run_options(parser: argparse.ArgumentParser) -> None:
    """Options that change how a review runs. Both frontends add exactly these."""
    parser.add_argument(
        "--passes", type=positive_int, metavar="N",
        help="maximum number of review passes (default: max_passes in review.toml, 6). The review can stop earlier once "
             "passes stop finding anything new. Use --passes 1 for a single, cheap pass")
    parser.add_argument(
        "--model", metavar="NAME",
        help="model for all roles, an alias or full name such as haiku or sonnet (default: the model_* settings in review.toml)")
    parser.add_argument(
        "--no-self-check", action="store_true",
        help="skip the phase where the reviewer tries to refute its own findings (cheaper, but findings are not double-checked)")
    parser.add_argument(
        "--keep-checkout", action="store_true",
        help="keep the temporary read-only checkout (its location is printed) instead of deleting it, for debugging")
    parser.add_argument(
        "--verbose", action="store_true",
        help="print extra progress detail while the review runs")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="show exactly what the reviewer would be given (PR text, criteria, tools, command) and stop: "
             "no model is called, nothing is posted and no checkout is made")


def apply_run_options(cfg: Config, args: Any) -> Config:
    """Return a copy of `cfg` with the command-line overrides applied and validated. The original is untouched."""
    cfg = replace(cfg)
    if args.passes:
        cfg.max_passes = args.passes
        cfg.min_passes = min(cfg.min_passes, args.passes)
    if args.no_self_check:
        cfg.self_check = False
    if args.model:
        cfg.model_reviewer = cfg.model_comparator = cfg.model_resolver = args.model
    cfg.validate()
    return cfg


# The options of `reviewer review`, one line each, for the banner it prints in a terminal. A test checks that every option
# of the command is listed here. (`--config` belongs to `reviewer` itself and goes before `review`.)
BANNER_OPTIONS = [
    ("--passes N", "maximum number of passes; --passes 1 is one cheap pass"),
    ("--model NAME", "model for all roles, for example haiku or sonnet"),
    ("--no-self-check", "skip the reviewer's self-refutation phase (cheaper)"),
    ("--keep-checkout", "keep the temporary checkout (its location is printed)"),
    ("--verbose", "print extra progress detail"),
    ("--dry-run", "show exactly what the reviewer would be given; no model call"),
    ("--post", "post the summary to the PR when done (nothing is posted without it)"),
    ("--local-path DIR", "local clone to use as the git object store (default: the current directory)"),
    ("--config PATH", "settings file to use; goes before the command: reviewer --config PATH review ..."),
]


def review_banner(pr: str, cfg: Config) -> str:
    """The banner `reviewer review` prints at the start in a terminal: what this run will do and the options you can add."""
    width = max(len(flag) for flag, _ in BANNER_OPTIONS) + 2
    lines = [
        "reviewer review: multi-pass pull request review",
        "",
        "This run: %s · up to %d passes · model %s · self-check %s · emails %s" % (
            pr, cfg.max_passes, cfg.model_reviewer, "on" if cfg.self_check else "off",
            "redacted" if cfg.redact_emails else "NOT redacted"),
        "",
        "Options you can add to this command:",
    ]
    lines += ["  %s%s" % (flag.ljust(width), description) for flag, description in BANNER_OPTIONS]
    lines += [
        "",
        "When it finishes you get the review summary (the text --post would send), one line about the run and the path of the",
        "full report. To mark findings, drop one or post after reading the summary, use the interactive prompt instead:",
        "run `reviewer`, then `review <PR URL>`.",
        "",
    ]
    return "\n".join(lines)
