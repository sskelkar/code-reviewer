"""Fresh persona Reviewer sessions, classification, consolidation and targeted verification."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from ...shared.criteria import render_criteria_block
from ...shared.llm import CallSpec, requires_lists
from ...shared.models import PassFailed
from ...shared.run_store import dump_json
from ...shared.templates import render
from .contracts import validate_annotation, validate_comparison
from .criteria import PERSONAS, routes, validate_routes
from .prompt_contract import digest, json_data, protect

REVIEW_TOOLS = 'Read,Grep,Glob'


def freeze(prompt: Any, persona: Any, standard: str, cfg: Any, inputs: Any) -> Tuple[str, str]:
    ctx = inputs.ctx
    system = render(prompt.system, {'persona_name': persona['name'], 'persona_focus': persona['focus'],
                                    'common_standard': standard}, {'self_check': cfg.self_check})
    variables = {
        'pr_number': ctx.number, 'repo': ctx.repo, 'base_ref': ctx.base_ref,
        'base_sha': ctx.base_sha[:12], 'head_sha': ctx.head_sha[:12],
        'pr_title': protect(ctx.title), 'pr_body': protect(ctx.body) or '(no description)',
        'commit_messages': protect(ctx.commit_messages) or '(none)', 'criteria_block': inputs.criteria_block,
        'changed_files_list': protect(inputs.changed_files), 'excluded_files_list': protect(inputs.excluded_files),
        'diff_block': protect(inputs.diff_block),
    }
    return system, render(prompt.user, variables)


def save_frozen(root: Path, persona: str, texts: Any, prompt: Any, cfg: Any) -> Dict[str, str]:
    directory = root / 'personas' / persona
    directory.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for kind, text in zip(('system', 'user'), texts):
        (directory / ('reviewer.' + kind + '.md')).write_text(text, encoding='utf-8')
        hashes[kind] = digest(text)
    dump_json(directory / 'settings.json', {'model': cfg.model_reviewer, 'schema': prompt.schema,
        'tools': REVIEW_TOOLS, 'prompt_hashes': hashes, 'isolation_flags': cfg.isolation_flags,
        'call_budget_usd': cfg.call_budget_usd, 'call_timeout_s': cfg.call_timeout_s})
    return hashes


class Roles:
    def __init__(self, caller: Any, prompts: Any, cfg: Any, work_dir: Path):
        self.caller, self.prompts, self.cfg, self.work_dir = caller, prompts, cfg, work_dir

    def call(self, role: str, variables: Any, validate: Any, raw: Path, pass_no: Any = None,
             checkout: Any = None, texts: Any = None) -> Any:
        prompt = self.prompts[role]
        system, user = texts or (prompt.system, render(prompt.user, variables))
        models = {'reviewer': self.cfg.model_reviewer, 'verifier': self.cfg.model_verifier,
                  'comparator': self.cfg.model_comparator, 'classifier': self.cfg.model_resolver,
                  'resolver': self.cfg.model_resolver}
        reads_code = role in ('reviewer', 'verifier')
        if reads_code != (checkout is not None):
            raise ValueError('only Reviewer/Verifier calls require the head checkout')
        spec = CallSpec(role, pass_no, system, user, prompt.schema, models[role],
                        REVIEW_TOOLS if reads_code else '', checkout if reads_code else self.work_dir)
        return self.caller.call(spec, validate, raw).parsed

    def classify(self, rules: Any, definitions: Any, warnings: List[str]) -> Dict[str, List[str]]:
        project = [r for r in rules if r.source == 'project']
        ids = list(dict.fromkeys(r.rule_id for r in project))
        if not ids:
            return {}
        latest: Dict[str, Any] = {}

        def validate(payload: Any) -> List[str]:
            latest.clear()
            latest.update(payload)
            return validate_routes(payload, ids)

        try:
            self.call('classifier', {'personas_json': json_data(definitions),
                'rules_json': json_data([r.__dict__ for r in project])}, validate, self.work_dir / 'classifier.raw.json')
        except PassFailed as exc:
            warnings.append('project-rule classification failed: %s' % exc)
        assigned = routes(latest, ids)
        fallback = [key for key in ids if key not in assigned]
        for key in fallback:
            assigned[key] = list(PERSONAS)
        if fallback:
            warnings.append('unresolved project rules assigned to all personas: %s' % ', '.join(fallback))
        dump_json(self.work_dir / 'classification.json', {'proposal': latest, 'assignments': assigned, 'fallback': fallback})
        return assigned

    def review(self, slot: Any, texts: Any, checkout: Path, raw: Path) -> Any:
        return self.call('reviewer', {}, requires_lists('findings'), raw, slot.pass_no, checkout, texts)

    def compare(self, slot: Any, findings: Any, master: Any, raw: Path) -> Any:
        return self.call('comparator', {'categories_json': json_data(PERSONAS),
            'master_json': json_data(list(master.items.values())), 'findings_json': json_data(findings)},
            lambda p: validate_comparison(p, findings, master), raw, slot.pass_no)

    def verify(self, group: Any, master: Any, criteria: Any, diff: str, checkout: Path, cache: Any, raw: Path) -> Any:
        claims = [master.items[i] for i in group['findings']]
        # A finding's citation can itself miss the rule that resolves the conflict.
        # Preserve the accepted base-commit rules rather than narrowing by model citations.
        relevant = criteria.rules
        return self.call('verifier', {'categories_json': json_data(PERSONAS), 'claims_json': json_data(claims),
            'reason': protect(group['reason']), 'criteria_block': protect(render_criteria_block(relevant)),
            'diff_block': protect(diff)}, lambda p: validate_annotation(p, group, cache), raw, checkout=checkout)
