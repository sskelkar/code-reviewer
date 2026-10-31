"""Persona reports show agreement by source, and verification as separate annotations."""
from __future__ import annotations

from ...shared.git_content import omission_notice

import json
import re
from typing import Any

from ...shared.findings import SEVERITY_RANK
from .criteria import PERSONAS
from .master import agreement


def fence(text: str) -> str:
    marker = '`' * max(3, max((len(s) for s in re.findall(r'`+', text)), default=0) + 1)
    return marker + '\n' + text + '\n' + marker


def render_finding(result: Any, item: Any) -> str:
    c = item['canonical']
    rates = agreement(item, result.completed_by_persona, result.cfg)
    rule = result.criteria.index().get(c['rule_id'], {})
    lines = ['### %s [%s] %s' % (item['id'], c['severity'], c['title']),
             '', '`%s:%s`' % (c['file'], c['line']),
             'Categories: ' + ', '.join(item['categories']),
             'Criterion: %s (%s)' % (rule.get('heading', 'Unspecified'), rule.get('path', 'no cited rule')),
             'Agreement: ' + ('; '.join('%s: %d/%d (%s)' % (p, a['seen'], a['completed'], a['tier'])
                                       for p, a in rates.items()) or 'self-check rejected only; not scored'),
             '', c['description']]
    if c.get('quote'):
        lines += ['', fence(c['quote'])]
    if c.get('anchor'):
        lines += ['', 'Anchor: %s; expected: %s' % (c['anchor'], c.get('expected', ''))]
    if c.get('suggestion'):
        lines += ['', 'Suggestion: ' + c['suggestion']]
    if item['rejected_sightings']:
        lines += ['', 'Self-check rejections (not votes): ' + str(item['rejected_sightings'])]
    return '\n'.join(lines)


def run_line(result: Any) -> str:
    return 'agent-personas: %d/%d completed · %s · $%.4f estimated' % (
        result.passes_completed, len(result.passes), result.stop_reason, result.stats.total_cost())


def conflict_text(group: Any, visible: Any) -> str:
    if not set(group['findings']) & visible:
        return ''
    lines = ['### %s: %s (%s)' % (group['id'], ', '.join(group['findings']), group['status']), '', group['reason']]
    if not set(group['findings']) <= visible:
        return lines[0] + '\n\nSome claims are manually omitted or filtered; consult the full report for context.'
    annotation = group['annotation']
    if annotation:
        lines += ['', 'Verification: %s. %s' % (annotation['outcome'], annotation['explanation'])]
        for claim in annotation['claims']:
            lines += ['- %s: %s — %s' % (claim['finding_id'], claim['status'], claim['explanation'])]
            for evidence in claim['evidence']:
                lines += ['  Evidence `%s:%d`: %s' % (evidence['file'], evidence['line'], evidence['quote'])]
    elif group['error']:
        lines += ['', 'Unresolved: ' + group['error']]
    return '\n'.join(lines)


def render_summary(result: Any, tiers: Any, dropped: Any) -> str:
    items = [i for i in result.master.scored() if i['id'] not in dropped and any(
        a['tier'] in tiers or a['tier'] == 'Unrated'
        for a in agreement(i, result.completed_by_persona, result.cfg).values())]
    items.sort(key=lambda i: (-SEVERITY_RANK[i['canonical']['severity']], i['id']))
    visible = {i['id'] for i in items}
    lines = ['## AI-generated persona review: %s' % result.ctx.label, '', run_line(result),
             'Model: %s. Agreement measures repeated sightings, not correctness. Verification annotates without suppressing findings.' % result.cfg.model_reviewer,
             '', 'Completed passes: ' + ', '.join('%s %d/%d' % (p, result.completed_by_persona[p], result.cfg.persona_passes) for p in PERSONAS),
             '', '%d findings shown.' % len(items)]
    lines.extend(render_finding(result, item) for item in items)
    lines.extend(text for text in (conflict_text(g, visible) for g in result.master.conflicts.values()) if text)
    if result.warnings:
        lines += ['', 'Warnings:'] + ['- ' + w for w in result.warnings]
    if omission_notice(result.ctx):
        lines += [omission_notice(result.ctx)]
    return '\n\n'.join(lines) + '\n'


def render_report(result: Any) -> str:
    lines = [render_summary(result, ('High', 'Medium', 'Low'), []), '## Criteria assigned to each persona']
    for persona in PERSONAS:
        lines += ['### ' + persona]
        lines += ['- %s (%s: %s)' % (r.heading, r.source, r.path) for r in result.assignments.get(persona, [])]
        if persona not in result.assignments:
            lines += ['Assignment did not complete.']
    table = ['| Slot | Persona | Round | Status | Raised | Usable | Ungrounded | Malformed | Out of scope | Error |',
             '|---|---|---|---|---|---|---|---|---|---|']
    for s in result.passes:
        table += ['| %d | %s | %d | %s | %d | %d | %d | %d | %d | %s |' % (
            s.pass_no, s.persona, s.round_no, s.status, s.raised, s.usable, s.ungrounded, s.malformed, s.out_of_scope,
            ' '.join(s.error.replace('|', '/').splitlines()))]
    lines += ['## Scheduled passes', '\n'.join(table)]
    lines += ['## Rejected by self-check (not scored)']
    lines.extend(render_finding(result, i) for i in result.master.rejected_only())
    lines += ['## Budget pools', fence(json.dumps(result.budget_pools, indent=2)), '## Usage and validation', fence(json.dumps(result.stats.to_dict(), indent=2)),
              'Model reply validation errors: ' + str(result.diagnostics),
              'Other counted normalization checks: ' + str({key: sum(getattr(s, key) for s in result.passes) for key in (
                  'rule_repaired', 'rule_unknown', 'self_check_missing', 'line_corrected', 'rejected_by_self_check')}),
              'Run artifacts: input/personas/ (frozen prompts/settings), input/classifier*, pass-*/ (requests, replies, findings, mapping, slot), '
              'verification/C-*/ (requests/replies), master.json, stats.json. Actual request logs include validation-retry feedback.']
    return '\n\n'.join(lines) + '\n'


def render_dry_run(dry: Any) -> str:
    return '\n'.join(['Dry run: agent-personas. No model calls, posting or checkout creation.',
        '%d passes per persona (%d scheduled). Defaults only: project discovery and classification are omitted.' % (
            dry.passes_per_persona, len(dry.assignments) * dry.passes_per_persona),
        'Concurrent calls: %d; budget allocation: %s' % (dry.parallel, ', '.join('%s $%.2f' % (p, v['allocated_usd']) for p, v in dry.budget_pools.items())),
        'Model: %s; tools: Read,Grep,Glob in the temporary head checkout.' % dry.model,
        'Diff: %s' % dry.diff_summary] + ['%s: %d default rules; prompts saved under input/personas/%s/' % (p, len(r), p)
        for p, r in dry.assignments.items()] + ['Saved in %s' % dry.run_dir])
