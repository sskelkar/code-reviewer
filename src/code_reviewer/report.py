"""Render the final Markdown report from the master list and run statistics. Pure formatting, no decisions."""
from __future__ import annotations

import re
from typing import Any, Dict, List

from .findings import SEVERITY_RANK
from .master import MIN_PASSES_FOR_CONFIDENCE, UNRATED, MasterList, pass_overlap, rate
from .models import RunResult

TIERS = ("High", "Medium", "Low")
SEVERITIES = ("high", "medium", "low", "nit")
STOP_TEXT = {
    "converged": "converged (no new findings for consecutive passes)",
    "budget_exceeded": "stopped: run budget exceeded",
    "failures": "stopped: repeated pass failures",
    "cancelled": "cancelled by the user",
}


def stop_text(result: RunResult) -> str:
    if result.stop_reason == "max_passes":
        return "reached the pass limit (%d)" % result.cfg.max_passes
    return STOP_TEXT.get(result.stop_reason, result.stop_reason)


def fence(text: str, lang: str = "") -> str:
    longest = max([len(m) for m in re.findall(r"`+", text)] + [2])
    ticks = "`" * (longest + 1)
    return "%s%s\n%s\n%s" % (ticks, lang, text, ticks)


def money(value: float, known: bool = True) -> str:
    return ("$%.3f" % value) + ("" if known else "+")


def count(number: int) -> str:
    return "{:,}".format(number)


def _location(canonical: Dict[str, Any]) -> str:
    line = canonical.get("line") or 0
    return "%s:%d" % (canonical["file"], line) if line else canonical["file"]


def tiered(master: MasterList, passes_completed: int, high: float, medium: float) -> Dict[str, List[Dict[str, Any]]]:
    """Scored findings grouped by confidence tier, most severe first.

    With fewer than two completed passes there is no agreement to measure, so there is a single "Unrated" group.
    """
    names = TIERS if passes_completed >= MIN_PASSES_FOR_CONFIDENCE else (UNRATED,)
    groups: Dict[str, List[Dict[str, Any]]] = {name: [] for name in names}
    for item in master.scored():
        groups[rate(item, passes_completed, high, medium)].append(item)
    for items in groups.values():
        items.sort(key=lambda i: (-SEVERITY_RANK.get(i["canonical"]["severity"], 0), i["id"]))
    return groups


def rule_label(rule_id: str, rules: Dict[str, Any]) -> str:
    """The criterion in words, for people: its name and where it comes from, never its internal ID. None for no rule."""
    rule = rules.get(rule_id)
    if not rule:
        return ""
    if rule["source"] == "default":
        return "%s (default criteria)" % rule["heading"]
    return "%s (`%s`)" % (rule["heading"], rule["path"])


def humanize(text: str, rules: Dict[str, Any]) -> str:
    """Replace rule IDs the model wrote into free text (for example "violates GEN-004") with the rule's name."""
    for rule_id in sorted(rules, key=len, reverse=True):
        pattern = r"(?<![\w:/#.-])\[?`?%s`?\]?(?![\w-])" % re.escape(rule_id)
        text = re.sub(pattern, lambda _m, name=rules[rule_id]["heading"]: '"%s"' % name, text)
    return text


def render_finding(item: Dict[str, Any], passes_completed: int, rules: Dict[str, Any], heading_level: str = "###") -> str:
    canon, seen = item["canonical"], item["seen_in_passes"]
    label = rule_label(canon["rule_id"], rules)
    lines = [
        "%s %s · %s · %s · `%s`" % (heading_level, item["id"], canon["severity"], canon["kind"], _location(canon)),
        "**%s**" % humanize(canon["title"], rules),
        "",
        "Seen in **%d/%d** passes (%s)" % (len(seen), passes_completed, ", ".join(str(p) for p in seen)),
        "Criterion: %s" % label if label else "Criterion: none (general concern)",
        "",
        humanize(canon["description"], rules),
        "",
    ]
    if canon.get("quote"):
        lines += ["Evidence:", fence(canon["quote"]), ""]
    elif canon.get("anchor"):
        anchor = canon["anchor"]
        symbol = " (%s)" % anchor["symbol"] if anchor.get("symbol") else ""
        lines.append("Anchor: `%s` lines %s-%s%s" % (anchor.get("file", canon["file"]), anchor.get("line_start"), anchor.get("line_end"), symbol))
        if canon.get("expected"):
            lines.append("Expected: %s" % humanize(canon["expected"], rules))
        lines.append("")
    if canon.get("suggestion"):
        lines += ["Suggestion: %s" % humanize(canon["suggestion"], rules), ""]
    if item["rejected_in_passes"]:
        reasons = "; ".join("pass %s: %s" % (p, r or "no reason given")
                            for p, r in sorted(item["rejected_reasons"].items(), key=lambda kv: int(kv[0])))
        lines += ["Also rejected in self-check: %s" % reasons, ""]
    if len(set(item["severities_seen"])) > 1:
        lines += ["Severity varied across passes: %s" % ", ".join(item["severities_seen"]), ""]
    return "\n".join(lines)


