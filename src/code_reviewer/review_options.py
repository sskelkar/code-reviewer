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
    parser.add_argument("--preflight", action="store_true", help="check configuration without fetching code, running a model or reading credentials")
    parser.add_argument("--provider", choices=("claude", "openai"), help="runtime inference adapter; independent of host/VM execution")
    parser.add_argument('--no-sandbox', action='store_true', help='explicit trusted direct-host acquisition/review, without starting a VM')
    parser.add_argument("--parallel", type=positive_int, metavar="N", help="concurrent reviewers, 1 to 4 (default: persona_parallel, 4)")
    parser.add_argument(
        "--passes", type=positive_int, metavar="N",
        help="passes per persona (default: persona_passes in review.toml, 3), with fixed rounds. "
             "Use --passes 1 for one review from each perspective")
    parser.add_argument(
        "--model", metavar="NAME",
        help="model for all roles, an alias or full name such as haiku or gpt-6-luna (default: model_* settings, otherwise haiku for Claude or gpt-6-luna for OpenAI)")
    parser.add_argument(
        "--no-self-check", action="store_true",
        help="skip the phase where the reviewer tries to refute its own findings (cheaper, but findings are not double-checked)")
    parser.add_argument(
        "--keep-checkout", action="store_true",
        help="host-only: keep the separate temporary checkout (its location is printed); model tools remain read-only")
    parser.add_argument(
        "--verbose", action="store_true",
        help="print extra progress detail while the review runs")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="preview PR text and save default-criteria prompts for all four reviewers, then stop: "
             "no model is called, nothing is posted and no checkout is made")


def apply_run_options(cfg: Config, args: Any) -> Config:
    """Return a copy of `cfg` with the command-line overrides applied and validated. The original is untouched."""
    cfg = replace(cfg)
    if getattr(args, 'no_sandbox', False):
        cfg.sandbox = False
    if getattr(args, 'auth', None):
        cfg.auth_mode = args.auth
    if getattr(args, "provider", None):
        cfg.provider = args.provider
    if getattr(args, 'preflight', False) and getattr(args, 'dry_run', False):
        from .config import ConfigError
        raise ConfigError('--preflight cannot be combined with --dry-run')
    if getattr(args, 'parallel', None) is not None:
        cfg.persona_parallel = args.parallel
    if args.passes:
        cfg.persona_passes = args.passes
    if args.no_self_check:
        cfg.self_check = False
    if args.model:
        cfg.model_reviewer = cfg.model_comparator = cfg.model_resolver = cfg.model_verifier = args.model
    cfg.validate()
    return cfg


# The options of `reviewer review`, one line each, for the banner it prints in a terminal. A test checks that every option
# of the command is listed here. (`--config` belongs to `reviewer` itself and goes before `review`.)
BANNER_OPTIONS = [
    ("--account LABEL", "saved ChatGPT account label for subscription access"),
    ("--auth NAME", "api-key or subscription; no automatic billing fallback"),
    ("--preflight", "check configuration; no fetch, model call or credential access"),
    ("--provider NAME", "runtime adapter: claude or openai, independent of execution location"),
    ('--no-sandbox', 'explicit direct-host execution; default is VM, with no automatic fallback'),
    ("--parallel N", "concurrent reviewers, 1 to 4 (default 4)"),
    ("--passes N", "passes per persona (default 3)"),
    ("--model NAME", "model for all roles; defaults: Claude haiku, OpenAI gpt-6-luna"),
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
        "reviewer review: multi-perspective pull request review",
        "",
        "This run: %s · %d scheduled reviews · model %s · self-check %s · emails %s" % (
            pr, 4 * cfg.persona_passes, cfg.model_reviewer, "on" if cfg.self_check else "off",
            "redacted" if cfg.redact_emails else "NOT redacted"),
        "",
        "Options you can add to this command:",
    ]
    lines.insert(3, 'Schedule: %d passes per persona; fixed rounds.' % cfg.persona_passes)
    lines.insert(3, 'Execution: %s · provider %s · auth %s' % ('VM' if cfg.sandbox else 'direct host (explicitly selected)', cfg.provider, cfg.auth_mode))
    lines += ["  %s%s" % (flag.ljust(width), description) for flag, description in BANNER_OPTIONS]
    lines += [
        "",
        "When it finishes you get the review summary (the text --post would send), one line about the run and the path of the",
        "full report. Inspect that file locally; the interactive prompt and its mark/drop commands are parked.",
        "",
    ]
    return "\n".join(lines)
