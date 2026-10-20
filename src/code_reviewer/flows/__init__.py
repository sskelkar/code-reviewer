"""Explicit mode dispatch; importing this module does not load either flow's assets."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..config import Config, ConfigError, DEFAULT_CRITERIA_DIR, PROMPTS_DIR
from ..shared.events import EventSink


def create_flow(cfg: Config, runner: Any, emit: Optional[EventSink] = None,
                prompts_dir: Optional[Path] = None, defaults_dir: Optional[Path] = None) -> Any:
    if cfg.review_mode == "single-agent":
        from .single_agent.flow import SingleAgentFlow
        return SingleAgentFlow(cfg, runner, emit, prompts_dir or PROMPTS_DIR, defaults_dir or DEFAULT_CRITERIA_DIR)
    if cfg.review_mode == "agent-personas":
        from .agent_personas.flow import AgentPersonasFlow
        return AgentPersonasFlow(cfg, runner, emit)
    raise ConfigError("unknown review mode: %s" % cfg.review_mode)