# --- sections ------------------------------------------------------------------------

def _header(result: RunResult) -> List[str]:
    cfg, ctx, stats = result.cfg, result.ctx, result.stats
    failed = sum(1 for p in result.passes if not p.ok)
    return [
        "# Code review: %s" % ctx.label, "", "**%s**" % ctx.title, "",
        "| | |", "|---|---|",
        "| Pull request | %s |" % (ctx.url or ctx.label),
        "| Head / base | `%s` / `%s` (%s) |" % (ctx.head_sha[:12], ctx.base_sha[:12], ctx.base_ref),
        "| Models | reviewer `%s`, comparator `%s`, resolver `%s` |" % (cfg.model_reviewer, cfg.model_comparator, cfg.model_resolver),
        "| Self-check | %s |" % ("on" if cfg.self_check else "off"),
        "| Passes completed | %d of %d max (%d failed) |" % (result.passes_completed, cfg.max_passes, failed),
        "| Stop reason | %s |" % stop_text(result),
        "| Diff | %(files)d files, %(changed_lines)d changed lines, %(excluded)d excluded |" % result.diff_summary,
        "| Estimated cost | %s, %s tokens |" % (money(stats.total_cost(), stats.cost_known()), count(stats.total_tokens())),
        "",
    ]


def _criteria_section(result: RunResult) -> List[str]:
    criteria, project = result.criteria, result.criteria.project
    defaults = sum(1 for r in criteria.rules if r.source == "default")
    out = ["## Criteria applied", "",
           "- Mode: `%s`; %d default rule(s), %d project rule(s)" % (criteria.mode, defaults, len(criteria.rules) - defaults)]
    if project.files_used:
        out.append("- Project criteria files (read from the base branch): %s" % ", ".join("`%s`" % f for f in project.files_used))
    out += ["- Added because they cover the changed files: `%s` (%s)" % (e["path"], e["reason"]) for e in project.auto_included]
    if project.notes:
        out.append("- How the project organizes its criteria: %s" % project.notes)
    out += ["- Ignored reference: `%s` (%s)" % (e["reference"], e["reason"]) for e in project.ignored]
    out += ["- **Warning:** %s" % w for w in criteria.warnings]
    return out + [""]


