"""Category-grouped persona findings, agreement by source and conflict annotations."""
from __future__ import annotations

from ..shared.git_content import omission_notice

import json
import re
from typing import Any

from ..shared.findings import SEVERITY_RANK
from .master import agreement
from .verification import publishable, counts


# Display order only: scheduling, budgets and finding authority are unchanged.
CATEGORY_TITLES = {
    'correctness': 'Correctness',
    'production-safety': 'Production safety',
    'test-quality': 'Test quality',
    'code-quality': 'Code quality',
}


def _groups(items):
    sections = {key: [] for key in CATEGORY_TITLES}
    for item in items:
        primary = next(key for key in CATEGORY_TITLES if key in item['categories'])
        sections[primary].append(item)
    for key, findings in sections.items():
        if findings:
            findings.sort(key=lambda item: (
                -SEVERITY_RANK[item['canonical']['severity']],
                item['canonical']['file'], item['canonical'].get('line') or 0,
                item['canonical']['title'], item['id'],
            ))
            yield CATEGORY_TITLES[key], findings


def fence(text: str) -> str:
    marker = '`' * max(3, max((len(s) for s in re.findall(r'`+', text)), default=0) + 1)
    return marker + '\n' + text + '\n' + marker


def render_finding(result: Any, item: Any) -> str:
    c = item['canonical']
    rates = agreement(item, result.completed_by_persona, result.cfg)
    rule = result.criteria.index().get(c['rule_id'], {})
    lines = ['#### [%s] %s' % (c['severity'], c['title']),
             '', '`%s:%s`' % (c['file'], c['line']),
             'Categories: ' + ', '.join(CATEGORY_TITLES[key] for key in CATEGORY_TITLES if key in item['categories']),
             'Criterion: %s (%s)' % (rule.get('heading', 'Unspecified'), rule.get('path', 'no cited rule')),
             'Agreement: ' + ('; '.join('%s: %d/%d (%s)' % (CATEGORY_TITLES[p], a['seen'], a['completed'], a['tier'])
                                       for p, a in rates.items()) or 'self-check rejected only; not scored'),
             '', c['description']]
    if c.get('quote'):
        lines += ['', fence(c['quote'])]
    if c.get('anchor'):
        lines += ['', 'Anchor: %s; expected: %s' % (c['anchor'], c.get('expected', ''))]
    assessment = item['verification']
    lines += ['', 'Verification: supported. ' + assessment['explanation']]
    if c.get('conditions'):
        lines += ['Conditions: ' + '; '.join(c['conditions'])]
    lines += ['Impact: ' + c.get('consequence', '')]
    evidence = {entry['id']: entry for entry in c.get('evidence', []) + assessment['additional_evidence']}
    for ref in assessment['evidence_refs']:
        if ref != 'primary':
            entry = evidence[ref]
            location = ('%s:%s' % (entry['file'], entry['line'])) if entry['source'] == 'head' else entry['rule_id']
            lines += ['Evidence `%s`: %s' % (location, entry['establishes']), fence(entry['quote'])]
    if c.get('suggestion') and assessment['remedy']['status'] == 'supported':
        lines += ['', 'Suggestion (supported, not an executed/tested patch): ' + c['suggestion']]
    if item['rejected_sightings']:
        lines += ['', 'Self-check rejections (not votes): ' + str(item['rejected_sightings'])]
    return '\n'.join(lines)


def run_line(result: Any) -> str:
    return 'Multi-perspective review: %d/%d completed · %s · %s' % (
        result.passes_completed, len(result.passes), result.stop_reason,
        'subscription (no API charge)' if result.cfg.auth_mode == 'subscription' else '$%.4f estimated' % result.stats.total_cost())


