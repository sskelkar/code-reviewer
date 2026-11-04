"""Turn engine events into brief, human-readable status lines."""
from __future__ import annotations

import sys
import time
import threading
import os
import shutil
from typing import Optional, TextIO

from .shared.events import Event


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
        elif kind == "pii_redacted":
            self._say("replaced %d email address%s in the PR text with [email] before sending it" % (d["emails"], "" if d["emails"] == 1 else "es"))
        elif kind == "diff_ready":
            self._say("diff: %d files, %d changed lines (%d excluded)" % (d["files"], d["changed_lines"], d["excluded"]))
        elif kind == "checkout_ready":
            self._say("checkout ready (%d files, %d symlinks skipped); model tools are read-only%s" % (
                d["files"], d["skipped_links"], "; kept at %s" % d["path"] if d.get("kept") else ""))
        elif kind == "resolver_started":
            self._say("resolving project criteria from AGENTS.md (%d candidate files) ..." % d["candidates"])
        elif kind == "criteria_resolved":
            self._say("criteria: %d default + %d project rules from %d files, %d ignored references" % (
                d["default_rules"], d["project_rules"], d["project_files"], d["ignored"]))
        elif kind == 'classification_started':
            self._say('distributing %d project rules among four personas ...' % d['rules'])
        elif kind == 'budget_allocated':
            self._say('budget allocation: ' + ', '.join(('%s %d tokens' % (p, v['allocated_tokens'])) if 'allocated_tokens' in v else ('%s $%.2f' % (p, v['allocated_usd'])) for p, v in d['pools'].items()))
        elif kind == "warning":
            self._say("warning: %s" % d["message"])
        elif kind == "persona_pass_started":
            self._say("%s: round %d/%d (slot %d/%d) ..." % (
                d["persona"], d["round_no"], d["rounds"], d["pass_no"], d["scheduled"]))
        elif kind == "persona_pass_finished":
            self._say("%s: round %d completed, %d usable findings · %s" % (d["persona"], d["round_no"], d["usable"], _fmt_money(d["cost"])))
        elif kind == "verification_started":
            self._say("verifying candidate group %s ..." % d["conflict"])
        elif kind == "reviewer_started" and self.verbose:
            self._say("  reviewer (%s) is reading the PR" % d["model"])
        elif kind == "reviewer_done":
            self._say("  reviewer: %d findings (%d usable, %d ungrounded, %d self-rejected)%s" % (
                d["raised"], d["usable"], d["ungrounded"], d["rejected"],
                ("; %d rule citations fixed" % d["rule_fixed"] if d["rule_fixed"] else "")
                + ("; %d in excluded files, dropped" % d["out_of_scope"] if d.get("out_of_scope") else "")))
        elif kind == "comparator_started" and self.verbose:
            self._say("  comparing %d new findings with %d candidates" % (d["new"], d["candidates"]))
        elif kind == "pass_failed":
            self._say("pass %d FAILED: %s" % (d["pass_no"], d["error"]))
        elif kind == "report_ready":
            self._say("done (%s)" % d["stop_reason"])
        elif kind == 'persona_review_finished':
            persona, number = d['persona'], d['round_no']
            if d.get('ok', True):
                self.reviewer_glimpse(persona, number, d)
            else:
                self._say('%s: review %d failed' % (persona, number))
        elif kind == 'round_preview':
            self._say('%s %d provisional: %d completed, %d incomplete; candidates: %d new, %d total. '
                      'UNCONFIRMED — not independently verified; see the final report.' % (
                          'Round', d['round'], d['completed'], d['failed'], d['new'], d['total']))

    def reviewer_glimpse(self, persona, number, data):
        self._say('%s: review %d returned — %d proposed, %d self-rejected' % (
            persona, number, data['raised'], data['self_rejected']))
        self._say('  Verification pending; candidate details are withheld until supported.')


class LiveProgress:
    """Timestamped status, TTY-only activity and heartbeats for one VM request."""
    def __init__(self, stream=None, interval=15):
        self.printer = ProgressPrinter(stream=stream or sys.stderr)
        self.interval = interval
        self.lock, self.stop = threading.RLock(), threading.Event()
        self.state = 'preparing review'
        self.active = set()
        self.updated = time.monotonic()
        self.animated = bool(getattr(self.printer.stream, 'isatty', lambda: False)() and os.environ.get('TERM') != 'dumb')
        self.visible, self.frame = False, 0
        self.thread = threading.Thread(target=self._watch, name='reviewer-progress', daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()
        with self.lock:
            self._clear()

    def _clear(self):
        if self.visible:
            self.printer.stream.write('\r\x1b[2K')
            self.printer.stream.flush()
            self.visible = False

    def _say(self, text):
        self._clear()
        self.updated = time.monotonic()
        self.printer._say(text)

    def note(self, text):
        with self.lock:
            self._say(text)

    def stage(self, name):
        from .sandbox.progress import STAGES
        with self.lock:
            if self.state == STAGES[name]:
                return
            self.state = STAGES[name]
            self._say(self.state)

    def __call__(self, frame):
        with self.lock:
            event, d = frame['event'], frame['data']
            if event == 'stage':
                self.stage(d['stage'])
            elif event == 'review_started':
                self.active.add(d['persona'])
                self.state = 'waiting for reviewer results: ' + ', '.join(sorted(self.active))
                self._say('%s: review %d scheduled' % (d['persona'], d['round']))
            elif event == 'review_finished':
                self.active.discard(d['persona'])
                self.state = ('waiting for reviewers: ' + ', '.join(sorted(self.active))) if self.active else 'checking evidence and consolidating findings'
                self._clear()
                self.updated = time.monotonic()
                if d['ok'] and 'raised' in d:
                    self.printer.reviewer_glimpse(d['persona'], d['round'], d)
                else:
                    self._say('%s: review %d %s' % (d['persona'], d['round'], 'returned' if d['ok'] else 'failed'))
            elif event == 'comparison_started':
                self.state = 'consolidating %s findings from review %d' % (d['persona'], d['round'])
                self._say(self.state)
            elif event == 'round_preview':
                self._clear()
                self.updated = time.monotonic()
                self.printer(Event(event, d))

    def tick(self):
        with self.lock:
            if time.monotonic() - self.updated >= self.interval:
                self._say('still waiting — ' + self.state)
            if self.animated:
                elapsed = int(time.time() - self.printer.started)
                text = '[%02d:%02d] %s %s' % (elapsed // 60, elapsed % 60, '|/-\\'[self.frame % 4], self.state)
                self.frame += 1
                width = max(0, shutil.get_terminal_size(fallback=(80, 24)).columns - 1)
                self.printer.stream.write('\r\x1b[2K' + text[:width])
                self.printer.stream.flush()
                self.visible = True

    def _watch(self):
        while not self.stop.wait(.2 if self.animated else min(1, self.interval)):
            self.tick()
