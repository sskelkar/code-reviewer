"""Configuration for the review harness."""
from __future__ import annotations

import os
import math
import stat
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(os.environ.get("CODE_REVIEWER_HOME") or Path(__file__).resolve().parents[2])
SINGLE_AGENT_DIR = REPO_ROOT / "src" / "code_reviewer" / "flows" / "single_agent"
PROMPTS_DIR = SINGLE_AGENT_DIR / "prompts"
DEFAULT_CRITERIA_DIR = SINGLE_AGENT_DIR / "criteria"
DATA_DIR = Path(os.environ.get("CODE_REVIEWER_DATA") or Path.home() / ".code-reviewer")

DEFAULT_EXCLUDE_PATTERNS = [
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "uv.lock",
    "Cargo.lock",
    "go.sum",
    "composer.lock",
    "Gemfile.lock",
    "gradle.lockfile",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.pb.go",
    "*_pb2.py",
    "*_pb2_grpc.py",
    "*.generated.*",
    "node_modules/",
    "vendor/",
    "dist/",
    ".idea/",
]

REVIEW_MODES = ("single-agent", "agent-personas")

CRITERIA_MODES = ("merge", "project_only", "defaults_only")


class ConfigError(ValueError):
    """Raised when the configuration file is invalid."""


@dataclass
class Config:
    # Models (aliases understood by `claude --model`)
    model_reviewer: str = "haiku"
    model_comparator: str = "haiku"
    model_resolver: str = "haiku"

    # Review behavior
    self_check: bool = True
    criteria_mode: str = "merge"
    criteria_manifest: str = ""
    disabled_default_rules: List[str] = field(default_factory=list)

    # Loop control
    min_passes: int = 3
    max_passes: int = 6
    quiet_k: int = 2
    max_consecutive_failures: int = 2

    # Confidence tiers (share of completed passes that saw a finding)
    tier_high: float = 0.75
    tier_medium: float = 0.40

    # Comparator candidates
    candidate_window: int = 20
    candidate_all_threshold: int = 30
    candidate_max: int = 40

    # Input limits
    max_diff_lines: int = 3000
    criteria_max_chars: int = 200000
    exclude_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_PATTERNS))
    # Added to `exclude_patterns` (so the built-in list stays in place), for example planning documents such as PRDs
    extra_exclude_patterns: List[str] = field(default_factory=list)
    grounding_line_window: int = 30
    # Replace email addresses in the PR title, description and commit messages before they are sent to the model
    redact_emails: bool = True

    # Budgets and timeouts
    call_budget_usd: float = 0.50
    call_timeout_s: int = 600
    run_budget_usd: float = 3.00
    run_budget_tokens: int = 2000000

    # Claude Code invocation
    claude_bin: str = "claude"
    isolation_flags: List[str] = field(default_factory=lambda: ["--safe-mode", "--restricted"])

    # Posting
    # Confidence tiers shown in the summary and included in a posted comment (single-pass findings are always included)
    post_tiers: List[str] = field(default_factory=lambda: ["High", "Medium", "Low"])

    # Storage
    runs_dir: str = ""

    # Append new fields to preserve existing positional constructor arguments.
    review_mode: str = "single-agent"
    model_verifier: str = "haiku"
    persona_prompt_max_chars: int = 500000
    persona_passes: int = 3
    persona_parallel: int = 4

    provider: str = "claude"
    sandbox: bool = True
    openai_input_price: float = 0.0
    openai_cached_input_price: float = 0.0
    openai_output_price: float = 0.0

    # Trusted deployment inputs; never credentials. Relative paths are refused.
    vm_artifacts: str = ""
    vm_lima_home: str = ""
    vm_instance: str = "reviewer-sandbox"
    vm_timeout_s: int = 1800
    vm_claude_binary: str = ""
    vm_claude_sha256: str = ""
    vm_claude_auth: str = "api-key"

    def validate(self) -> None:
        if type(self.sandbox) is not bool:
            raise ConfigError('sandbox must be true or false')
        for name in ('openai_input_price', 'openai_cached_input_price', 'openai_output_price'):
            value = getattr(self, name)
            try:
                valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
            except OverflowError:
                valid = False
            if not valid:
                raise ConfigError('%s must be finite and nonnegative' % name)
        if self.provider not in ("claude", "openai"):
            raise ConfigError("provider must be claude or openai; no fallback")
        for name in ("call_budget_usd", "run_budget_usd"):
            value = getattr(self, name)
            try:
                valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0
            except OverflowError:
                valid = False
            if not valid:
                raise ConfigError("%s must be finite and positive" % name)
        if isinstance(self.persona_parallel, bool) or not isinstance(self.persona_parallel, int) or not 1 <= self.persona_parallel <= 4:
            raise ConfigError("persona_parallel must be an integer from 1 to 4")
        if isinstance(self.persona_passes, bool) or not isinstance(self.persona_passes, int) or self.persona_passes < 1:
            raise ConfigError('persona_passes must be a positive integer')
        if self.review_mode == 'agent-personas' and self.call_budget_usd < 0.01:
            raise ConfigError('agent-personas needs call_budget_usd >= 0.01 (Claude CLI dollar limits use cents)')
        if self.persona_prompt_max_chars < 1:
            raise ConfigError("persona_prompt_max_chars must be positive")
        if self.review_mode not in REVIEW_MODES:
            raise ConfigError("review_mode must be one of %s" % ", ".join(REVIEW_MODES))
        if self.criteria_mode not in CRITERIA_MODES:
            raise ConfigError("criteria_mode must be one of %s" % ", ".join(CRITERIA_MODES))
        if self.min_passes < 1 or self.max_passes < self.min_passes:
            raise ConfigError("need 1 <= min_passes <= max_passes")
        if self.quiet_k < 1:
            raise ConfigError("quiet_k must be >= 1")
        if not (0 < self.tier_medium < self.tier_high <= 1):
            raise ConfigError("need 0 < tier_medium < tier_high <= 1")
        if self.call_budget_usd <= 0 or self.run_budget_usd <= 0:
            raise ConfigError("budgets must be positive")
        if not self.post_tiers:
            raise ConfigError("post_tiers must list at least one of High, Medium, Low")
        for tier in self.post_tiers:
            if tier not in ("High", "Medium", "Low"):
                raise ConfigError("post_tiers entries must be High, Medium or Low")

    def all_exclude_patterns(self) -> List[str]:
        """Files left out of the diff, and about which no findings are reported."""
        return list(self.exclude_patterns) + list(self.extra_exclude_patterns)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def runs_path(self) -> Path:
        return Path(self.runs_dir).expanduser() if self.runs_dir else DATA_DIR / "runs"


