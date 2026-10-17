"""`reviewer review` prints a banner of its options when it runs in a terminal."""
import contextlib
import io
import re
import unittest
from unittest import mock

from code_reviewer import cli
from code_reviewer.config import Config
from code_reviewer.engine import Engine
from code_reviewer.review_options import BANNER_OPTIONS, review_banner

from .helpers import FakeRunner, TempDirTestMixin, finding, make_repo
from .test_options import render_help

RESOLVER = {"criteria_files": [], "inline_sections": [], "organization_notes": "", "ignored_references": []}


class TerminalStringIO(io.StringIO):
    def isatty(self):
        return True


class BannerContentTests(unittest.TestCase):
    def test_every_option_of_the_command_is_in_the_banner(self):
        flags_in_help = set(re.findall(r"--[a-z][a-z-]*", render_help("review"))) - {"--help"}
        flags_in_banner = {flag.split()[0] for flag, _ in BANNER_OPTIONS}
        self.assertEqual(flags_in_help - flags_in_banner, set(), "options missing from the review banner")
        self.assertIn("--config", flags_in_banner)  # the top-level option that goes before `review`

    def test_banner_describes_this_run_and_what_happens_afterwards(self):
        text = review_banner("acme/project#7", Config(max_passes=4, model_reviewer="sonnet", self_check=False, redact_emails=False))
        for expected in ("acme/project#7", "up to 4 passes", "model sonnet", "self-check off", "NOT redacted",
                         "Options you can add to this command:", "--passes N", "--dry-run", "--post",
                         "the review summary", "interactive prompt", "run `reviewer`"):
            self.assertIn(expected, text)
        self.assertIn("self-check on", review_banner("a/b#1", Config()))

    def test_option_descriptions_line_up(self):
        lines = [l for l in review_banner("a/b#1", Config()).splitlines() if l.startswith("  --")]
        self.assertEqual(len(lines), len(BANNER_OPTIONS))
        self.assertEqual(len({l.index(d) for l, (_, d) in zip(lines, BANNER_OPTIONS)}), 1)


class BannerInTheCliTests(TempDirTestMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()
        self.ctx = make_repo(self.tmp)

    def run_cli(self, stream, *argv):
        fake = FakeRunner(reviewer=[{"findings": [finding(rule_id="none")]}], resolver=RESOLVER)
        config = Config(runs_dir=str(self.tmp / "runs"))
        with mock.patch.object(cli, "load_config", return_value=config), \
                mock.patch.object(cli, "ClaudeRunner", return_value=fake), \
                mock.patch.object(Engine, "prepare", return_value=self.ctx), \
                contextlib.redirect_stdout(stream):
            return cli.main(["review", "acme/project#7"] + list(argv))

    def test_banner_is_printed_first_in_a_terminal(self):
        out = TerminalStringIO()
        self.assertEqual(self.run_cli(out, "--passes", "1"), 0)
        text = out.getvalue()
        self.assertTrue(text.startswith("reviewer review: multi-pass pull request review"))
        self.assertIn("up to 1 passes", text)  # the effective settings, after --passes
        self.assertLess(text.index("Options you can add"), text.index("## AI-generated code review"))

    def test_banner_is_not_printed_when_output_is_not_a_terminal(self):
        out = io.StringIO()
        self.assertEqual(self.run_cli(out, "--passes", "1"), 0)
        self.assertNotIn("Options you can add", out.getvalue())
        self.assertIn("## AI-generated code review", out.getvalue())

    def test_banner_is_also_shown_for_a_dry_run(self):
        out = TerminalStringIO()
        self.assertEqual(self.run_cli(out, "--dry-run"), 0)
        self.assertIn("Options you can add", out.getvalue())
        self.assertIn("WHAT THE REVIEWER IS GIVEN", out.getvalue())


if __name__ == "__main__":
    unittest.main()
