"""Interactive REPL: review a PR, inspect and label findings, and optionally post a summary comment."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import git_ops
from .claude_cli import ClaudeError
from .config import Config, ConfigError
from .engine import Engine, ReviewRefused, RunResult
from .master import confidence, tier
from .post import format_comment, post_comment
from .progress import ProgressPrinter
from .report import TIERS, render_finding, tiered
from .review_options import OptionError, ReplArgumentParser, add_run_options, apply_run_options

try:  # command history and line editing where available
    import readline  # noqa: F401
except ImportError:  # pragma: no cover
    pass

REVIEW_USAGE = "review PR [--passes N] [--model NAME] [--no-self-check] [--keep-checkout] [--verbose]"

HELP = """\
Commands:
  review <PR URL | owner/repo#n> [options]
      Review a pull request (several independent passes, then a merged report). Options:
        --passes N        maximum number of passes; use 1 for a single, cheap pass
        --model NAME      model for all roles, for example haiku or sonnet
        --no-self-check   skip the reviewer's self-refutation phase (cheaper, findings are not double-checked)
        --keep-checkout   keep the temporary checkout in the run directory, for debugging
        --verbose         print extra progress detail
      `review --help` shows the options in full.
  show <F-id>                 Show one finding in full.
  drop <F-id> / undrop <F-id> Exclude a finding from the posted comment (or restore it).
  mark <F-id> real|nit|wrong  Record your judgment of a finding (saved with the run).
  missed "<description>"      Record an issue you found that the agent missed (saved with the run).
  summary                     Reprint the summary of the last review.
  post [--tiers High,Medium]  Preview the PR comment and post it after you confirm.
  open                        Open the report file.
  help                        Show this help.
  quit                        Leave.
