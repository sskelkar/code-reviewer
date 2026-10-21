"""Interactive REPL: review a PR, inspect and label findings, and optionally post a summary comment."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .shared import git_ops
from .config import Config, ConfigError
from .presentation import render_dry_run
from .post import format_comment, post_comment
from .presentation import TIERS, render_finding
from .repl_help import HELP, REVIEW_USAGE, welcome_text
from .review_flow import REVIEW_ERRORS, run_review
from .review_options import OptionError, ReplArgumentParser, add_run_options, apply_run_options
from .presentation import render_summary, run_line

try:  # command history and line editing where available
    import readline  # noqa: F401
except ImportError:  # pragma: no cover
    pass

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
    # Command word -> the method that handles it. `quit` is handled separately (QUIT_WORDS).
    COMMANDS = {
        "review": "cmd_review", "show": "cmd_show", "drop": "cmd_drop", "undrop": "cmd_undrop", "mark": "cmd_mark",
        "missed": "cmd_missed", "summary": "cmd_summary", "post": "cmd_post", "open": "cmd_open", "help": "cmd_help",
    }
    QUIT_WORDS = ("quit", "exit", "q")

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
        self.result: Optional[Any] = None
        self.ref: Optional[git_ops.PRRef] = None
        self.labels: Dict[str, Any] = {"marks": {}, "dropped": [], "missed": []}

    # ------------------------------------------------------------------ main loop

    def loop(self) -> None:
        local = git_ops.local_repo_info(self.local_path)
        self.out(welcome_text("%s/%s (%s)" % (local.owner, local.repo, local.host) if local else None))
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
        if command in self.QUIT_WORDS:
            return False
        method = self.COMMANDS.get(command)
        if method is None:
            self.out("unknown command '%s'. Type 'help' to see the commands." % command)
        else:
            getattr(self, method)(args)
        return True

    def cmd_help(self, args: List[str]) -> None:
        self.out(HELP)

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

        try:
            outcome = run_review(cfg, self.runner, ref, options, self.local_path)
        except REVIEW_ERRORS as exc:
            self.out("error: %s" % exc)
            return
        except KeyboardInterrupt:
            self.out("cancelled before any pass completed")
            return
        if outcome.dry_run:
            self.out(render_dry_run(outcome.dry_run))
            return
        self.result, self.ref = outcome.result, ref
        self.labels = load_labels(outcome.result.run_dir)
        self.print_summary()

    def _need_result(self) -> Optional[Any]:
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
            self.out(render_finding(self.result, item))
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
        if self._need_result() is None:
            return
        tiers = self._tiers_option(args)
        if tiers is not None:
            self.print_summary(tiers)

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
        tiers = self._tiers_option(args)
        if tiers is None:
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

    def _tiers_option(self, args: List[str]) -> Optional[List[str]]:
        """The confidence tiers to show: `--tiers High,Medium` if given, otherwise the `post_tiers` setting. None if invalid."""
        if args[:1] == ["--tiers"] and len(args) > 1:
            tiers = [t.strip().capitalize() for t in args[1].split(",") if t.strip()]
            if not tiers or any(t not in TIERS for t in tiers):
                self.out("tiers must be from: %s" % ", ".join(TIERS))
                return None
            return tiers
        return list(self.cfg.post_tiers)

    def print_summary(self, tiers: Optional[List[str]] = None) -> None:
        """The shareable summary (exactly what `post` would send), then a line about the run and where the full report is."""
        result = self.result
        if result is None:
            return
        self.out("")
        self.out(render_summary(result, tiers or list(self.cfg.post_tiers), self.labels["dropped"]))
        self.out(run_line(result))
        self.out("full report: %s" % result.report_path)
        self.out('next: show <id> | mark <id> real|nit|wrong | missed "..." | drop <id> | post | open')
        self.out("")