def _summary_section(result: RunResult, groups: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    scored = result.master.scored()
    out = [
        "## Overview", "",
        "| Confidence | %s | Total |" % " | ".join(groups), "|---" * (len(groups) + 2) + "|",
        "| Findings | %s | %d |" % (" | ".join(str(len(items)) for items in groups.values()), len(scored)),
        "",
        "By severity: " + ", ".join("%s %d" % (s, sum(1 for i in scored if i["canonical"]["severity"] == s)) for s in SEVERITIES),
        "",
    ]
    if result.passes_completed == 0:
        out += ["_No pass completed, so there are no findings. See the appendix for the failure details._", ""]
    elif result.passes_completed < MIN_PASSES_FOR_CONFIDENCE:
        out += ["_Only one pass completed, so findings are **unrated**: confidence is the share of passes that found an issue, "
                "and it cannot be measured from a single pass._", ""]
    return out


def _tier_sections(result: RunResult, groups: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    cfg, rules = result.cfg, result.criteria.index()
    bounds = {
        "High": "at least %d%%" % round(cfg.tier_high * 100),
        "Medium": "%d-%d%%" % (round(cfg.tier_medium * 100), round(cfg.tier_high * 100) - 1),
        "Low": "under %d%%" % round(cfg.tier_medium * 100),
    }
    out: List[str] = []
    for name, items in groups.items():
        if not items:
            continue
        if name == UNRATED:
            out += ["## Findings, unrated (%d)" % len(items), "_Single pass: no agreement data. Run at least two passes to get confidence._", ""]
        else:
            out += ["## %s confidence (%d)" % (name, len(items)), "_Found in %s of passes._" % bounds[name], ""]
        for item in items:
            out += [render_finding(item, result.passes_completed, rules), ""]
    return out


def _rejected_section(result: RunResult) -> List[str]:
    rejected_only = result.master.rejected_only()
    if not rejected_only:
        return []
    rules = result.criteria.index()
    out = ["## Rejected by self-check (not scored)", "",
           "The model raised these and then refuted them in its own self-check. Listed for transparency.", ""]
    for item in rejected_only:
        canon = item["canonical"]
        reason = "; ".join(r for r in item["rejected_reasons"].values() if r) or "no reason given"
        out.append("- **%s** `%s` %s: %s (rejected in passes %s)" % (
            item["id"], _location(canon), humanize(canon["title"], rules), humanize(reason, rules),
            ", ".join(str(p) for p in item["rejected_in_passes"])))
    return out + [""]


def _pass_table(result: RunResult) -> List[str]:
    out = ["### Discovery curve and per-pass usage", "",
           "| Pass | Status | New | Matched | Usable | Self-rejected | Ungrounded | Unknown rule | Reviewer cost | Comparator cost | Tokens |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in result.passes:
        if not p.ok:
            out.append("| %d | failed: %s | | | | | | | | | |" % (p.pass_no, p.error.replace("|", "/")[:80]))
            continue
        out.append("| %d | ok | %d | %d | %d | %d | %d | %d | %s | %s | %s |" % (
            p.pass_no, p.new_scored, p.matched, p.usable, p.rejected_by_self_check, p.ungrounded, p.rule_unknown,
            money(p.reviewer_cost_usd), money(p.comparator_cost_usd), count(p.reviewer_tokens + p.comparator_tokens)))
    return out + [""]


def _overlap_section(result: RunResult) -> List[str]:
    overlap = pass_overlap(result.master, [p.pass_no for p in result.passes if p.ok])
    if overlap["mean"] is None:
        return []
    return ["### Pass-to-pass overlap", "",
            "Mean pairwise Jaccard overlap of findings between passes: **%.2f** (1.00 means every pass found exactly the same "
            "findings, which makes the confidence tiers uninformative)." % overlap["mean"], ""]


def _usage_section(result: RunResult) -> List[str]:
    cfg, stats = result.cfg, result.stats
    known = stats.cost_known()
    totals, counts = stats.token_totals(), stats.retries_and_failures()
    out = ["### Usage and cost (estimated)", "", "| Role | Calls | Tokens | Cost |", "|---|---|---|---|"]
    for role, entry in sorted(stats.by_role().items()):
        out.append("| %s | %d | %s | %s |" % (role, entry["calls"], count(entry["tokens"]), money(entry["cost_usd"], known)))
    note = "" if known else " Some calls reported no cost, so the total is a lower bound (marked +)."
    return out + [
        "",
        "- Total: %s, %s tokens (input %s, output %s, cache write %s, cache read %s)" % (
            money(stats.total_cost(), known), count(stats.total_tokens()), count(totals["input"]), count(totals["output"]),
            count(totals["cache_creation"]), count(totals["cache_read"])),
        "- Claude invocations: %d (%d retries, %d failed); model time %.0fs" % (
            counts["calls"], counts["retries"], counts["failed_calls"], stats.duration_s()),
        "- Budget: %s of %s; %s of %s counted tokens (input, output and cache writes; cache reads are not counted)" % (
            money(stats.total_cost(), known), money(cfg.run_budget_usd), count(stats.budget_tokens()), count(cfg.run_budget_tokens)),
        "- Costs are estimates reported by Claude Code and may not match billing." + note,
        "",
    ]


def _counters_section(result: RunResult) -> List[str]:
    ok = [p for p in result.passes if p.ok]

    def total(field: str) -> int:
        return sum(getattr(p, field) for p in ok)

    return [
        "### Other counters", "",
        "- Ungrounded findings (evidence not found): %d" % total("ungrounded"),
        "- Findings located in excluded files (dropped): %d" % total("out_of_scope"),
        "- Rule citations that only needed reformatting (for example brackets), repaired: %d" % total("rule_repaired"),
        "- Findings citing a rule that does not exist (kept, rule set to none): %d" % total("rule_unknown"),
        "- Malformed findings: %d" % total("malformed"),
        "- Findings where self-check output was missing (treated as confirmed): %d" % total("self_check_missing"),
        "- Findings whose line number was corrected to the quote location: %d" % total("line_corrected"),
        "", "Artifacts: `%s`" % result.run_dir, "",
    ]


def render_report(result: RunResult) -> str:
    cfg = result.cfg
    groups = tiered(result.master, result.passes_completed, cfg.tier_high, cfg.tier_medium)
    sections = [
        _header(result),
        _criteria_section(result),
        _summary_section(result, groups),
        _tier_sections(result, groups),
        _rejected_section(result),
        ["## Appendix", ""],
        _pass_table(result),
        _overlap_section(result),
        _usage_section(result),
        _counters_section(result),
    ]
    return "\n".join(line for section in sections for line in section)
