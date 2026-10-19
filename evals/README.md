# Local review evals

The harness is runnable with standard-library Python. **Only `run` calls Claude and costs money.** `init`, `plan`, `label`, `grade` and `rekey` are offline. There is no LLM judge: you judge findings; Python validates those labels and calculates scores. No command fetches a repository, modifies its working tree, or posts to GitHub.

For production code, run these commands yourself. Keep the clone, suite, expectations, findings, labels and reports physically outside this code-reviewer repository, and do not paste them into this chat. Claude performs the review through the existing restricted Claude Code invocation. Local artifact storage does not make inference local. The examples below contain placeholders, not production paths.

## 1. Pin one local change

Use an existing clone containing both refs. The harness resolves them to full SHAs and uses their merge-base for the diff. The current working tree and uncommitted changes are not reviewed.

```bash
# Replace the clone path and refs. Use a fresh directory for each experiment.
eval_dir="$HOME/.code-reviewer/evals/pilot"
project_clone="/path/to/local/clone"

python -m code_reviewer.evals init \
  --repo "$project_clone" --base BASE_REF --head HEAD_REF \
  --output "$eval_dir/suite.json" \
  --title "Update request handling" \
  --description "Describe the intended change without disclosing expected defects."
```

`init` refuses to overwrite a suite. Edit its title/description locally so they describe the real intended change. The Reviewer sees **only title, description, pinned diff, default criteria and the historical head checkout**. Suite IDs, answer keys, notes, later repair references and historical commit messages are not supplied in the prompt. Head files can themselves contain clues; this is not a guarantee against model familiarity with public history.

A suite can contain multiple cases from the same clone. Each case needs `id`, full `base`/`head`, `title`, `description` and an `expected` list. Extra provenance fields are evaluator metadata. `base` must be an ancestor of `head`; use the merge-base for a PR branch.

## 2. Preview, then run

A small pilot needs only one model and one pass:

```bash
python -m code_reviewer.evals plan "$eval_dir/suite.json" \
  --repo "$project_clone" --output "$eval_dir/preview" \
  --models haiku --passes 1
```

Inspect the saved `input/reviewer.user.md` and `input/diff.txt` locally. Plan does not call Claude, even for version checks, and does not create a head checkout. Plans and real runs use separate output directories; `run` does not execute a previously saved plan.

```bash
# Paid: one review trial per selected case.
python -m code_reviewer.evals run "$eval_dir/suite.json" \
  --repo "$project_clone" --output "$eval_dir/run-1" \
  --models haiku --passes 1 --max-total-cost 1
```

When the pilot is useful, compare configurations on the same pinned cases:

```bash
# Paid: 2 models x 2 pass counts x 2 repetitions = 8 trials PER CASE.
python -m code_reviewer.evals run "$eval_dir/suite.json" \
  --repo "$project_clone" --output "$eval_dir/comparison-1" \
  --models haiku sonnet --passes 1 3 --repeat 2 --max-total-cost 12
```

`--case CASE_ID` can be repeated. `--config PATH` accepts ordinary review TOML settings. Evals explicitly use `defaults_only` criteria and ignore `criteria_manifest`, keeping the generic criteria constant and avoiding a stochastic project-rule resolver. Disabled defaults and other configured limits still apply and are recorded. This is a generic-criteria experiment, not an evaluation of the normal `merge` criteria configuration.

Only the Reviewer model varies; the Comparator model/settings remain fixed. `min_passes = max_passes = requested count` prevents convergence before the final requested pass. These are **fixed attempts**, not guaranteed successful passes: failures, timeouts and budgets can make a trial incomplete. This does not evaluate the production early-stop policy.

One trial shares a temporary read-only head checkout across independent fresh Claude sessions. Each subsequent trial gets another checkout. Checkouts contain files, not git history, and are removed on completion/error. Tools remain Read, Grep and Glob. No answer keys or prior trial outputs are placed in them.

Per-call and per-run limits still apply. `--max-total-cost` additionally checks accumulated reported cost between trials and reduces the remaining trial/call allowance. These are **soft limits**, not hard billing caps; retries or in-flight calls can overshoot. Unknown cost stops further scheduling when this cap is supplied. Ctrl-C stops subsequent trials. Failed/incomplete/skipped trials and incurred/unknown costs stay visible. Existing output directories are never reused; there is no resume command.

## 3. Judge actual findings locally

Each trial has:

- `findings.md`: scored findings without model, agreement or confidence-tier metadata, for optional human judgment without those cues. Trial ordering itself is not randomized/blinded.
- `judgments.json`: the three rubric checks, verdict, issue ID, duplicate reference and reason per finding.
- `runs/.../report.md`: the normal detailed report, including dropped/rejected counts, usage and pass results.

Use [RUBRIC.md](RUBRIC.md). Read the relevant base/head code as needed. There is no automated claim that a finding is correct just because its quote is grounded.

```bash
# A valid discovery absent from the answer key:
python -m code_reviewer.evals label "$eval_dir/run-1" trial-0001 F-001 additional \
  --issue issue-1 --reason "Explain the trigger, consequence and repository evidence."

# A demonstrated false allegation:
python -m code_reviewer.evals label "$eval_dir/run-1" trial-0001 F-002 wrong \
  --fail correct --reason "Explain the code/test evidence contradicting the claim."

# An unresolved material claim:
python -m code_reviewer.evals label "$eval_dir/run-1" trial-0001 F-003 uncertain \
  --uncertain correct --reason "Explain what evidence is still needed."

# A repeat of an already valid issue:
python -m code_reviewer.evals label "$eval_dir/run-1" trial-0001 F-004 duplicate \
  --issue issue-1 --duplicate-of F-001 --reason "Same root cause as F-001."
```

