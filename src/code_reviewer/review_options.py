"""Options shared by `reviewer review` and the REPL's `review` command, so both accept and document the same things."""
from __future__ import annotations

import argparse
from dataclasses import replace
from typing import Any

from .config import Config, REVIEW_MODES


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
    parser.add_argument("--preflight", action="store_true", help="show production readiness without fetching code, running a model or reading credentials")
    parser.add_argument("--provider", choices=("claude", "openai"), help="inference backend; the same security gates apply to both")
    parser.add_argument("--parallel", type=positive_int, metavar="N", help="persona-only concurrent calls, 1 to 4 (default: persona_parallel, 4)")
    parser.add_argument("--mode", choices=REVIEW_MODES,
                        help="review flow (default: review_mode in review.toml, single-agent); agent-personas defaults to three passes per persona")
    parser.add_argument(
        "--passes", type=positive_int, metavar="N",
        help="single-agent: maximum number of review passes (default: max_passes in review.toml, 6), with early convergence stopping. "
             "agent-personas: passes per persona (default: persona_passes in review.toml, 3), with no quiet-pass stopping. "
             "Use --passes 1 for a single, cheap pass in single-agent or one pass per persona in agent-personas")
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
    if getattr(args, "provider", None):
        cfg.provider = args.provider
    if getattr(args, 'preflight', False) and getattr(args, 'dry_run', False):
        from .config import ConfigError
        raise ConfigError('--preflight cannot be combined with --dry-run')
    if getattr(args, "mode", None):
        cfg.review_mode = args.mode
    if getattr(args, 'parallel', None) is not None:
        from .config import ConfigError
        if cfg.review_mode != 'agent-personas':
            raise ConfigError('--parallel applies only to agent-personas')
        cfg.persona_parallel = args.parallel
    if args.passes:
        if cfg.review_mode == 'agent-personas':
            cfg.persona_passes = args.passes
        else:
            cfg.max_passes = args.passes
            cfg.min_passes = min(cfg.min_passes, args.passes)
    if args.no_self_check:
        cfg.self_check = False
    if args.model:
        cfg.model_reviewer = cfg.model_comparator = cfg.model_resolver = cfg.model_verifier = args.model
    cfg.validate()
    return cfg


# The options of `reviewer review`, one line each, for the banner it prints in a terminal. A test checks that every option
# of the command is listed here. (`--config` belongs to `reviewer` itself and goes before `review`.)
BANNER_OPTIONS = [
    ("--preflight", "show release blockers; no fetch, model call or credential access"),
    ("--provider NAME", "claude or openai; the same security gates apply"),
    ("--parallel N", "persona-only concurrent calls, 1 to 4 (default 4)"),
    ("--mode NAME", "single-agent (default); agent-personas defaults to three passes each"),
    ("--passes N", "single-agent maximum passes; agent-personas passes per persona"),
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
        "This run: %s · up to %d passes · model %s · self-check %s · emails %s · mode %s" % (
            pr, 4 * cfg.persona_passes if cfg.review_mode == "agent-personas" else cfg.max_passes, cfg.model_reviewer, "on" if cfg.self_check else "off",
            "redacted" if cfg.redact_emails else "NOT redacted", cfg.review_mode),
        "",
        "Options you can add to this command:",
    ]
    if cfg.review_mode == 'agent-personas':
        lines.insert(3, 'Persona schedule: %d passes per persona; no quiet-pass stopping.' % cfg.persona_passes)
    lines += ["  %s%s" % (flag.ljust(width), description) for flag, description in BANNER_OPTIONS]
    lines += [
        "",
        "When it finishes you get the review summary (the text --post would send), one line about the run and the path of the",
        "full report. To mark findings, drop one or post after reading the summary, use the interactive prompt instead:",
        "run `reviewer`, then `review <PR URL>`.",
        "",
    ]
    return "\n".join(lines)
