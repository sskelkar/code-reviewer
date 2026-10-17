"""The review options are defined once and documented in `reviewer --help`, `reviewer review --help` and the REPL help."""
import contextlib
import io
import unittest
from argparse import Namespace

from code_reviewer.cli import build_parser
from code_reviewer.config import Config, ConfigError
from code_reviewer.repl import Session
from code_reviewer.repl_help import HELP
from code_reviewer.review_options import apply_run_options

from .helpers import FakeRunner

RUN_OPTIONS = ("--passes", "--model", "--no-self-check", "--keep-checkout", "--verbose", "--dry-run")


def squash(text):
    """Collapse whitespace: argparse wraps long help text across lines."""
    return " ".join(text.split())


def render_help(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), self_exit_ok():
        build_parser().parse_args(list(argv) + ["--help"])
    return squash(out.getvalue())


@contextlib.contextmanager
def self_exit_ok():
    try:
        yield
    except SystemExit as exc:
        assert exc.code == 0


def options(**overrides):
    base = dict(passes=None, model=None, no_self_check=False, keep_checkout=False, verbose=False)
    base.update(overrides)
    return Namespace(**base)


class HelpTextTests(unittest.TestCase):
    def test_top_level_help_points_to_the_review_options(self):
        text = render_help()
        self.assertIn("reviewer review --help", text)
        self.assertIn("--passes 1", text)  # the example
        self.assertIn("interactive prompt", text)

    def test_review_help_documents_every_option_with_a_description(self):
        text = render_help("review")
        for option in RUN_OPTIONS + ("--post", "--local-path"):
            self.assertIn(option, text)
        for phrase in ("maximum number of review passes", "refute its own findings", "temporary read-only checkout",
                       "extra progress detail", "alias or full name", "explicit opt-in",
                       "no model is called"):
            self.assertIn(phrase, text)

    def test_repl_help_describes_the_options(self):
        for option in RUN_OPTIONS:
            self.assertIn(option, HELP)
        self.assertIn("single, cheap pass", HELP)


class ParsingTests(unittest.TestCase):
    def parse(self, *argv):
        return build_parser().parse_args(["review", "owner/repo#1"] + list(argv))

    def test_defaults(self):
        args = self.parse()
        self.assertEqual((args.passes, args.model, args.no_self_check, args.keep_checkout, args.verbose, args.post),
                         (None, None, False, False, False, False))

    def test_all_options(self):
        args = self.parse("--passes", "2", "--model", "sonnet", "--no-self-check", "--keep-checkout", "--verbose", "--post")
        self.assertEqual((args.passes, args.model), (2, "sonnet"))
        self.assertTrue(args.no_self_check and args.keep_checkout and args.verbose and args.post)

    def test_passes_must_be_a_positive_whole_number(self):
        for bad in ("0", "-1", "two"):
            with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
                self.parse("--passes", bad)
            self.assertEqual(caught.exception.code, 2)


class ApplyOptionsTests(unittest.TestCase):
    def test_passes_sets_max_and_lowers_min(self):
        cfg = apply_run_options(Config(), options(passes=1))
        self.assertEqual((cfg.max_passes, cfg.min_passes), (1, 1))
        cfg = apply_run_options(Config(), options(passes=10))
        self.assertEqual((cfg.max_passes, cfg.min_passes), (10, 3))

    def test_model_applies_to_all_roles_and_self_check_can_be_disabled(self):
        cfg = apply_run_options(Config(), options(model="sonnet", no_self_check=True))
        self.assertEqual((cfg.model_reviewer, cfg.model_comparator, cfg.model_resolver), ("sonnet",) * 3)
        self.assertFalse(cfg.self_check)

    def test_original_config_is_not_modified(self):
        original = Config()
        apply_run_options(original, options(passes=1, model="sonnet", no_self_check=True))
        self.assertEqual((original.max_passes, original.model_reviewer, original.self_check), (6, "haiku", True))

    def test_invalid_combination_raises(self):
        with self.assertRaises(ConfigError):
            apply_run_options(Config(min_passes=3, max_passes=3, quiet_k=0), options())


class ReplReviewCommandTests(unittest.TestCase):
    def setUp(self):
        self.lines = []
        self.session = Session(Config(), FakeRunner(reviewer=[{"findings": []}]), out=self.lines.append, ask=lambda p: "n")

    def text(self):
        return squash("\n".join(self.lines))

    def test_review_help_lists_all_options(self):
        self.session.dispatch("review --help")
        for option in RUN_OPTIONS:
            self.assertIn(option, self.text())
        self.assertIn("maximum number of review passes", self.text())

    def test_bad_option_value_gives_a_helpful_error_and_does_not_exit(self):
        self.assertTrue(self.session.dispatch("review owner/repo#1 --passes 0"))
        self.assertIn("must be 1 or more", self.text())
        self.assertIn("review --help", self.text())

    def test_missing_pr_and_unknown_option_are_reported(self):
        self.session.dispatch("review")
        self.session.dispatch("review owner/repo#1 --bogus")
        self.assertEqual(self.text().count("error:"), 2)

    def test_non_pr_argument_is_reported(self):
        self.session.dispatch("review not-a-pr")
        self.assertIn("not a pull request URL", self.text())


if __name__ == "__main__":
    unittest.main()
