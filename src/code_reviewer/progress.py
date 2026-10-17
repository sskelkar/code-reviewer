"""Turn engine events into brief, human-readable status lines."""
from __future__ import annotations

import sys
import time
from typing import Callable, Optional, TextIO

from .events import Event


def _fmt_money(value: float) -> str:
    return "$%.2f" % value


class ProgressPrinter:
    """Prints one line per stage. Instances are callable and can be used as an engine event sink."""

    def __init__(self, stream: Optional[TextIO] = None, verbose: bool = False):
        self.stream = stream or sys.stdout
        self.verbose = verbose
        self.started = time.time()

    def _say(self, text: str) -> None:
        elapsed = int(time.time() - self.started)
        print("[%02d:%02d] %s" % (elapsed // 60, elapsed % 60, text), file=self.stream, flush=True)

    def __call__(self, event: Event) -> None:
        d = event.data
        kind = event.type
        if kind == "prepare_started":
            self._say("fetching %s ..." % d["ref"])
        elif kind == "prepare_note":
            self._say("note: %s" % d["message"])
        elif kind == "run_started":
            self._say("reviewing %s#%d at %s: %s" % (d["repo"], d["number"], d["head"][:10], d["title"]))
        elif kind == "diff_ready":
            self._say("diff: %d files, %d changed lines (%d excluded)" % (d["files"], d["changed_lines"], d["excluded"]))
        elif kind == "checkout_ready":
            self._say("read-only checkout ready (%d files, %d symlinks skipped)" % (d["files"], d["skipped_links"]))
        elif kind == "resolver_started":
            self._say("resolving project criteria from AGENTS.md (%d candidate files) ..." % d["candidates"])
        elif kind == "criteria_resolved":
            self._say("criteria: %d default + %d project rules from %d files, %d ignored references" % (
                d["default_rules"], d["project_rules"], d["project_files"], d["ignored"]))
        elif kind == "warning":
            self._say("warning: %s" % d["message"])
        elif kind == "pass_started":
            self._say("pass %d/%d ..." % (d["pass_no"], d["max_passes"]))
        elif kind == "reviewer_started" and self.verbose:
            self._say("  reviewer (%s) is reading the PR" % d["model"])
        elif kind == "reviewer_done":
            self._say("  reviewer: %d findings (%d usable, %d ungrounded, %d invalid rule, %d self-rejected)" % (
                d["raised"], d["usable"], d["ungrounded"], d["invalid_rule"], d["rejected"]))
        elif kind == "comparator_started" and self.verbose:
            self._say("  comparing %d new findings with %d candidates" % (d["new"], d["candidates"]))
        elif kind == "pass_finished":
            self._say("pass %d: %d new, %d matched, %d ungrounded · %s · quiet streak %d/%d" % (
                d["pass_no"], d["new"], d["matched"], d["ungrounded"], _fmt_money(d["cost"]), d["quiet_streak"], d["quiet_k"]))
        elif kind == "pass_failed":
            self._say("pass %d FAILED: %s" % (d["pass_no"], d["error"]))
        elif kind == "converged":
            self._say("converged after %d passes" % d["passes"])
        elif kind == "report_ready":
            self._say("done (%s)" % d["stop_reason"])