def render_summary(result: Any, tiers: Any, dropped: Any) -> str:
    items = [i for i in result.master.scored() if publishable(i) and i['id'] not in dropped and any(
        a['tier'] in tiers or a['tier'] == 'Unrated'
        for a in agreement(i, result.completed_by_persona, result.cfg).values())]
    totals = counts(result.master)
    lines = ['## AI-generated persona review: %s' % result.ctx.label, '', run_line(result),
             'Model: %s. Agreement measures repeated sightings, not correctness. Only independently supported candidates are published; support is an evidence-based judgment, not a correctness guarantee.' % result.cfg.model_reviewer,
             '', 'Completed passes: ' + ', '.join('%s %d/%d' % (CATEGORY_TITLES[p], result.completed_by_persona[p], result.cfg.persona_passes) for p in CATEGORY_TITLES),
             '', ('**No review completed.** Findings could not be finalized; this does not mean the change is issue-free. '
                  'Inspect the full report for failures and skipped work.' if result.passes_completed == 0 else
                  '%d supported finding%s shown.' % (len(items), '' if len(items) == 1 else 's')),
             'Verification: %d candidates; %d supported, %d refuted, %d unresolved, %d not completed.' % (
                 totals['candidates'], totals['supported'], totals['refuted'], totals['unresolved'], totals['pending'])]
    if totals['pending'] or totals['unresolved'] or totals['supported'] > totals['published']:
        lines += ['**Verification incomplete.** Withheld candidates are not a clean-review result.']
    for title, findings in _groups(items):
        lines += ['### %s (%d)' % (title, len(findings))]
        lines.extend(render_finding(result, item) for item in findings)
    if result.warnings:
        lines += ['', 'Warnings:'] + ['- ' + w for w in result.warnings]
    if omission_notice(result.ctx):
        lines += [omission_notice(result.ctx)]
    return '\n\n'.join(line for line in lines if line) + '\n'


def render_report(result: Any) -> str:
    lines = [render_summary(result, ('High', 'Medium', 'Low'), []), '## Criteria assigned to each persona']
    for persona in CATEGORY_TITLES:
        lines += ['### ' + CATEGORY_TITLES[persona]]
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
    lines += ['## Budget pools', fence(json.dumps(result.budget_pools, indent=2)), '## Usage and validation', fence(json.dumps(result.stats.to_dict(), indent=2)),
              'Model reply validation errors: ' + str(result.diagnostics),
              'Other counted normalization checks: ' + str({key: sum(getattr(s, key) for s in result.passes) for key in (
                  'rule_repaired', 'rule_unknown', 'self_check_missing', 'line_corrected', 'rejected_by_self_check')}),
              'Run artifacts: input/personas/ (frozen prompts/settings), input/classifier*, pass-*/ (requests, replies, findings, mapping, slot), '
              'verification/V-*/ (requests/replies), master.json, findings.json (private audit and supported-only findings), stats.json. Actual request logs include validation-retry feedback.']
    return '\n\n'.join(lines) + '\n'


def render_dry_run(dry: Any) -> str:
    return '\n'.join(['Dry run: multi-perspective review. No model calls, posting or checkout creation.',
        '%d passes per persona (%d scheduled). Defaults only: project discovery and classification are omitted.' % (
            dry.passes_per_persona, len(dry.assignments) * dry.passes_per_persona),
        'Concurrent calls: %d; budget allocation: %s' % (dry.parallel, ', '.join(('%s %d tokens' % (p, v['allocated_tokens'])) if 'allocated_tokens' in v else ('%s $%.2f' % (p, v['allocated_usd'])) for p, v in dry.budget_pools.items())),
        'Model: %s; tools: Read,Grep,Glob in the temporary head checkout.' % dry.model,
        'WHAT THE REVIEWERS ARE GIVEN', dry.ctx.title, dry.ctx.body, dry.ctx.commit_messages,
        ('Email redaction is ON; addresses replaced with [email]: %d replaced above.' % dry.redacted_emails
         if dry.redact_emails else 'Warning: email redaction is OFF.'),
        'Diff: %s' % dry.diff_summary] + ['%s: %d default rules; prompts saved under input/personas/%s/' % (p, len(r), p)
        for p, r in dry.assignments.items()] + ['Saved in %s' % dry.run_dir])
