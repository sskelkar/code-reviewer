"""The welcome banner lists every REPL command, so users do not have to remember them."""
import re
import unittest

from code_reviewer.config import Config
from code_reviewer.repl import COMMAND_GROUPS, Session, welcome_text

from .helpers import FakeRunner


def first_words():
    return {command.split()[0] for _, entries in COMMAND_GROUPS for command, _ in entries}


class WelcomeTests(unittest.TestCase):
    def test_every_command_the_repl_accepts_is_in_the_banner(self):
        words = set(re.findall(r"[A-Za-z]+", welcome_text(None)))
        for command in list(Session.COMMANDS) + ["quit"]:
            self.assertIn(command, words, "command %r is not mentioned in the welcome banner" % command)

    def test_every_banner_command_exists(self):
        accepted = set(Session.COMMANDS) | {"quit"}
        self.assertEqual(first_words() - accepted, set())

    def test_banner_text(self):
        text = welcome_text("acme/project (github.com)")
        self.assertIn("Repository here: acme/project (github.com)", text)
        for group in ("Start a review:", "After a review:", "Anytime:"):
            self.assertIn(group, text)
        for example in ("review <PR URL> --passes 1", "mark F-001 real|nit|wrong", "post", "missed", "help", "quit"):
            self.assertIn(example, text)
        self.assertIn("only after you confirm", text)
        self.assertIn("Not inside a clone", welcome_text(None))

    def test_banner_columns_are_aligned(self):
        lines = [l for l in welcome_text(None).splitlines() if l.startswith("  ")]
        starts = {l.index(l.strip().split("  ", 1)[1].strip()) for l in lines if "  " in l.strip()}
        self.assertEqual(len(starts), 1)  # descriptions all begin in the same column

    def test_repl_prints_the_banner_at_start_and_help_still_works(self):
        out, inputs = [], iter(["help", "quit"])
        session = Session(Config(), FakeRunner(reviewer=[{"findings": []}]), out=out.append, ask=lambda prompt: next(inputs))
        session.loop()
        text = "\n".join(out)
        self.assertIn("Start a review:", text)
        self.assertIn("After a review:", text)
        self.assertIn("Commands:", text)  # the detailed help from `help`

    def test_unknown_command_points_to_help(self):
        out = []
        session = Session(Config(), FakeRunner(reviewer=[{"findings": []}]), out=out.append, ask=lambda prompt: "q")
        session.dispatch("frobnicate")
        self.assertIn("Type 'help'", out[0])
        self.assertFalse(session.dispatch("exit"))


if __name__ == "__main__":
    unittest.main()