def _read_toml(path: Path) -> Dict[str, Any]:
    try:
        import tomllib as toml_lib  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        try:
            import tomli as toml_lib  # type: ignore[import-not-found,no-redef]
        except ModuleNotFoundError as exc:
            raise ConfigError(
                "reading %s needs Python 3.11+ or the 'tomli' package (pip install tomli)" % path
            ) from exc
    return toml_lib.loads(_operator_bytes(path).decode("utf-8"))


def _operator_bytes(path: Path) -> bytes:
    if os.name != "posix":
        raise ConfigError("trusted operator configuration currently requires POSIX file controls")
    # Pin each directory descriptor so a concurrent symlink swap cannot redirect
    # the configuration open. Canonical parents also support macOS /tmp aliases.
    if path.is_symlink():
        raise ConfigError("operator configuration must not be a symlink")
    canonical = path.parent.resolve(strict=True) / path.name
    if any((parent / ".git").exists() for parent in canonical.parents):
        raise ConfigError("operator configuration must be outside Git working trees")
    directory = os.open(canonical.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in canonical.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(canonical.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536
                or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022):
            raise ConfigError("operator configuration must be bounded, owned and not writable by other users")
        data = handle.read(65537)
    if len(data) > 65536:
        raise ConfigError("operator configuration grew beyond its limit")
    return data


def _coerce(name: str, value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError("%s must be true or false" % name)
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError("%s must be an integer" % name)
        return value
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError("%s must be a number" % name)
        try:
            result = float(value)
        except OverflowError as exc:
            raise ConfigError("%s must be a finite number" % name) from exc
        if not math.isfinite(result):
            raise ConfigError("%s must be a finite number" % name)
        return result
    if isinstance(default, list):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError("%s must be a list of strings" % name)
        return list(value)
    if isinstance(default, str):
        if not isinstance(value, str):
            raise ConfigError("%s must be a string" % name)
        return value
    return value


def find_config_file(explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists() and not path.is_symlink():
            raise ConfigError("config file not found: %s" % path)
        return path
    for candidate in (DATA_DIR / "review.toml",):
        if candidate.exists() or candidate.is_symlink():
            return candidate
    return None


def load_config(explicit: Optional[str] = None) -> Config:
    cfg = Config()
    path = find_config_file(explicit)
    if path is not None:
        try:
            data = _read_toml(path)
        except (OSError, ValueError) as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError("cannot read valid trusted operator configuration") from exc
        known = {f.name: f for f in fields(Config)}
        for key, value in data.items():
            if key not in known:
                raise ConfigError("unknown config key: %s" % key)
            setattr(cfg, key, _coerce(key, value, getattr(Config(), key)))
    cfg.validate()
    return cfg
