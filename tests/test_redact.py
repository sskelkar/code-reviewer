"""Email addresses in the PR text are replaced before anything is sent to the model."""
import json
import unittest

from code_reviewer.config import Config
from code_reviewer.flows.single_agent.dry_run import render_dry_run
from code_reviewer.engine import Engine
from code_reviewer.shared.redact import PLACEHOLDER, redact_emails

from .helpers import FakeRunner, TempDirTestMixin, make_repo

RESOLVER = {"criteria_files": [], "inline_sections": [], "organization_notes": "", "ignored_references": []}


class RedactEmailsTests(unittest.TestCase):
    def test_replaces_addresses_and_counts_them(self):
        text, count = redact_emails("Assigned by jane.doe@example.com, cc bob+ci@mail.corp.example.org and <a_b@x.io>.")
        self.assertEqual(count, 3)
        self.assertEqual(text, "Assigned by [email], cc [email] and <[email]>.")

    def test_markdown_mailto_and_trailing_punctuation(self):
        text, count = redact_emails("[mail](mailto:jane@example.com). Or jane@example.com.")
        self.assertEqual((text, count), ("[mail](mailto:[email]). Or [email].", 2))

    def test_leaves_things_that_are_not_personal_addresses(self):
        for keep in ("git clone git@github.com:acme/project.git", "bump lodash@4.17.21 and react@18.2.0-rc.1", "ping @octocat",
                     "decorator@property", "user@localhost", "a @scope/package", "the @ sign", "x@1.2.3"):
            self.assertEqual(redact_emails(keep), (keep, 0), keep)

    def test_is_idempotent_and_handles_empty_input(self):
        once, _ = redact_emails("mail me at jane@example.com")
        self.assertEqual(redact_emails(once), (once, 0))
        self.assertEqual(redact_emails(""), ("", 0))
        self.assertEqual(redact_emails(None), ("", 0))
        self.assertEqual(PLACEHOLDER, "[email]")


class EngineRedactionTests(TempDirTestMixin, unittest.TestCase):
    EMAIL = "jane.doe@example.com"

    def setUp(self):
        self.tmp = self.make_tmp()
        self.ctx = make_repo(self.tmp)
        self.ctx.title = "Speed up lookups (requested by %s)" % self.EMAIL
        self.ctx.body = "Replaces the prepared statement.\n\nAssigned by %s, see the thread." % self.EMAIL
        self.ctx.commit_messages = "Build query by concatenation\n\nCo-authored-by: Jane <%s>" % self.EMAIL

    def engine(self, runner, **cfg):
        events = []
        return Engine(Config(runs_dir=str(self.tmp / "runs"), max_passes=1, min_passes=1, **cfg), runner, emit=events.append), events

    def test_no_email_reaches_the_model_or_disk(self):
        runner = FakeRunner(reviewer=[{"findings": []}], resolver=RESOLVER)
        engine, events = self.engine(runner)
        result = engine.review(self.ctx)
        for call in runner.calls:
            self.assertNotIn(self.EMAIL, call["user_prompt"])
            self.assertNotIn(self.EMAIL, call["system_prompt"])
        self.assertIn("[email]", runner.calls_for("reviewer")[0]["user_prompt"])
        for path in result.run_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn(self.EMAIL, path.read_text(errors="ignore"), str(path))
        pr = json.loads((result.run_dir / "input" / "pr.json").read_text())
        self.assertEqual(pr["redacted_emails"], 3)
        self.assertNotIn(self.EMAIL, pr["title"] + pr["description"] + pr["commit_messages"])
        self.assertEqual([e.data["emails"] for e in events if e.type == "pii_redacted"], [3])
        self.assertNotIn(self.EMAIL, result.ctx.title)

    def test_redaction_can_be_turned_off(self):
        runner = FakeRunner(reviewer=[{"findings": []}], resolver=RESOLVER)
        engine, events = self.engine(runner, redact_emails=False)
        engine.review(self.ctx)
        self.assertIn(self.EMAIL, runner.calls_for("reviewer")[0]["user_prompt"])
        self.assertEqual([e for e in events if e.type == "pii_redacted"], [])

    def test_dry_run_shows_the_redacted_text_and_how_many_were_replaced(self):
        runner = FakeRunner(reviewer=[{"findings": []}], resolver=RESOLVER)
        engine, _ = self.engine(runner)
        text = render_dry_run(engine.dry_run(self.ctx))
        self.assertNotIn(self.EMAIL, text)
        self.assertIn("replaced with [email]: 3 replaced above", text)
        off, _ = self.engine(runner, redact_emails=False)
        self.assertIn("Email redaction is OFF", render_dry_run(off.dry_run(self.ctx)))
        self.assertEqual(runner.calls, [])

    def test_diff_and_code_are_not_redacted(self):
        """Only the PR text is redacted: the code under review is sent as it is."""
        from .helpers import git
        (self.ctx.git_dir / "app.py").write_text('OWNER = "%s"\n' % self.EMAIL)
        git(self.ctx.git_dir, "commit", "-qam", "add owner")
        from dataclasses import replace
        ctx = replace(self.ctx, head_sha=git(self.ctx.git_dir, "rev-parse", "HEAD"))
        runner = FakeRunner(reviewer=[{"findings": []}], resolver=RESOLVER)
        engine, _ = self.engine(runner)
        engine.review(ctx)
        self.assertIn(self.EMAIL, runner.calls_for("reviewer")[0]["user_prompt"])  # from the diff


if __name__ == "__main__":
    unittest.main()
