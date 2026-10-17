"""The shareable review summary: what you read in the terminal, copy, or post as the PR comment.

The terminal output, `summary.md` in the run directory and the posted PR comment all come from `render_summary`, so what
you see is exactly what would be posted. The long report (`report.md`) is for digging into what the agent did.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from .findings import SEVERITY_RANK
from .master import MIN_PASSES_FOR_CONFIDENCE, UNRATED, rate
from .models import RunResult
from .report import TIERS, fence, money


def _plural(count: int) -> str:
    return "%d finding%s" % (count, "" if count == 1 else "s")


def _level(result: RunResult, item: Dict[str, Any]) -> str:
    cfg = result.cfg
    return rate(item, result.passes_completed, cfg.tier_high, cfg.tier_medium)


def select_findings(result: RunResult, tiers: Sequence[str], dropped: Sequence[str]) -> List[Dict[str, Any]]:
    """Scored findings to show: the chosen tiers (unrated single-pass findings are always included), minus dropped ones."""
    order = TIERS + (UNRATED,)
    chosen = []
    for item in result.master.scored():
        level = _level(result, item)
        if (level in tiers or level == UNRATED) and item["id"] not in dropped:
            chosen.append((order.index(level), -SEVERITY_RANK.get(item["canonical"]["severity"], 0), item["id"], item))
    chosen.sort(key=lambda entry: entry[:3])
    return [entry[3] for entry in chosen]


def _headline(result: RunResult, tiers: Sequence[str], dropped: Sequence[str], shown: List[Dict[str, Any]]) -> str:
    if result.passes_completed == 0:
        return "No pass completed, so there is no review to share. The full report has the details."
    scored = result.master.scored()
    if not scored:
        return "**No findings.**"
    if result.passes_completed < MIN_PASSES_FOR_CONFIDENCE:
        return "**%s (unrated: only one pass completed, so confidence cannot be measured)**" % _plural(len(shown))
    if not shown:
        return "**No findings to show** (selected confidence levels: %s)." % ", ".join(tiers)
    counts = {tier_name: sum(1 for item in shown if _level(result, item) == tier_name) for tier_name in TIERS}
    parts = ["%d %s confidence" % (counts[name], name.lower()) for name in TIERS if counts[name]]
    text = "**%s: %s**" % (_plural(len(shown)), ", ".join(parts))
    hidden = sum(1 for item in scored if _level(result, item) not in tiers and item["id"] not in dropped)
    if hidden:
        text += " · %s at other confidence levels not shown" % _plural(hidden)
    return text


def _finding_block(item: Dict[str, Any], level: str, passes_completed: int) -> List[str]:
    canon = item["canonical"]
    location = "%s:%d" % (canon["file"], canon["line"]) if canon.get("line") else canon["file"]
    confidence_label = "unrated" if level == UNRATED else "%s confidence" % level
    lines = [
        "### %s · %s severity · %s" % (item["id"], canon["severity"], confidence_label),
        "**%s**" % canon["title"],
        "`%s` · seen in %d/%d pass%s" % (
            location, len(item["seen_in_passes"]), passes_completed, "" if passes_completed == 1 else "es"),
        "",
        canon["description"],
    ]
    if canon.get("quote"):
        lines += ["", fence(canon["quote"])]
    elif canon.get("expected"):
        lines += ["", "**Expected:** %s" % canon["expected"]]
    if canon.get("suggestion"):
        lines += ["", "**Suggestion:** %s" % canon["suggestion"]]
    lines.append("")
    return lines


def render_summary(result: RunResult, tiers: Sequence[str], dropped: Sequence[str]) -> str:
    """Markdown summary of the review's findings at the chosen confidence tiers, without the dropped ones."""
    cfg, ctx = result.cfg, result.ctx
    shown = select_findings(result, tiers, dropped)
    confidence_note = ("Confidence is the share of passes that found an issue." if result.passes_completed >= MIN_PASSES_FOR_CONFIDENCE
                       else "Only one pass completed, so findings carry no confidence rating.")
    lines = [
        "## AI-generated code review (experimental)",
        "",
        "Reviewed commit `%s` with %d independent pass(es) of `%s`. %s "
        "This review is advisory and has not been verified by a human reviewer." % (
            ctx.head_sha[:12], result.passes_completed, cfg.model_reviewer, confidence_note),
        "",
        _headline(result, tiers, dropped, shown),
        "",
    ]
    for item in shown:
        lines += _finding_block(item, _level(result, item), result.passes_completed)
    return "\n".join(lines).rstrip() + "\n"


_STOP_SHORT = {
    "converged": "converged",
    "max_passes": "pass limit reached",
    "budget_exceeded": "stopped: budget exceeded",
    "failures": "stopped: repeated failures",
    "cancelled": "cancelled",
}


def run_line(result: RunResult) -> str:
    """One line about the run itself (not part of the shareable summary)."""
    stats = result.stats
    return "Run: %d pass%s, %s · est. cost %s · %.0fs of model time" % (
        result.passes_completed, "" if result.passes_completed == 1 else "es",
        _STOP_SHORT.get(result.stop_reason, result.stop_reason), money(stats.total_cost(), stats.cost_known()), stats.duration_s())
