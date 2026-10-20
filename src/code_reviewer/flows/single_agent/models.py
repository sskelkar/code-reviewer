"""Results owned by the single-agent flow."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from ...config import Config
from ...shared.criteria import CriteriaSet
from ...shared.models import PRContext, PassRecord
from ...shared.stats import Stats
from .master import MasterList

@dataclass
class RunResult:
    ctx: PRContext
    cfg: Config
    run_dir: Path
    master: MasterList
    stats: Stats
    stop_reason: str
    passes: List[PassRecord]
    passes_completed: int
    criteria: CriteriaSet
    report_path: Path
    diff_summary: Dict[str, Any] = field(default_factory=dict)
