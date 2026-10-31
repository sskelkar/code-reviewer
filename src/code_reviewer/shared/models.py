"""Plain data types shared across the engine, report and frontends."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List


class ReviewRefused(ValueError):
    """The PR cannot be reviewed (too large, nothing reviewable, ...)."""


class PassFailed(RuntimeError):
    """One pass could not produce a usable result."""


@dataclass
class PRContext:
    """A pull request pinned to exact commits."""

    repo: str
    number: int
    url: str
    title: str
    body: str
    commit_messages: str
    base_ref: str
    base_sha: str
    head_sha: str
    merge_base: str
    git_dir: Path
    host: str = "github.com"
    author: str = ""
    state: str = ""
    binary_omissions: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return "%s#%d" % (self.repo, self.number)


@dataclass
class ReviewInput:
    """Everything the Reviewer sees that stays the same in every pass."""

    ctx: PRContext
    checkout: Path
    criteria_block: str
    diff_block: str
    changed_files: str
    excluded_files: str


@dataclass
class PassRecord:
    """What happened in one pass. Failed passes only carry `error`."""

    pass_no: int
    ok: bool = True
    error: str = ""
    raised: int = 0
    usable: int = 0
    rejected_by_self_check: int = 0
    ungrounded: int = 0
    out_of_scope: int = 0   # findings located in excluded files (dropped)
    rule_repaired: int = 0   # rule citations that only needed reformatting
    rule_unknown: int = 0    # rule citations that matched no rule (finding kept, rule set to none)
    malformed: int = 0
    self_check_missing: int = 0
    line_corrected: int = 0
    new_total: int = 0      # master entries created in this pass
    new_scored: int = 0     # entries that gained their first confirmed sighting (drives convergence)
    matched: int = 0        # existing entries seen again
    reviewer_cost_usd: float = 0.0
    reviewer_tokens: int = 0
    comparator_cost_usd: float = 0.0
    comparator_tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
