"""Developer tool: re-run the comparator on the findings saved from a real review and show how the grouping changes.

    python -m code_reviewer.replay ~/.code-reviewer/runs/<run directory> [--model haiku] [--repeat 2]

The comparator decides which findings of different passes are the same issue, which drives every "found in 3 of 6 passes"
count. After editing `flows/single_agent/prompts/comparator.*.md`, replay a few real runs and check that duplicates merge and different
issues do not. This makes real (cheap) model calls for the comparator only; the Reviewer is not run again.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from .shared.claude_cli import ClaudeRunner
from .config import Config, ConfigError, load_config
from .shared.llm import ClaudeCaller
from .flows.single_agent.master import MasterList
from .flows.single_agent.roles import Comparator
from .shared.stats import Stats
from .flows.single_agent.prompt_contract import load_prompt

PassFindings = List[Tuple[int, List[Dict[str, Any]]]]
Groups = Dict[str, List[str]]  # master finding ID -> the local finding IDs merged into it
Decide = Callable[[MasterList, int, List[Dict[str, Any]]], Dict[str, Dict[str, Any]]]


def load_pass_findings(run_dir: Path) -> PassFindings:
    """The usable findings of each completed pass, as saved by the run. Findings that older runs set aside only because their
    rule citation was malformed (status `invalid_rule`) are included, since today they would be kept."""
    passes: PassFindings = []
    for path in sorted(run_dir.glob("pass-*/findings.json")):
        records = json.loads(path.read_text(encoding="utf-8")).get("findings", [])
        passes.append((int(path.parent.name.split("-")[1]), [r for r in records if r.get("status") in ("ok", "invalid_rule")]))
    return passes


def _group(passes: PassFindings, decide: Decide) -> Tuple[Groups, Dict[str, str]]:
    """Merge the passes one after another with `decide`, as a real run does. Returns (groups, local ID -> title)."""
    master = MasterList()
    groups: Groups = {}
    titles: Dict[str, str] = {}
    for pass_no, findings in passes:
        outcome = master.apply(pass_no, findings, decide(master, pass_no, findings))
        for local_id, master_id in outcome.assigned.items():
            groups.setdefault(master_id, []).append(local_id)
        titles.update({f["local_id"]: f["title"] for f in findings})
    return groups, titles


def recorded_grouping(run_dir: Path, passes: PassFindings) -> Tuple[Groups, Dict[str, str]]:
    """Group findings the way the original run did, by re-applying the comparator decisions it recorded."""
    def decisions_of(pass_no: int) -> Dict[str, Dict[str, Any]]:
        return json.loads((run_dir / ("pass-%02d" % pass_no) / "mapping.json").read_text(encoding="utf-8"))["decisions"]

    # Findings the original run set aside (for example for a malformed rule citation) have no recorded decision:
    # leave them out of the "recorded" grouping so it shows what that run actually reported.
    processed = [(n, [f for f in findings if f["local_id"] in decisions_of(n)]) for n, findings in passes]
    groups, _ = _group(processed, lambda master, pass_no, findings: decisions_of(pass_no))
    return groups, {f["local_id"]: f["title"] for _, findings in passes for f in findings}


def replay_grouping(passes: PassFindings, cfg: Config, runner: Any, stats: Optional[Stats] = None) -> Tuple[Groups, Dict[str, str]]:
    """Group findings by asking the current comparator prompt, one pass at a time, exactly as a real run would.

    Pass a `Stats` to collect the cost of the comparator calls."""
    scratch = Path(tempfile.mkdtemp(prefix="comparator-replay-"))
    comparator = Comparator(ClaudeCaller(runner, cfg, stats or Stats()), load_prompt("comparator"), cfg, scratch)

    def decide(master: MasterList, pass_no: int, findings: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        candidates = master.candidates(findings, cfg.candidate_window, cfg.candidate_all_threshold, cfg.candidate_max)
        if comparator.is_trivial(findings, candidates):
            return comparator.all_new(findings)
        return comparator.compare(pass_no, findings, candidates, scratch / ("pass-%02d.raw.json" % pass_no))

    return _group(passes, decide)


def _same_group_pairs(groups: Groups) -> Set[Tuple[str, str]]:
    return {pair for members in groups.values() for pair in combinations(sorted(members), 2)}


def changed_pairs(before: Groups, after: Groups) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """(pairs of findings that were separate and are now merged, pairs that were merged and are now separate)."""
    old, new = _same_group_pairs(before), _same_group_pairs(after)
    return sorted(new - old), sorted(old - new)


def describe(label: str, groups: Groups, titles: Dict[str, str]) -> str:
    lines = ["%s: %d distinct findings" % (label, len(groups))]
    for master_id, members in groups.items():
        lines.append("  %s, found in %d pass%s" % (master_id, len(members), "" if len(members) == 1 else "es"))
        lines += ["      %-8s %s" % (local_id, titles[local_id][:105]) for local_id in members]
    return "\n".join(lines)


def describe_changes(before: Groups, after: Groups, titles: Dict[str, str]) -> str:
    merged, split = changed_pairs(before, after)
    if not merged and not split:
        return "  grouping is identical to the original run"
    lines = []
    for label, pairs in (("now merged", merged), ("now split", split)):
        for a, b in pairs:
            lines.append("  %s: %s (%s) + %s (%s)" % (label, a, titles[a][:60], b, titles[b][:60]))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m code_reviewer.replay", description=__doc__.split("\n\n")[0])
    parser.add_argument("run_dir", help="a review run directory under ~/.code-reviewer/runs")
    parser.add_argument("--model", help="model for the comparator (default: model_comparator from review.toml)")
    parser.add_argument("--repeat", type=int, default=1, help="how many times to replay (the model is not deterministic)")
    parser.add_argument("--config", help="path to a review.toml")
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir).expanduser()
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print("config error: %s" % exc, file=sys.stderr)
        return 2
    if args.model:
        cfg = replace(cfg, model_comparator=args.model)
    passes = load_pass_findings(run_dir)
    if not passes:
        print("no pass-*/findings.json under %s" % run_dir, file=sys.stderr)
        return 1
    runner = ClaudeRunner(cfg.claude_bin, cfg.isolation_flags)

    before, titles = recorded_grouping(run_dir, passes)
    print(describe("RECORDED (the comparator used when the review ran)", before, titles))
    for attempt in range(1, args.repeat + 1):
        stats = Stats()
        after, _ = replay_grouping(passes, cfg, runner, stats)
        print("\n" + describe("REPLAY %d (current comparator prompt, model %s)" % (attempt, cfg.model_comparator), after, titles))
        print("  comparator cost of this replay: $%.4f over %d calls" % (stats.total_cost(), len(stats.calls)))
        print(describe_changes(before, after, titles))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
