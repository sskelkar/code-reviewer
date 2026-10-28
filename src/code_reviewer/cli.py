"""One-shot review and deployment commands; the interactive frontend is parked."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .shared import git_ops
from .config import ConfigError, load_config
from .presentation import render_dry_run
from .post import format_comment, post_comment
from .review_flow import REVIEW_ERRORS, run_review
from .review_options import add_run_options, apply_run_options, review_banner
from .presentation import render_summary, run_line

MAIN_EPILOG = """\
examples:
  reviewer                                show help (the interactive prompt is parked)
  reviewer review <PR URL> --passes 1     one cheap pass over a pull request
  reviewer review <PR URL> --dry-run      show what the reviewer would be given, without calling the model
  reviewer review <PR URL>                a full review (several passes)
  reviewer review <PR URL> --preflight    list production release blockers; no network or model calls

Each subcommand has its own options, for example: reviewer review --help
"""

REVIEW_EPILOG = """\
examples:
  reviewer review https://github.com/OWNER/REPO/pull/123 --passes 1
  reviewer review OWNER/REPO#123 --model sonnet
  reviewer review OWNER/REPO#123 --provider openai --model MODEL_ID --preflight

The review never posts to GitHub unless you pass --post.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reviewer",
        description="Multi-pass pull request review agent. The interactive prompt is parked; use reviewer review.",
        epilog=MAIN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", help="path to a review.toml (default: ~/.code-reviewer/review.toml; must be outside Git working trees)")
    sub = parser.add_subparsers(dest="command", metavar="{review,doctor,sandbox}")

    review = sub.add_parser(
        "review", help="review a pull request with the one-shot CLI",
        description="Review a pull request: several independent passes, then a merged report with findings, tokens and cost.",
        epilog=REVIEW_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    review.add_argument("pr", metavar="PR", help="pull request URL, or owner/repo#number")
    add_run_options(review)
    review.add_argument("--post", action="store_true",
                        help="post the summary comment to the pull request after the review (explicit opt-in; nothing is posted without it)")
    review.add_argument("--local-path", metavar="DIR",
                        help="local clone to use as the git object store (default: the current directory, if it is a clone of the same repository)")

    doctor = sub.add_parser("doctor", help="show offline execution status without commands or credentials")
    doctor.add_argument("--live", action="store_true", help="legacy live check; blocked until the production backend is qualified")
    sandbox = sub.add_parser('sandbox', help='run the supported sandbox deployment checks')
    checks = sandbox.add_subparsers(dest='sandbox_command', required=True)
    check = checks.add_parser('check', help='build and check Firecracker in a dedicated Lima worker; no key or inference')
    check.add_argument('--artifacts', required=True, type=Path,
                       help='operator directory containing verified firecracker.tgz and vmlinux-6.1.188')
    check.add_argument('--lima-home', required=True, type=Path, help='dedicated Lima state directory')
    check.add_argument('--instance', default='reviewer-sandbox', help='prepared dedicated worker (must be stopped)')
    check.add_argument('--output', type=Path, help='new private result directory (default: a random temporary directory)')
    check.add_argument('--lifecycle', action='store_true', help='also test cancellation and supervisor SIGKILL/deadline (adds about 30 seconds)')
    check.add_argument('--pr', help='also test public PR acquisition inside the VM and source-access revocation; no key or inference')
    recover = checks.add_parser('recover', help='restart the dedicated worker and remove abandoned reviewer job state')
    recover.add_argument('--lima-home', required=True, type=Path, help='dedicated Lima state directory')
    recover.add_argument('--instance', default='reviewer-sandbox', help='dedicated worker; refuses an active launcher lock')
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == 'sandbox':
        # Deployment checks accept no target config, PR, provider credential or model settings.
        from .sandbox.check import cli as sandbox_check
        return sandbox_check(args)
    if args.command == "review" and args.post and (args.dry_run or args.preflight):
        parser.error("--dry-run/--preflight cannot be combined with --post")
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print("config error: %s" % exc, file=sys.stderr)
        return 2
    if args.command == "doctor":
        from .security import readiness
        report = readiness(cfg.provider, sandbox=cfg.sandbox)
        from .backends.factory import configuration_errors
        report['configuration_errors'] = configuration_errors(cfg)
        if cfg.sandbox:
            from .sandbox.review import configuration_errors as vm_errors
            report['configuration_errors'] += vm_errors(cfg)
            report['qualified'] = report['ready']
            report['qualification_blockers'] = report['blockers']
            report['ready'] = not report['configuration_errors']
            report['blockers'] = [] if report['ready'] else ['vm-configuration']
        if report['configuration_errors']:
            report['ready'] = False
            report['blockers'].append('model-configuration')
        report['diagnostic'] = 'offline release status; no external executable or model was started'
        if args.live:
            report['diagnostic'] = 'live sandbox diagnostic is not integrated; no host fallback'
        print(json.dumps(report, indent=2))
        return 0 if report['ready'] and not args.live else 1

    if args.command == "review":
        try:
            ref = git_ops.parse_pr_ref(args.pr)
            cfg = apply_run_options(cfg, args)
            if sys.stdout.isatty() and not args.preflight:  # readiness output stays JSON even in a terminal
                print(review_banner(str(ref), cfg))
            local = Path(args.local_path) if args.local_path else Path.cwd()
            outcome = run_review(cfg, None, ref, args, local)
        except REVIEW_ERRORS as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1
        if outcome.preflight:
            print(json.dumps(outcome.preflight, indent=2))
            return 0 if outcome.preflight['ready'] else 1
        if outcome.dry_run:
            print(render_dry_run(outcome.dry_run))
            return 0
        result = outcome.result
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
        return 0 if result.stop_reason in ("converged", "max_passes", "completed", "cancelled", "budget_exceeded") else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
