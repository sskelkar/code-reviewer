"""Concurrent persona rounds, grounded candidates and supported-only publication."""
from __future__ import annotations

from ..shared.git_content import omission_section

from dataclasses import replace
import copy
import platform
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..config import ConfigError, REVIEW_DIR, PROMPTS_DIR, DEFAULT_CRITERIA_DIR
from ..shared import git_ops
from ..backends.contract import RunnerError
from ..shared.diffview import DiffIndex
from ..shared.events import Event, null_sink, round_preview, reviewer_preview
from ..shared.findings import FileCache, FindingsPreparer
from ..shared.models import PassFailed
from ..shared.pr_source import load_pr_context
from ..shared.review_inputs import InputBuilder
from ..shared.run_store import RunStore, dump_json
from ..tracing import traced, event as trace_event, annotate
from .caller import PersonaCaller, RunStopped
from .budget import Budget, ConcurrentStats
from .runner import ParallelRunner
from .criteria import Catalog, PERSONAS
from .master import MasterList
from .models import DryRun, PersonaPass, RunResult
from .evidence import evidence_errors
from .verification import groups, artifact, counts
from .prompt_contract import digest, load_prompts
from .roles import Roles, freeze, save_frozen

class ReviewFlow:
    def __init__(self, cfg: Any, runner: Any, emit: Any = None, prompts_dir: Any = None, defaults_dir: Any = None):
        cfg.validate()
        self.cfg, self.runner, self.sink = cfg, runner, emit or null_sink
        self.prompts_dir, self.defaults_dir = prompts_dir or PROMPTS_DIR, defaults_dir or DEFAULT_CRITERIA_DIR
        self.catalog = Catalog(REVIEW_DIR, self.defaults_dir)
        try:
            self.prompts = load_prompts(self.prompts_dir)
        except (OSError, ValueError) as exc:
            raise ConfigError('invalid persona prompts: %s' % exc) from exc
        self.inputs = InputBuilder(self.catalog.input_config(cfg), self.prompts, self.defaults_dir, self.emit)

    def emit(self, type_: str, **data: Any) -> None:
        trace_event(type_, data)
        self.sink(Event(type_, data))

    def prepare(self, ref: Any, local_path: Any = None) -> Any:
        return load_pr_context(ref, local_path, self.emit)

    def _freeze(self, ctx: Any, files: Any, criteria: Any, assignments: Any, checkout: Path, store: Any) -> Any:
        frozen = {}
        for persona in self.catalog.personas:
            key = persona['id']
            subset = replace(criteria, rules=assignments[key])
            inputs = self.inputs.review_input(ctx, files, subset, checkout)
            texts = freeze(self.prompts['reviewer'], persona, self.catalog.standard, self.cfg, inputs)
            hashes = save_frozen(store.input_dir, key, texts, self.prompts['reviewer'], self.cfg)
            frozen[key] = (texts, hashes)
        return frozen

    def dry_run(self, ctx: Any) -> DryRun:
        ctx, count = self.inputs.redact(ctx)
        files, summary = self.inputs.load_diff(ctx)
        store = RunStore.create(self.cfg.runs_path(), ctx, label='dry-run')
        store.save_pr_text(ctx, count)
        criteria = self.inputs.default_criteria(files)
        assigned = self.catalog.allocate(criteria.rules, {})
        self._freeze(ctx, files, criteria, assigned, Path('<checkout>'), store)
        store.save_inputs(self.inputs.review_input(ctx, files, criteria, Path('<checkout>')), criteria)
        return DryRun(ctx, store.root, assigned, summary, self.cfg.model_reviewer, passes_per_persona=self.cfg.persona_passes,
                      parallel=self.cfg.persona_parallel, redacted_emails=count,
                      redact_emails=self.cfg.redact_emails, budget_pools=Budget(self.cfg).snapshot())

    def review(self, ctx: Any, keep_checkout: bool = False, prepared_checkout: Any = None) -> RunResult:
        from .report import render_report, render_summary
        cfg = self.cfg
        ctx, redacted = self.inputs.redact(ctx)
        files, summary = self.inputs.load_diff(ctx)
        store = RunStore.create(cfg.runs_path(), ctx)
        store.save_pr_text(ctx, redacted)
        stats = ConcurrentStats()
        budget = Budget(cfg)
        runner = ParallelRunner(self.runner, budget)
        caller = PersonaCaller(runner, cfg, stats, budget)
        roles = Roles(caller, self.prompts, cfg, store.input_dir)
        slots = [PersonaPass(pass_no=(r - 1) * len(PERSONAS) + n + 1, persona=p, round_no=r, ok=False)
                 for r in range(1, cfg.persona_passes + 1) for n, p in enumerate(PERSONAS)]
        result = RunResult(ctx, cfg, store.root, MasterList(), stats, self.inputs.default_criteria(files), slots, store.report_path,
                           diff_summary=summary, diagnostics=caller.diagnostics)
        self.emit('budget_allocated', pools=budget.snapshot())
        checkout = Path(prepared_checkout) if prepared_checkout is not None else store.prepare_checkout()
        if prepared_checkout is not None and (checkout.is_symlink() or not checkout.is_dir()):
            raise ValueError('prepared checkout must be a regular admitted directory')
        self.emit('run_started', repo=ctx.repo, number=ctx.number, title=ctx.title, head=ctx.head_sha, base=ctx.base_sha)
        try:
            store.dump('config.json', {'config': cfg.to_dict(), 'prompt_hashes': {
                role: {'system': digest(p.system), 'user': digest(p.user)} for role, p in self.prompts.items()},
                'schemas': {role: p.schema for role, p in self.prompts.items()},
                'versions': {'python': platform.python_version(),
                    'adapter': self.runner.version() if hasattr(self.runner, 'version') else 'unknown'}})
            self.emit('checkout_ready', path=str(checkout), kept=keep_checkout,
                      **({} if prepared_checkout is not None else git_ops.export_tree(ctx.git_dir, ctx.head_sha, checkout)))
            result.criteria = self.inputs.resolve_criteria(ctx, files, caller, store.input_dir)
            result.warnings.extend(result.criteria.warnings)
            if any(r.source == 'project' for r in result.criteria.rules):
                self.emit('classification_started', rules=sum(r.source == 'project' for r in result.criteria.rules))
            prior_warnings = len(result.warnings)
            routed = roles.classify(result.criteria.rules, self.catalog.personas, result.warnings)
            for warning in result.warnings[prior_warnings:]:
                self.emit('warning', message=warning)
            result.assignments = self.catalog.allocate(result.criteria.rules, routed)
            frozen = self._freeze(ctx, files, result.criteria, result.assignments, checkout, store)
            for slot in slots:
                slot.prompt_hashes = frozen[slot.persona][1]
            cache = FileCache(checkout)
            result.stop_reason = self._passes(result, roles, files, frozen, checkout, cache, store)
            if result.stop_reason != 'cancelled':
                self._verify(result, roles, files, checkout, cache, store)
        except RunStopped as exc:
            result.warnings.append(str(exc))
            result.stop_reason = 'budget_exceeded'
        except KeyboardInterrupt:
            result.stop_reason = 'cancelled'
        except (RunnerError, PassFailed) as exc:
            result.stop_reason = 'failures'
            result.warnings.append(str(exc))
        finally:
            try:
                self.emit('report_started')
                for group in result.master.conflicts.values():
                    if group['status'] == 'pending':
                        group['error'] = 'verification not completed: %s' % result.stop_reason
                if not result.assignments:
                    result.warnings.append('project discovery/routing or prompt preparation did not complete; no review passes ran')
                store.save_inputs(self.inputs.review_input(ctx, files, result.criteria, checkout), result.criteria)
                store.save_outputs(result.master.to_dict(), {**stats.to_dict(), 'passes': [s.to_dict() for s in slots],
                    'completed_by_persona': result.completed_by_persona, 'stop_reason': result.stop_reason,
                    'validation_errors': result.diagnostics, 'warnings': result.warnings, 'budget_pools': budget.snapshot()})
                result.budget_pools = budget.snapshot()
                store.report_path.write_text(render_report(result) + omission_section(ctx), encoding='utf-8')
                store.summary_path.write_text(render_summary(result, cfg.post_tiers, []), encoding='utf-8')
                store.dump('findings.json', artifact(result))
                store.save_binary_omissions(ctx)
                self.emit('report_ready', path=str(store.report_path), stop_reason=result.stop_reason)
            finally:
                if not keep_checkout:
                    store.cleanup_checkout()
        return result

    def _passes(self, result: Any, roles: Any, files: Any, frozen: Any, checkout: Any, cache: Any, store: Any) -> str:
        failures = dict.fromkeys(PERSONAS, 0)
        disabled = {}
        incomplete = False
        budget = roles.caller.budget
        workers = {p: Roles(PersonaCaller(roles.caller.guard.runner, self.cfg, result.stats, budget, p),
                             self.prompts, self.cfg, store.input_dir) for p in PERSONAS}

        def review(slot):
            try:
                payload = workers[slot.persona].review(slot, frozen[slot.persona][0], checkout,
                                                       store.pass_dir(slot.pass_no) / 'reviewer.raw.json')
                return payload
            except BaseException as exc:
                return exc

        executor = ThreadPoolExecutor(max_workers=self.cfg.persona_parallel)
        try:
            for start in range(0, len(result.passes), len(PERSONAS)):
                wave = result.passes[start:start + len(PERSONAS)]
                previous = {item['id'] for item in result.master.scored()}
                pending = {}
                for slot in wave:
                    if slot.persona in disabled:
                        slot.error = disabled[slot.persona]
                        continue
                    self.emit('persona_pass_started', persona=slot.persona, round_no=slot.round_no,
                              pass_no=slot.pass_no, rounds=self.cfg.persona_passes, scheduled=len(result.passes))
                    self.emit('reviewer_started', pass_no=slot.pass_no, model=self.cfg.model_reviewer)
                    pending[slot.pass_no] = executor.submit(copy_context().run, review, slot)
                # Barrier: no consolidation or subsequent round until this round's reviews finish.
                payloads = {}
                numbers = {future: number for number, future in pending.items()}
                for future in as_completed(numbers):
                    payload = future.result()
                    if isinstance(payload, (KeyboardInterrupt, RunnerError)):
                        raise payload
                    payloads[numbers[future]] = payload
                    slot = next(s for s in wave if s.pass_no == numbers[future])
                    self.emit('persona_review_finished', persona=slot.persona, round_no=slot.round_no,
                              ok=not isinstance(payload, BaseException), **reviewer_preview(payload))
                for slot in wave:
                    if slot.pass_no not in payloads:
                        continue
                    payload = payloads[slot.pass_no]
                    if isinstance(payload, (KeyboardInterrupt, RunnerError)):
                        raise payload
                    try:
                        self._consolidate(result, roles, files, frozen, checkout, cache, store, slot, payload)
                        failures[slot.persona] = 0
                    except RunStopped as exc:
                        if exc.pool != slot.persona:
                            raise
                        incomplete = True
                        disabled[slot.persona] = str(exc)
                        slot.error = str(exc)
                        result.warnings.append(str(exc))
                    except PassFailed as exc:
                        incomplete = True
                        failures[slot.persona] += 1
                        self.emit('pass_failed', pass_no=slot.pass_no, error=str(exc))
                        if failures[slot.persona] >= self.cfg.max_consecutive_failures:
                            disabled[slot.persona] = 'persona failure limit reached'
                self.emit('round_preview', **round_preview(wave[0].round_no,
                    result.master, previous, sum(s.ok for s in wave), sum(not s.ok for s in wave)))
                if len(disabled) == len(PERSONAS):
                    break
        finally:
            budget.cancelled.set()
            while True:
                try:
                    executor.shutdown(wait=True, cancel_futures=True)
                    break
                except KeyboardInterrupt:
                    budget.cancelled.set()
            budget.cancelled.clear()
            for worker in workers.values():
                result.diagnostics.extend(worker.caller.diagnostics)
            for slot in result.passes:
                if slot.status == 'skipped' and result.stats.by_pass_role(slot.pass_no, 'reviewer')['calls']:
                    slot.status, slot.error = 'unmerged', 'review attempt not consolidated before run stopped'
                if slot.status == 'skipped' and slot.persona in disabled:
                    slot.error = disabled[slot.persona]
                for role in ('reviewer', 'comparator'):
                    usage = result.stats.by_pass_role(slot.pass_no, role)
                    setattr(slot, role + '_cost_usd', usage['cost_usd'])
                    setattr(slot, role + '_tokens', usage['tokens'])
                dump_json(store.pass_dir(slot.pass_no) / 'slot.json', slot.to_dict())
        if incomplete and not result.passes_completed:
            return 'failures' if any(failures.values()) else 'budget_exceeded'
        return 'incomplete' if incomplete else 'completed'

    @traced('review.consolidate')
    def _consolidate(self, result, roles, files, frozen, checkout, cache, store, slot, payload):
        directory = store.pass_dir(slot.pass_no)
        previous_master = result.master
        try:
            if isinstance(payload, BaseException):
                raise payload
            preparer = FindingsPreparer({r.rule_id: r.__dict__ for r in result.assignments[slot.persona]}, self.cfg.self_check,
                cache, DiffIndex(files), self.cfg.grounding_line_window, self.cfg.all_exclude_patterns())
            prepared = preparer.prepare(payload.get('findings'), slot.pass_no)
            for finding in list(prepared.ok):
                errors = evidence_errors(finding['evidence'], 'S', cache, result.criteria.index())
                if errors:
                    finding.update(status='ungrounded', reason='; '.join(errors))
                    prepared.ok.remove(finding)
                    prepared.ungrounded.append(finding)
            dump_json(directory / 'findings.json', {'notes': payload.get('notes', ''), 'findings': prepared.all_records()})
            slot.raised, slot.usable = len(prepared.all_records()), len(prepared.ok)
            for key in ('rule_repaired', 'rule_unknown', 'malformed', 'self_check_missing', 'line_corrected', 'ungrounded', 'out_of_scope'):
                value = getattr(prepared, key)
                setattr(slot, key, len(value) if isinstance(value, list) else value)
            slot.rejected_by_self_check = sum(f['verdict'] == 'rejected' for f in prepared.ok)
            self.emit('reviewer_done', raised=slot.raised, usable=slot.usable, ungrounded=slot.ungrounded,
                      rejected=slot.rejected_by_self_check, rule_fixed=slot.rule_repaired + slot.rule_unknown,
                      out_of_scope=slot.out_of_scope)
            if prepared.ok:
                self.emit('comparator_started', new=len(prepared.ok), candidates=len(result.master.items),
                          persona=slot.persona, round_no=slot.round_no)
            proposal = roles.compare(slot, prepared.ok, result.master, directory / 'comparator.raw.json') if prepared.ok else {'decisions': [], 'conflicts': []}
            # Keep partial consolidation out of agreement and subsequent passes if interrupted.
            staged_master = copy.deepcopy(previous_master)
            assigned = staged_master.apply(slot, prepared.ok, proposal)
            dump_json(directory / 'mapping.json', {'proposal': proposal, 'assigned': assigned})
            result.master = staged_master
            slot.ok, slot.status = True, 'completed'
            self.emit('persona_pass_finished', persona=slot.persona, round_no=slot.round_no, usable=slot.usable, cost=result.stats.total_cost())
        except PassFailed as exc:
            result.master, slot.ok = previous_master, False
            slot.error, slot.status = str(exc), 'failed'
            raise
        except (RunStopped, KeyboardInterrupt, RunnerError):
            result.master, slot.ok = previous_master, False
            slot.status, slot.error = 'failed', 'interrupted before review and consolidation completed'
            raise
        finally:
            for role in ('reviewer', 'comparator'):
                usage = result.stats.by_pass_role(slot.pass_no, role)
                setattr(slot, role + '_cost_usd', usage['cost_usd'])
                setattr(slot, role + '_tokens', usage['tokens'])
            dump_json(directory / 'slot.json', slot.to_dict())


    @traced('review.verify')
    def _verify(self, result: Any, roles: Any, files: Any, checkout: Any, cache: Any, store: Any) -> None:
        diff = self.inputs.review_input(result.ctx, files, result.criteria, checkout).diff_block
        for number, ids in enumerate(groups(result.master), 1):
            directory = store.root / 'verification' / ('V-%03d' % number)
            directory.mkdir(parents=True, exist_ok=True)
            self.emit('verification_started', conflict='V-%03d' % number)
            try:
                if len(ids) > 40:
                    raise PassFailed('conflict group exceeds the verification candidate limit; not truncated')
                response = roles.verify(ids, result.master, result.criteria, diff, checkout, cache, directory / 'verifier.raw.json')
            except (PassFailed, RunStopped) as exc:
                for ident in ids:
                    result.master.items[ident]['verification_error'] = str(exc)
                result.warnings.append('candidate investigation incomplete: %s' % exc)
                response = None
            if response is not None:
                for claim in response['assessments']:
                    item = result.master.items[claim['candidate_id']]
                    item['verification'] = claim
                    item['verification_relationship'] = response['relationship']
                    item['categories'] = list(claim['categories'])
            for group in result.master.conflicts.values():
                if set(group['findings']) <= set(ids):
                    group['status'] = 'unresolved' if response is None or response['relationship'] == 'unresolved' else 'annotated'
                    group['annotation'] = None if response is None else {
                        'relationship': response['relationship'], 'explanation': response['explanation']}
                    group['error'] = '' if response is not None else 'candidate investigation incomplete'
        totals = counts(result.master)
        annotate(proposed=totals['candidates'], accepted=totals['published'], rejected=totals['refuted'])
        if totals['pending'] or totals['unresolved'] or totals['supported'] > totals['published']:
            if result.stop_reason == 'completed':
                result.stop_reason = 'incomplete'
