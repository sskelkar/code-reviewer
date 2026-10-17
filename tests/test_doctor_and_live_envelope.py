"""Regression tests built from a real `claude -p --output-format json --json-schema` reply (Haiku, Claude Code 2.1.301)."""
import json
import unittest
from pathlib import Path
from unittest import mock

from code_reviewer import doctor
from code_reviewer.claude_cli import parse_envelope
from code_reviewer.config import Config

from .helpers import FakeRunner, TempDirTestMixin

FIXTURE = Path(__file__).parent / "fixtures" / "live_envelope_haiku.json"


class LiveEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.text = FIXTURE.read_text()
        self.envelope = json.loads(self.text)

    def test_real_envelope_parses(self):
        result = parse_envelope(self.text)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.parsed, self.envelope["structured_output"])
        self.assertEqual(result.model, "claude-haiku-5-5")
        self.assertAlmostEqual(result.cost_usd, self.envelope["total_cost_usd"])
        self.assertEqual(result.num_turns, 3)
        self.assertEqual(result.tokens["output"], 386)
        self.assertEqual(result.tokens["cache_creation"], 6724)
        self.assertEqual(result.tokens["cache_read"], 6436)
        self.assertGreater(result.total_tokens, 13000)
        self.assertFalse(result.budget_exceeded)

    def test_the_fields_the_harness_relies_on_exist(self):
        for field in ("result", "structured_output", "total_cost_usd", "usage", "modelUsage", "num_turns", "duration_ms",
                      "is_error", "subtype"):
            self.assertIn(field, self.envelope)
        for field in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            self.assertIn(field, self.envelope["usage"])

    def test_structured_output_matches_the_text_result(self):
        self.assertEqual(json.loads(self.envelope["result"]), self.envelope["structured_output"])


class DoctorToolCheckTests(TempDirTestMixin, unittest.TestCase):
    def run_live(self, tool_names):
        payload = {"tool_names": tool_names, "hello_contents": "hello from the repo", "canary_followed": False, "answer": "hi"}
        runner = FakeRunner(reviewer=[payload])
        lines = []
        cfg = Config(claude_bin="claude-binary-that-does-not-exist")
        with mock.patch.object(doctor, "DATA_DIR", self.make_tmp()):
            doctor.run_doctor(cfg, runner, live=True, out=lines.append)
        return "\n".join(lines)

    def test_structured_output_tool_is_accepted(self):
        output = self.run_live(["Glob", "Grep", "Read", "StructuredOutput"])
        self.assertIn("[ok] only the read tools", output)
        self.assertNotIn("[FAIL] only the read tools", output)

    def test_any_other_tool_fails_the_check(self):
        output = self.run_live(["Read", "Bash", "StructuredOutput"])
        self.assertIn("[FAIL] only the read tools", output)
        self.assertIn("unexpected: Bash", output)

    def test_no_tools_reported_fails_the_check(self):
        self.assertIn("[FAIL] only the read tools", self.run_live([]))


if __name__ == "__main__":
    unittest.main()
