"""Configuration for the review harness."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(os.environ.get("CODE_REVIEWER_HOME") or Path(__file__).resolve().parents[2])
PROMPTS_DIR = REPO_ROOT / "prompts"
DEFAULT_CRITERIA_DIR = REPO_ROOT / "criteria" / "defaults"
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
    max_diff_lines: int = 1500
    criteria_max_chars: int = 200000
    exclude_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_PATTERNS))
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

    def validate(self) -> None:
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
    with open(path, "rb") as handle:
        return toml_lib.load(handle)


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
        return float(value)
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
        if not path.is_file():
            raise ConfigError("config file not found: %s" % path)
        return path
    for candidate in (Path.cwd() / "review.toml", DATA_DIR / "review.toml"):
        if candidate.is_file():
            return candidate
    return None


def load_config(explicit: Optional[str] = None) -> Config:
    cfg = Config()
    path = find_config_file(explicit)
    if path is not None:
        data = _read_toml(path)
        known = {f.name: f for f in fields(Config)}
        for key, value in data.items():
            if key not in known:
                raise ConfigError("unknown config key: %s" % key)
            setattr(cfg, key, _coerce(key, value, getattr(Config(), key)))
    cfg.validate()
    return cfg