Use actual finding IDs; the examples do not assume these findings exist. `--fail`/`--uncertain` accept `correct`, `introduced` or `useful`. `match`, `additional` and `duplicate` record all three checks as pass. `wrong` requires a failing check; `uncertain` requires an unresolved check. You can edit JSON to record multiple failing/unresolved checks. Final labels require evidence-based reasons. `pending` resets a finding for later judgment. Findings containing independent bundled claims should remain uncertain until the scoring unit is resolved; claim splitting is not implemented.

A `match` requires `--issue` to name an entry in the case's `expected` list. Additional valid discoveries need a new stable issue ID. Repeated valid issue IDs must be explicitly labeled `duplicate` and point to the original valid finding. Repeated wrong allegations remain wrong. Rejected-only master items are not final scored findings and do not get judgment entries.

## 4. Establish expectations, if recall is needed

An unseeded pilot can measure precision after judgment, but it cannot measure recall. Add independently established defects to `expected`:

```json
{
  "id": "missing-rollback",
  "criterion": "GEN-007",
  "file": "src/service.py",
  "description": "Describe the independently established defect and trigger.",
  "evidence": "Describe the base/head evidence or reproducer."
}
```

At case level, `expectation_status` defaults to `provisional`. Set it to `confirmed` with a nonempty `expectation_reason` only after human verification of the answer key. This is an attestation, not something Python can verify for you. An empty confirmed key still does not certify a diff free of all defects. Public answer keys remain explicitly provisional; delegating their curation or accepting this harness does not make them human-verified.

Before a comparison, freeze one answer-key version for every model/pass configuration. Discovery of additional valid issues does not silently expand recall's denominator. If you revise expectations **after** running, copy the original suite to a new file, change only `expected`, `expectation_status` and `expectation_reason`, then:

```bash
python -m code_reviewer.evals rekey "$eval_dir/comparison-1" "$eval_dir/revised-suite.json" \
  --output "$eval_dir/comparison-rekeyed"
```

This copies the saved reviews, uses the revised key and resets **all human labels** so every compared configuration can be adjudicated consistently. No reviews are rerun and original artifacts remain intact. Editing a frozen suite/rubric/master directly is rejected. Changes to code, criteria, prompts or the judging policy need a new experiment; `rekey` is only for answer-key revisions.

## 5. Calculate scores

```bash
python -m code_reviewer.evals grade "$eval_dir/run-1"
```

This writes `evaluation.json` and `evaluation.md`. It never invokes Claude.

- Precision = distinct valid issues / reported scored findings; duplicate comments count against it.
- Known-issue recall = confirmed expected issues found / confirmed expected issues. Provisional expectations never enter this denominator.
- Pending or uncertain labels and incomplete trials suppress the group's quality scores. Per-trial scores/status remain visible; do not select only successful runs for comparison.
- No findings means undefined precision; recall is zero only when confirmed expected issues exist.
- Costs/duration cover all trials, including failed/incomplete ones. Unknown cost is marked rather than presented as complete accounting. Duration is wall time; per-call/model usage is retained separately.

A comparison is marked ready only when all trials are adjudicated/complete and each configuration covers the same cases/repetition numbers. That is bookkeeping readiness, not statistical significance or evidence of judge reliability. Current reports do not compute confidence intervals, score by tier/pass, or judge Comparator merge quality independently.

## Public cases and combined reports

The eight cases in [CASES.md](CASES.md) are four regression/repair pairs, not eight independent scenarios. Keep pairs together when splitting calibration/held-out data. Stop expanding the set for now. Each suite uses one existing local clone; retain/fetch the pinned commits yourself.

The Java introduction has 1746 changed lines, which fits the default cap (`max_diff_lines = 3000`). [public.example.toml](public.example.toml) pins that cap; use the same config for **all three suites** if combining them.

```bash
python -m code_reviewer.evals plan evals/suites/express.json \
  --repo /path/to/express-clone --output /tmp/express-eval-preview \
  --config evals/public.example.toml --models haiku sonnet --passes 1 3
```

Use `run` instead of `plan` only when ready for paid calls. Run the HTTPX and Resilience4j suites against their corresponding clones with the same configuration. After human labeling, combine:

```bash
python -m code_reviewer.evals grade /path/to/express-run /path/to/httpx-run /path/to/resilience4j-run \
  --output /path/to/combined-evaluation
```

Aggregation requires identical recorded source-code, prompt, default-criteria, rubric and fixed-setting hashes. Model aliases are recorded as requested and resolved identities remain in each run's usage; aliases can change between experiments. Avoid time-separated alias comparisons, or use full model IDs. Overlapping trial identities are rejected. Scenario counts group regression/repair pairs. Matching issue IDs across unrelated cases do not imply the same defect.

The complete suite snapshot, rubric snapshot, fixed environment hashes, per-run config, actual prompts/criteria, outputs and labels remain alongside results. These hashes detect accidental edits; they are not tamper-proof signatures. Historical public cases may have appeared in training data. Neither the offline tests nor these eight cases establish production model quality.
