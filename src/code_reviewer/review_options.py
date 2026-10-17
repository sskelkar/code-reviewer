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
        help="keep the temporary read-only checkout inside the run directory, for debugging")
    parser.add_argument(
        "--verbose", action="store_true",
        help="print extra progress detail while the review runs")


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
