"""Explicit unavailable-mode boundary until the agreed persona flow is implemented."""
from __future__ import annotations

from typing import Any

from ...config import ConfigError


class AgentPersonasFlow:
    def __init__(self, *args: Any, **kwargs: Any):
        raise ConfigError("agent-personas is not implemented yet; use --mode single-agent")
