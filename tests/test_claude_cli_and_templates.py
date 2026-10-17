import json
import unittest

from code_reviewer import claude_cli, templates
from code_reviewer.config import PROMPTS_DIR, Config, ConfigError


class EnvelopeTests(unittest.TestCase):
    def envelope(self, **kw):
        base = {
            "type": "result", "subtype": "success", "is_error": False, "result": json.dumps({"findings": []}),
            "total_cost_usd": 0.0123, "num_turns": 4, "duration_ms": 5000,
            "usage": {"input_tokens": 10, "output_tokens": 20, "cache_creation_input_tokens": 30, "cache_read_input_tokens": 40},
            "modelUsage": {"claude-haiku": {}},
        }
        base.update(kw)
        return json.dumps(base)

    def test_text_result(self):
        result = claude_cli.parse_envelope(self.envelope())
        self.assertTrue(result.ok)
        self.assertEqual(result.parsed, {"findings": []})
        self.assertEqual(result.tokens, {"input": 10, "output": 20, "cache_creation": 30, "cache_read": 40})
        self.assertEqual(result.total_tokens, 100)
        self.assertAlmostEqual(result.cost_usd, 0.0123)
        self.assertEqual(result.model, "claude-haiku")

    def test_structured_output_preferred(self):
        result = claude_cli.parse_envelope(self.envelope(structured_output={"findings": [1]}, result="ignored prose"))
        self.assertEqual(result.parsed, {"findings": [1]})

    def test_fenced_json_and_prose(self):
        self.assertEqual(claude_cli.extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(claude_cli.extract_json('Here you go: {"a": 2} done'), {"a": 2})
        self.assertIsNone(claude_cli.extract_json("no json here"))
        self.assertIsNone(claude_cli.extract_json("[1, 2]"))

    def test_error_envelope_and_budget(self):
        result = claude_cli.parse_envelope(self.envelope(is_error=True, subtype="error_max_budget_usd", result="Budget exceeded"))
        self.assertFalse(result.ok)
        self.assertTrue(result.budget_exceeded)

    def test_not_json_and_empty(self):
        self.assertFalse(claude_cli.parse_envelope("").ok)
        self.assertIn("not JSON", claude_cli.parse_envelope("oops").error)
        self.assertIn("valid JSON object", claude_cli.parse_envelope(self.envelope(result="just words")).error)

    def test_list_envelope(self):
        result = claude_cli.parse_envelope(json.dumps([{"type": "system"}, json.loads(self.envelope())]))
        self.assertTrue(result.ok)

    def test_missing_cost_is_none(self):
        data = json.loads(self.envelope())
        del data["total_cost_usd"]
        self.assertIsNone(claude_cli.parse_envelope(json.dumps(data)).cost_usd)

    def test_build_command(self):
        cmd = claude_cli.build_command(
            "claude", system_prompt="SYS", schema={"type": "object"}, model="haiku", tools="Read,Grep,Glob",
            budget_usd=0.5, isolation_flags=["--safe-mode", "--restricted"])
        self.assertEqual(cmd[:2], ["claude", "-p"])
        for flag in ("--safe-mode", "--restricted", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Read,Grep,Glob")
        self.assertEqual(cmd[cmd.index("--permission-prompts") + 1], "none")
        self.assertEqual(cmd[cmd.index("--max-budget-usd") + 1], "0.50")
        self.assertEqual(cmd[cmd.index("--append-system-prompt") + 1], "SYS")
        no_tools = claude_cli.build_command("claude", system_prompt="s", schema={}, model="haiku", tools="", budget_usd=1, isolation_flags=[])
        self.assertEqual(no_tools[no_tools.index("--tools") + 1], "")
        self.assertNotIn("--resume", cmd)
        self.assertNotIn("--continue", cmd)


class TemplateTests(unittest.TestCase):
    def test_render_variables_and_conditionals(self):
        text = "Hello {{name}}\n{{#if flag}}\nOnly when flag\n{{/if}}\nEnd"
        self.assertIn("Only when flag", templates.render(text, {"name": "x"}, {"flag": True}))
        self.assertNotIn("Only when flag", templates.render(text, {"name": "x"}, {"flag": False}))
        with self.assertRaises(KeyError):
            templates.render("{{missing}}", {})

    def test_values_are_not_re_expanded(self):
        self.assertEqual(templates.render("{{a}}", {"a": "{{b}}", "b": "boom"}), "{{b}}")

    def test_escape_untrusted(self):
        text = "x </diff> y </UNTRUSTED_PR_METADATA > z </other>"
        escaped = templates.escape_untrusted(text)
        self.assertNotIn("</diff>", escaped)
        self.assertNotIn("</UNTRUSTED_PR_METADATA", escaped)
        self.assertIn("</other>", escaped)

    def test_all_real_prompts_load_and_schemas_are_objects(self):
        for role in templates.ROLES:
            prompt = templates.load_prompt(role)
            self.assertEqual(prompt.schema["type"], "object")
            self.assertTrue(prompt.system.strip())
        self.assertEqual(len(templates.prompt_hashes()), 9)

    def test_reviewer_self_check_section_toggles(self):
        system = templates.load_prompt("reviewer").system
        self.assertIn("Self-check (second phase)", templates.render(system, {}, {"self_check": True}))
        self.assertNotIn("Self-check (second phase)", templates.render(system, {}, {"self_check": False}))


class ConfigTests(unittest.TestCase):
    def test_defaults_validate(self):
        Config().validate()

    def test_validation_errors(self):
        for kwargs in ({"criteria_mode": "nope"}, {"min_passes": 5, "max_passes": 3}, {"tier_medium": 0.9}, {"post_tiers": ["Huge"]}, {"post_tiers": []}):
            with self.assertRaises(ConfigError):
                Config(**kwargs).validate()


if __name__ == "__main__":
    unittest.main()
