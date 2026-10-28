"""Wrapper around `claude -p` (Claude Code headless mode)."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

TOKEN_KEYS = ("input", "output", "cache_creation", "cache_read")


class ClaudeError(RuntimeError):
    """Raised when the claude binary cannot be run at all."""


@dataclass
class ClaudeResult:
    ok: bool
    parsed: Optional[Dict[str, Any]] = None
    text: str = ""
    error: Optional[str] = None
    cost_usd: Optional[float] = None
    tokens: Dict[str, int] = field(default_factory=lambda: {k: 0 for k in TOKEN_KEYS})
    duration_ms: Optional[int] = None
    num_turns: Optional[int] = None
    model: Optional[str] = None
    budget_exceeded: bool = False
    timed_out: bool = False
    raw: Any = None

    @property
    def total_tokens(self) -> int:
        return sum(self.tokens.values())


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Pull a JSON object out of model text, tolerating code fences and stray prose."""
    if not text:
        return None
    candidate = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", candidate, re.S)
    if fence:
        candidate = fence.group(1)
    for attempt in (candidate, candidate[candidate.find("{"): candidate.rfind("}") + 1]):
        if not attempt:
            continue
        try:
            value = json.loads(attempt)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _decode_envelope(stdout: str) -> Tuple[Optional[Dict[str, Any]], Optional[ClaudeResult]]:
    """The result object printed by `claude -p --output-format json`, or a failed ClaudeResult explaining why there is none."""
    stdout = (stdout or "").strip()
    if not stdout:
        return None, ClaudeResult(ok=False, error="empty output from claude")
    try:
        envelope = json.loads(stdout)
    except ValueError:
        return None, ClaudeResult(ok=False, error="claude output is not JSON: %s" % stdout[:200], text=stdout)
    if isinstance(envelope, list):  # some modes print every message; the last "result" message is the answer
        results = [item for item in envelope if isinstance(item, dict) and item.get("type") == "result"]
        if not results:
            return None, ClaudeResult(ok=False, error="no result message in claude output", raw=envelope)
        envelope = results[-1]
    if not isinstance(envelope, dict):
        return None, ClaudeResult(ok=False, error="unexpected claude output shape", raw=envelope)
    return envelope, None


def _token_counts(envelope: Dict[str, Any]) -> Dict[str, int]:
    usage = envelope.get("usage") or {}
    return {
        "input": _as_int(usage.get("input_tokens")),
        "output": _as_int(usage.get("output_tokens")),
        "cache_creation": _as_int(usage.get("cache_creation_input_tokens")),
        "cache_read": _as_int(usage.get("cache_read_input_tokens")),
    }


def parse_envelope(stdout: str) -> ClaudeResult:
    """Parse the JSON envelope printed by `claude -p --output-format json`."""
    envelope, failure = _decode_envelope(stdout)
    if envelope is None:
        return failure  # type: ignore[return-value]

    cost = envelope.get("total_cost_usd", envelope.get("cost_usd"))
    model_usage = envelope.get("modelUsage")
    text = envelope.get("result") if isinstance(envelope.get("result"), str) else ""
    subtype = str(envelope.get("subtype") or "")
    is_error = bool(envelope.get("is_error")) or subtype.startswith("error")

    result = ClaudeResult(
        ok=False,
        text=text,
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        tokens=_token_counts(envelope),
        duration_ms=_as_int(envelope.get("duration_ms")) or None,
        num_turns=_as_int(envelope.get("num_turns")) or None,
        model=", ".join(sorted(model_usage)) if isinstance(model_usage, dict) and model_usage else None,
        budget_exceeded="budget" in subtype.lower() or (is_error and "budget" in text.lower()[:200]),
        raw=envelope,
    )
    if is_error:
        result.error = text or subtype or "claude reported an error"
        return result

    structured = envelope.get("structured_output")
    parsed = structured if isinstance(structured, dict) else extract_json(text)
    if parsed is None:
        result.error = "output is not a valid JSON object"
        return result
    result.parsed = parsed
    result.ok = True
    return result


def build_command(
    claude_bin: str,
    *,
    system_prompt: str,
    schema: Dict[str, Any],
    model: str,
    tools: str,
    budget_usd: float,
    isolation_flags: Sequence[str],
    add_dirs: Sequence[str] = (),
) -> List[str]:
    cmd = [
        claude_bin,
        "-p",
        "--model", model,
        *isolation_flags,
        "--tools", tools,
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--permission-prompts", "none",
        "--no-session-persistence",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--max-budget-usd", "%.2f" % budget_usd,
        "--append-system-prompt", system_prompt,
    ]
    for directory in add_dirs:
        cmd += ["--add-dir", str(directory)]
    return cmd


class ClaudeRunner:
    """Runs one headless Claude Code call. The user prompt is sent on stdin."""

    def __init__(self, claude_bin: str = "claude", isolation_flags: Optional[Sequence[str]] = None, *, env: Optional[Dict[str, str]] = None):
        self.env = env
        self.claude_bin = claude_bin
        self.isolation_flags = list(isolation_flags if isolation_flags is not None else ["--safe-mode", "--restricted"])

    def version(self) -> str:
        try:
            proc = subprocess.run([self.claude_bin, "--version"], capture_output=True, text=True, timeout=30, env=self.env)
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        return (proc.stdout or proc.stderr).strip() or "unknown"

    def run(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: Dict[str, Any],
        model: str,
        tools: str,
        cwd: Optional[Path],
        budget_usd: float,
        timeout_s: int,
        add_dirs: Sequence[str] = (),
    ) -> ClaudeResult:
        cmd = build_command(
            self.claude_bin,
            system_prompt=system_prompt,
            schema=schema,
            model=model,
            tools=tools,
            budget_usd=budget_usd,
            isolation_flags=self.isolation_flags,
            add_dirs=add_dirs,
        )
        try:
            proc = subprocess.run(
                cmd,
                input=user_prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(cwd) if cwd else None,
                timeout=timeout_s,
                env=self.env,
            )
        except subprocess.TimeoutExpired:
            return ClaudeResult(ok=False, error="timed out after %ss" % timeout_s, timed_out=True)
        except FileNotFoundError as exc:
            raise ClaudeError("claude binary not found: %s" % self.claude_bin) from exc

        result = parse_envelope(proc.stdout)
        if proc.returncode != 0 and result.ok:
            result.ok = False
            result.error = "claude exited with status %d: %s" % (proc.returncode, (proc.stderr or "").strip()[-300:])
        elif result.raw is None and not result.ok:
            stderr = (proc.stderr or "").strip()[-300:]
            if stderr:
                result.error = "%s (stderr: %s)" % (result.error, stderr)
        return result
