"""ClaudeRunner against a fake `claude` executable: checks stdin, arguments, working directory, timeouts and exit codes."""
import json
import os
import stat
import sys
import unittest
from pathlib import Path

from code_reviewer.claude_cli import ClaudeError, ClaudeRunner

from .helpers import TempDirTestMixin

SUCCESS = """#!%(python)s
import json, os, sys
record = {"argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd": os.getcwd()}
open(os.environ["FAKE_CLAUDE_OUT"], "w").write(json.dumps(record))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": json.dumps({"ok": True}), "total_cost_usd": 0.5,
                  "usage": {"input_tokens": 1, "output_tokens": 2}, "num_turns": 1, "duration_ms": 10}))
sys.exit(%(code)d)
"""

SLEEPER = "#!%(python)s\nimport time\ntime.sleep(10)\n"


class RunnerPlumbingTests(TempDirTestMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()
        self.out = self.tmp / "record.json"
        os.environ["FAKE_CLAUDE_OUT"] = str(self.out)
        self.addCleanup(os.environ.pop, "FAKE_CLAUDE_OUT", None)

    def fake(self, template, **values):
        path = self.tmp / "claude"
        path.write_text(template % {"python": sys.executable, **values})
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return str(path)

    def run_runner(self, binary, timeout_s=30, tools="Read,Grep,Glob"):
        cwd = self.tmp / "work"
        cwd.mkdir(exist_ok=True)
        runner = ClaudeRunner(binary, ["--safe-mode", "--restricted"])
        return runner.run(
            system_prompt="SYSTEM TEXT", user_prompt="USER PROMPT\nwith lines", schema={"type": "object"}, model="haiku",
            tools=tools, cwd=cwd, budget_usd=0.5, timeout_s=timeout_s), cwd

    def test_prompt_goes_on_stdin_and_flags_are_passed(self):
        result, cwd = self.run_runner(self.fake(SUCCESS, code=0))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.parsed, {"ok": True})
        self.assertAlmostEqual(result.cost_usd, 0.5)
        record = json.loads(self.out.read_text())
        self.assertEqual(record["stdin"], "USER PROMPT\nwith lines")
        self.assertEqual(Path(record["cwd"]).resolve(), cwd.resolve())
        argv = record["argv"]
        self.assertEqual(argv[0], "-p")
        self.assertEqual(argv[argv.index("--model") + 1], "haiku")
        self.assertEqual(argv[argv.index("--append-system-prompt") + 1], "SYSTEM TEXT")
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]), {"type": "object"})
        self.assertIn("--safe-mode", argv)

    def test_empty_tools_value_is_passed_through(self):
        self.run_runner(self.fake(SUCCESS, code=0), tools="")
        argv = json.loads(self.out.read_text())["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "")

    def test_nonzero_exit_is_a_failure(self):
        result, _ = self.run_runner(self.fake(SUCCESS, code=3))
        self.assertFalse(result.ok)
        self.assertIn("status 3", result.error)

    def test_timeout(self):
        result, _ = self.run_runner(self.fake(SLEEPER), timeout_s=1)
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)

    def test_missing_binary_raises(self):
        with self.assertRaises(ClaudeError):
            self.run_runner(str(self.tmp / "does-not-exist"))


if __name__ == "__main__":
    unittest.main()