"""


def load_labels(run_dir: Path) -> Dict[str, Any]:
    path = run_dir / "labels.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {"marks": data.get("marks", {}), "dropped": data.get("dropped", []), "missed": data.get("missed", [])}
        except ValueError:
            pass
    return {"marks": {}, "dropped": [], "missed": []}


def save_labels(run_dir: Path, labels: Dict[str, Any]) -> None:
    (run_dir / "labels.json").write_text(json.dumps(labels, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class Session:
    def __init__(
        self,
        cfg: Config,
        runner: Any,
        local_path: Optional[Path] = None,
        out: Callable[[str], None] = print,
        ask: Callable[[str], str] = input,
    ):
        self.cfg = cfg
        self.runner = runner
        self.local_path = local_path or Path.cwd()
        self.out = out
        self.ask = ask
        self.result: Optional[RunResult] = None
        self.ref: Optional[git_ops.PRRef] = None
        self.labels: Dict[str, Any] = {"marks": {}, "dropped": [], "missed": []}

    # ------------------------------------------------------------------ main loop

    def loop(self) -> None:
        self.out("code-reviewer REPL. Type 'review <PR URL>' to start, 'help' for commands.")
        local = git_ops.local_repo_info(self.local_path)
        if local is not None:
            self.out("Detected repository: %s/%s (%s)" % (local.owner, local.repo, local.host))
        while True:
            try:
                line = self.ask("reviewer> ")
            except EOFError:
                self.out("")
                return
            except KeyboardInterrupt:
                self.out("")
                continue
            if not line.strip():
                continue
            try:
                if not self.dispatch(line):
                    return
            except KeyboardInterrupt:
                self.out("\ninterrupted")

    def dispatch(self, line: str) -> bool:
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            self.out("could not parse the command: %s" % exc)
            return True
        command, args = tokens[0].lower(), tokens[1:]
        handlers = {
            "review": self.cmd_review, "show": self.cmd_show, "drop": self.cmd_drop, "undrop": self.cmd_undrop,
            "mark": self.cmd_mark, "missed": self.cmd_missed, "summary": self.cmd_summary, "post": self.cmd_post,
            "open": self.cmd_open, "help": lambda a: self.out(HELP),
        }
        if command in ("quit", "exit", "q"):
            return False
        handler = handlers.get(command)
        if handler is None:
            self.out("unknown command '%s'. Type 'help'." % command)
        else:
            handler(args)
        return True

    # ------------------------------------------------------------------ commands

    def cmd_review(self, args: List[str]) -> None:
        parser = ReplArgumentParser(prog="review", add_help=False, usage=REVIEW_USAGE)
        parser.add_argument("pr", metavar="PR", help="pull request URL, or owner/repo#number")
        add_run_options(parser)
        if "-h" in args or "--help" in args:
            self.out(parser.format_help())
            return
        try:
            options = parser.parse_args(args)
        except OptionError as exc:
            self.out("error: %s\n%s\n(type `review --help` for the options)" % (exc, parser.format_usage().strip()))
            return
        try:
            ref = git_ops.parse_pr_ref(options.pr)
            cfg = apply_run_options(self.cfg, options)
        except (git_ops.GitError, ConfigError) as exc:
            self.out("error: %s" % exc)
            return

        local = git_ops.local_repo_info(self.local_path)
        if local is not None and not git_ops.same_repo(local, ref):
            answer = self.ask("This PR belongs to %s, but the current directory is %s/%s. Review anyway using a cached clone? [y/N] " % (
                ref.slug, local.owner, local.repo))
            if answer.strip().lower() not in ("y", "yes"):
                return

        engine = Engine(cfg, self.runner, emit=ProgressPrinter(verbose=options.verbose))
        try:
            ctx = engine.prepare(ref, self.local_path)
            result = engine.review(ctx, keep_checkout=options.keep_checkout)
        except (git_ops.GitError, ReviewRefused, ClaudeError) as exc:
            self.out("error: %s" % exc)
            return
        except KeyboardInterrupt:
            self.out("cancelled before any pass completed")
            return
        self.result, self.ref = result, ref
        self.labels = load_labels(result.run_dir)
        self.print_summary()

    def _need_result(self) -> Optional[RunResult]:
        if self.result is None:
            self.out("no review yet. Run 'review <PR URL>' first.")
        return self.result

    def _find(self, ident: str) -> Optional[Dict[str, Any]]:
        result = self._need_result()
        if result is None:
            return None
        item = result.master.items.get(ident.upper())
        if item is None:
            self.out("no finding %s" % ident)
        return item

    def cmd_show(self, args: List[str]) -> None:
        if not args:
            self.out("usage: show <F-id>")
            return
        item = self._find(args[0])
        if item is not None and self.result is not None:
            self.out(render_finding(item, self.result.passes_completed, self.result.criteria.index()))
            mark = self.labels["marks"].get(item["id"])
            if mark:
                self.out("Your mark: %s" % mark)

    def cmd_drop(self, args: List[str]) -> None:
        if not args or self._find(args[0]) is None:
            return
        ident = args[0].upper()
        if ident not in self.labels["dropped"]:
            self.labels["dropped"].append(ident)
            self._save()
        self.out("%s will be excluded from the posted comment" % ident)

    def cmd_undrop(self, args: List[str]) -> None:
        if not args:
            return
        ident = args[0].upper()
        if ident in self.labels["dropped"]:
            self.labels["dropped"].remove(ident)
            self._save()
        self.out("%s restored" % ident)

    def cmd_mark(self, args: List[str]) -> None:
        if len(args) != 2 or args[1].lower() not in ("real", "nit", "wrong"):
            self.out("usage: mark <F-id> real|nit|wrong")
            return
        if self._find(args[0]) is None:
            return
        self.labels["marks"][args[0].upper()] = args[1].lower()
        self._save()
        self.out("marked %s as %s" % (args[0].upper(), args[1].lower()))

    def cmd_missed(self, args: List[str]) -> None:
        if self._need_result() is None:
            return
        text = " ".join(args).strip()
        if not text:
            self.out('usage: missed "<description of an issue the agent missed>"')
            return
        self.labels["missed"].append(text)
        self._save()
        self.out("recorded (%d missed issue(s) so far)" % len(self.labels["missed"]))

    def cmd_summary(self, args: List[str]) -> None:
        if self._need_result() is not None:
            self.print_summary()

    def cmd_open(self, args: List[str]) -> None:
        result = self._need_result()
        if result is None:
            return
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        try:
            subprocess.run([opener, str(result.report_path)], check=False)
        except OSError:
            self.out(str(result.report_path))

    def cmd_post(self, args: List[str]) -> None:
        result = self._need_result()
        if result is None or self.ref is None:
            return
        tiers = list(self.cfg.post_tiers)
        if args[:1] == ["--tiers"] and len(args) > 1:
            tiers = [t.strip().capitalize() for t in args[1].split(",") if t.strip()]
            if any(t not in TIERS for t in tiers):
                self.out("tiers must be from: %s" % ", ".join(TIERS))
                return
        body = format_comment(result, tiers, self.labels["dropped"])
        self.out("----- comment preview -----")
        self.out(body)
        self.out("----- end of preview -----")
        answer = self.ask("Post to %s? [y/N] " % self.ref)
        if answer.strip().lower() not in ("y", "yes"):
            self.out("not posted")
            return
        try:
            url = post_comment(self.ref, body)
        except git_ops.GitError as exc:
            self.out("posting failed: %s" % exc)
            return
        self.out("posted: %s" % url)

    # ------------------------------------------------------------------ output

    def _save(self) -> None:
        if self.result is not None:
            save_labels(self.result.run_dir, self.labels)

    def print_summary(self) -> None:
        result = self.result
        if result is None:
            return
        cfg = result.cfg
        groups = tiered(result.master, result.passes_completed, cfg.tier_high, cfg.tier_medium)
        stats = result.stats
        self.out("")
        self.out("== %s: %s ==" % (result.ctx.label, result.ctx.title))
        self.out("passes completed: %d, stop reason: %s, estimated cost: $%.3f, tokens: %s" % (
            result.passes_completed, result.stop_reason, stats.total_cost(), "{:,}".format(stats.total_tokens())))
        self.out("findings: " + ", ".join("%s %d" % (t, len(groups[t])) for t in TIERS))
        for name in TIERS:
            for item in groups[name]:
                canon = item["canonical"]
                self.out("  %-6s %s  %-6s  %s:%s  %s  (%d/%d)" % (
                    name, item["id"], canon["severity"], canon["file"], canon["line"] or "-", canon["title"],
                    len(item["seen_in_passes"]), result.passes_completed))
        if result.master.rejected_only():
            self.out("  (%d finding(s) were rejected by self-check and are not scored)" % len(result.master.rejected_only()))
        self.out("report: %s" % result.report_path)
        self.out("next: show <id> | mark <id> real|nit|wrong | missed \"...\" | drop <id> | post | open")
        self.out("")
