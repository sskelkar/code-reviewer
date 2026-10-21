"""Persona-owned results keep scheduling, agreement and annotations separate."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from ...config import Config
from ...shared.criteria import CriteriaSet, Rule
from ...shared.models import PassRecord, PRContext
from ...shared.stats import Stats
from .master import MasterList


@dataclass
class PersonaPass(PassRecord):
    persona: str = ''
    round_no: int = 0
    status: str = 'skipped'
    prompt_hashes: Dict[str, str] = field(default_factory=dict)


@dataclass
class RunResult:
    ctx: PRContext
    cfg: Config
    run_dir: Path
    master: MasterList
    stats: Stats
    criteria: CriteriaSet
    passes: List[PersonaPass]
    report_path: Path
    stop_reason: str = 'completed'
    assignments: Dict[str, List[Rule]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    diagnostics: List[Any] = field(default_factory=list)
    diff_summary: Dict[str, Any] = field(default_factory=dict)
    budget_pools: Dict[str, Any] = field(default_factory=dict)

    @property
    def completed_by_persona(self) -> Dict[str, int]:
        from .criteria import PERSONAS
        return {p: sum(s.status == 'completed' and s.persona == p for s in self.passes) for p in PERSONAS}

    @property
    def passes_completed(self) -> int:
        return sum(self.completed_by_persona.values())


@dataclass
class DryRun:
    ctx: PRContext
    run_dir: Path
    assignments: Dict[str, List[Rule]]
    diff_summary: Dict[str, Any]
    model: str
    review_mode: str = 'agent-personas'
    passes_per_persona: int = 3
    parallel: int = 4
    budget_pools: Dict[str, Any] = field(default_factory=dict)
