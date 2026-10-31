"""Where the Reviewer's checkout lives, what is saved of its input, and `--dry-run`."""
import contextlib
import io
import shutil
import unittest
from pathlib import Path
from unittest import mock

from code_reviewer import cli
from code_reviewer.backends.contract import RunnerError
from code_reviewer.config import Config
from code_reviewer.flows.single_agent.dry_run import render_dry_run
from code_reviewer.engine import Engine

from .helpers import FakeRunner, TempDirTestMixin, finding, make_repo

RESOLVER = {"criteria_files": [], "inline_sections": [], "organization_notes": "", "ignored_references": []}


class CheckoutLocationTests(TempDirTestMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()
        self.ctx = make_repo(self.tmp)
        self.events = []

    def engine(self, runner, **cfg):
        return Engine(Config(runs_dir=str(self.tmp / "runs"), **cfg), runner, emit=self.events.append)

    def runner(self):
        return FakeRunner(reviewer=[{"findings": []}], resolver=RESOLVER)

    def test_checkout_is_not_inside_the_run_directory_or_next_to_run_files(self):
        runner = self.runner()
        result = self.engine(runner, max_passes=3, min_passes=3).review(self.ctx)
        cwd = Path(runner.calls_for("reviewer")[0]["cwd"])
        self.assertNotIn(result.run_dir, cwd.parents)
        self.assertFalse(str(cwd).startswith(str(result.run_dir)))
        # nothing the run produced (earlier passes' findings, master list, reports) sits in any ancestor of the checkout
        for ancestor in cwd.parents:
            for name in ("pass-01", "master.json", "report.md", "summary.md", "stats.json"):
                self.assertFalse((ancestor / name).exists(), "%s found in %s" % (name, ancestor))

    def test_checkout_is_deleted_after_the_run(self):
        runner = self.runner()
        self.engine(runner).review(self.ctx)
        cwd = Path(runner.calls_for("reviewer")[0]["cwd"])
        self.assertFalse(cwd.exists())
        self.assertFalse(cwd.parent.exists())  # the temporary directory that held it is gone too

    def test_checkout_is_deleted_even_when_the_run_fails(self):
        seen = []

        class Boom(FakeRunner):
            def run(self, **kwargs):
                checkout = Path(kwargs["cwd"])
                seen.append((checkout, (checkout / "app.py").exists()))  # the checkout existed and was populated
                raise RunnerError("claude binary not found")

        with self.assertRaises(RunnerError):
            self.engine(Boom(reviewer=[{"findings": []}]), criteria_mode="defaults_only").review(self.ctx)
        self.assertEqual([populated for _, populated in seen], [True])
        self.assertFalse(seen[0][0].parent.exists())  # removed although the run raised

    def test_keep_checkout_keeps_it_outside_the_run_dir_and_reports_where(self):
        runner = self.runner()
        result = self.engine(runner, max_passes=1, min_passes=1).review(self.ctx, keep_checkout=True)
        cwd = Path(runner.calls_for("reviewer")[0]["cwd"])
        self.addCleanup(shutil.rmtree, str(cwd.parent), True)
        self.assertTrue((cwd / "app.py").exists())
        self.assertNotIn(result.run_dir, cwd.parents)
        ready = [e for e in self.events if e.type == "checkout_ready"][0]
        self.assertEqual((ready.data["kept"], ready.data["path"]), (True, str(cwd)))

    def test_checkout_has_the_files_but_no_git_history(self):
        seen = {}

        class Peek(FakeRunner):
            def run(inner, **kwargs):
                if Path(kwargs["cwd"]).name == "checkout":
                    root = Path(kwargs["cwd"])
                    seen["files"] = sorted(p.name for p in root.iterdir())
                return super().run(**kwargs)

        peek = Peek(reviewer=[{"findings": []}], resolver=RESOLVER)
        self.engine(peek, max_passes=1, min_passes=1).review(self.ctx)
        self.assertNotIn(".git", seen["files"])
        self.assertIn("app.py", seen["files"])


class SavedInputsTests(TempDirTestMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()
        self.ctx = make_repo(self.tmp)

    def test_saved_prompts_and_pr_text_are_exactly_what_the_reviewer_received(self):
        runner = FakeRunner(reviewer=[{"findings": [finding(rule_id="none")]}], resolver=RESOLVER)
        result = Engine(Config(runs_dir=str(self.tmp / "runs"), max_passes=1, min_passes=1), runner).review(self.ctx)
        call = runner.calls_for("reviewer")[0]
        inputs = result.run_dir / "input"
        self.assertEqual((inputs / "reviewer.system.md").read_text(), call["system_prompt"])
        self.assertEqual((inputs / "reviewer.user.md").read_text(), call["user_prompt"])
        import json
        pr = json.loads((inputs / "pr.json").read_text())
        self.assertEqual((pr["title"], pr["description"], pr["commit_messages"]),
                         (self.ctx.title, self.ctx.body, self.ctx.commit_messages))
        self.assertEqual((pr["head_sha"], pr["number"]), (self.ctx.head_sha, 7))

    def test_only_an_allow_list_of_pr_fields_is_fetched_from_github(self):
        """Review comments, review threads and issue comments must never be fetched. Adding any of them here must be a
        deliberate decision that also changes this test (and the README's privacy section)."""
        from code_reviewer.shared import git_ops
        captured = []

        def fake_run(cmd, *args, **kwargs):
            captured.append(cmd)
            return mock.Mock(stdout="{}", returncode=0)

        with mock.patch.object(git_ops, "_run", fake_run):
            git_ops.gh_pr_view(git_ops.parse_pr_ref("acme/project#7"))
        fields = set(captured[0][captured[0].index("--json") + 1].split(","))
        self.assertEqual(fields, {"title", "body", "baseRefName", "headRefName", "headRefOid", "url", "state", "author",
                                  "isCrossRepository"})
        for forbidden in ("comments", "reviews", "latestReviews", "reviewRequests", "statusCheckRollup"):
            self.assertNotIn(forbidden, fields)

    def test_the_reviewer_only_has_read_tools(self):
        runner = FakeRunner(reviewer=[{"findings": []}], resolver=RESOLVER)
        Engine(Config(runs_dir=str(self.tmp / "runs"), max_passes=1, min_passes=1), runner).review(self.ctx)
        self.assertEqual({c["tools"] for c in runner.calls_for("reviewer")}, {"Read,Grep,Glob"})
        self.assertEqual({c["tools"] for c in runner.calls_for("comparator") + runner.calls_for("resolver")}, {""})


class DryRunTests(TempDirTestMixin, unittest.TestCase):
    def setUp(self):
        from .helpers import synthetic_frontend
        synthetic_frontend(self)
        self.tmp = self.make_tmp()
        self.ctx = make_repo(self.tmp)
        self.ctx.body = "Replaces the prepared statement.\n\nSummary by SomeBot: this PR looks great."

    def make(self, runner=None, **cfg):
        runner = runner or FakeRunner(reviewer=[{"findings": []}], resolver=RESOLVER)
        return runner, Engine(Config(runs_dir=str(self.tmp / "runs"), **cfg), runner)

    def test_dry_run_calls_no_model_and_creates_no_checkout(self):
        runner, engine = self.make()
        with mock.patch("code_reviewer.shared.git_ops.export_tree") as export:
            dry = engine.dry_run(self.ctx)
        self.assertEqual(runner.calls, [])
        export.assert_not_called()
        self.assertTrue(dry.run_dir.name.endswith("_dry-run"))

    def test_dry_run_prompts_equal_a_real_reviews_prompts(self):
        runner, engine = self.make(criteria_mode="defaults_only", max_passes=1, min_passes=1)
        dry = engine.dry_run(self.ctx)
        runner2, engine2 = self.make(criteria_mode="defaults_only", max_passes=1, min_passes=1)
        real = engine2.review(self.ctx)
        for name in ("reviewer.system.md", "reviewer.user.md", "diff.txt"):
            self.assertEqual((dry.run_dir / "input" / name).read_text(), (real.run_dir / "input" / name).read_text(), name)
        self.assertEqual((dry.run_dir / "input" / "reviewer.user.md").read_text(), runner2.calls_for("reviewer")[0]["user_prompt"])
        self.assertEqual((dry.run_dir / "input" / "reviewer.system.md").read_text(), runner2.calls_for("reviewer")[0]["system_prompt"])

    def test_dry_run_output_shows_the_pr_text_the_tools_and_the_command(self):
        _, engine = self.make()
        text = render_dry_run(engine.dry_run(self.ctx))
        for expected in ("nothing was sent to Claude", "Speed up lookups", "Summary by SomeBot", "Build query by concatenation",
                         "Read, Grep, Glob", "no shell, no network, no GitHub access", "WHAT IT IS NOT GIVEN",
                         "Pull request comments, review threads", "claude -p --model haiku", "--tools Read,Grep,Glob",
                         "<system prompt: input/reviewer.system.md>", "<JSON schema of the findings>", "reviewer.user.md",
                         "default rules", "criteria resolver"):
            self.assertIn(expected, text)
        self.assertNotIn("You are a meticulous senior engineer", text)  # the command shows placeholders, not the prompt itself

    def test_dry_run_refuses_the_same_prs_a_real_review_refuses(self):
        from code_reviewer.shared.models import ReviewRefused
        _, engine = self.make(max_diff_lines=2)
        with self.assertRaises(ReviewRefused):
            engine.dry_run(self.ctx)

    def test_cli_dry_run_prints_and_does_not_call_the_model(self):
        fake = FakeRunner(reviewer=[{"findings": []}], resolver=RESOLVER)
        out = io.StringIO()
        config = Config(runs_dir=str(self.tmp / "runs"))
        with mock.patch.object(cli, "load_config", return_value=config), \
                mock.patch("code_reviewer.backends.factory.create_runner", return_value=fake), \
                mock.patch.object(Engine, "prepare", return_value=self.ctx), \
                contextlib.redirect_stdout(out):
            code = cli.main(["review", "acme/project#7", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(fake.calls, [])
        self.assertIn("WHAT THE REVIEWER IS GIVEN", out.getvalue())

    def test_dry_run_cannot_be_combined_with_post(self):
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
            cli.main(["review", "acme/project#7", "--dry-run", "--post"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
