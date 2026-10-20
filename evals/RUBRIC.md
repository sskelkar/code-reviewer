# Review finding grading rubric

**Version:** draft-2, 2026-10-19. Evaluator-only: do not include this document, its examples or answer keys in the Reviewer's checkout or prompt. The local harness implements this human-label policy and deterministic scoring. The policy remains provisional; it is not a calibrated or validated LLM judge.

The ten [review criteria](../src/code_reviewer/flows/single_agent/criteria/general.md) tell the agent what to inspect. This rubric judges its findings. A separate answer key identifies expected issues for each pinned diff. The same rubric can be used for public and privately stored production cases; private case details must remain outside this repository.

## What is graded

Grade each scored finding in the final master list once per completed review trial. Check it against that case's pinned base/head, the criteria actually supplied, and relevant repository evidence. Use the head for the proposed behavior and the base to establish what the change introduced or worsened. Later fixes/tests can corroborate an answer key, but do not assume they establish the behavior of an earlier version without checking it.

Self-check-rejected, malformed and ungrounded candidates are not final scored findings. Keep their counts and review failures visible separately; do not claim the rubric measures the correctness of every intermediate candidate. A missing final finding still counts as a miss against an established expected issue.

If possible, hide Reviewer model, pass count, agreement tier and other judges' labels while grading. A confident tone, matching rule ID, exact quote or high pass agreement does not prove correctness.

## Three checks

For each check, record **pass**, **fail** or **uncertain**, with a short evidence-based explanation.

| Check | Pass | Fail |
|---|---|---|
| Correct and supported | The material claim follows from code, tests or contracts in the repository under an identifiable scenario. The evidence supports the consequence, not just the code's location. | The claim contradicts available evidence, relies on a guarantee the repository disproves, or mistakes intentional supported behavior for a defect. |
| Introduced or worsened by the change | The diff introduces or worsens the problem, including a newly introduced violation of an applicable criterion. | The problem is pre-existing and unaffected, outside the reviewed change, or depends on an invented requirement. |
| Specific and useful | The finding identifies an affected behavior and a concrete failure, uncovered regression, documentation mismatch or maintenance burden that can reasonably be addressed in the change. | It only names a pattern or best practice, or expresses a preference without a concrete consequence. |

Use **uncertain** when judging a material claim needs additional code, a technical fact or a reproducer that has not been established. Missing local dependencies alone do not force uncertainty if the available source and evidence are already sufficient. Distinguish a vague finding (fails specificity) from a specific claim whose truth is unresolved (uncertain correctness).

All three checks passing makes a finding **valid**. A demonstrated failure of any check makes it **wrong**, with the failing check and evidence recorded. If there is no demonstrated failure but any check remains uncertain, leave it **uncertain** and resolve it before final scoring. Uncertainty is not a claim that the finding is false. **Pending** means not graded yet.

A different criterion ID, a minor line-number discrepancy or a severity disagreement does not invalidate an otherwise correct, sufficiently located finding. Track those separately if useful. An unsupported material allegation or wrong failure mechanism cannot earn a match merely because its title resembles the answer key.

## Match valid findings, then identify duplicates

After establishing validity:

- **match:** the finding identifies an expected issue's underlying defect, relevant trigger and consequence. Exact wording or the suggested fix may differ. Record the expected issue ID.
- **additional:** the finding is valid but absent from the answer key. Record a stable issue ID and supporting evidence. It is not automatically a false positive.
- **duplicate:** the finding repeats an already-counted valid issue in this trial. Record the same issue ID and the original finding it repeats. Different consequences of the same unawaited operation do not automatically establish different defects; independently fixable root causes may be separate issues.

Wrong, uncertain and pending findings keep those labels. Duplicate wrong allegations remain wrong; duplication does not establish validity. If a finding bundles independent claims, adjudicate the claims explicitly and resolve the scoring unit before including that trial in comparisons; the draft runner does not yet support claim splitting.

The version-1 `judgments.json` contains the rubric/master hashes and a map keyed by finding ID. Each entry records the three checks (`correct`, `introduced`, `useful`), verdict, issue ID, duplicate reference and an evidence-based reason. The experiment/trial identifies the case. `eval_judgments.py` validates these records before scoring. See [the guide](README.md) for the label commands and JSON editing.

## How the labels become metrics

For a fully adjudicated trial:

- **Precision:** distinct valid issues reported / scored findings reported. Extra comments about an already-counted valid issue count against this duplicate-penalizing precision.
- **Known-issue recall:** established expected issues found / established expected issues. Additional valid discoveries do not change this denominator during an experiment.
- Show wrong findings, duplicates and label coverage alongside these ratios. Uncertain/pending labels prevent final quality scores; incomplete or unadjudicated scheduled trials also suppress aggregate group quality scores, avoiding selection of only successful trials. Retain every trial and its cost/status visibly.
- An empty review has undefined precision. It has zero recall if there are established expected issues, and undefined recall for a case with none.
- A repair/control case is not automatically defect-free: adjudicate every reported issue. Silence on an expected-empty case supports only the absence of complaints in that trial, not proof of completeness.

Only independently established expectations belong in a final recall denominator. Current public answer keys are agent-curated candidates with documented evidence; an upstream fix or user-delegated curation must not be relabeled as independent human adjudication. Cases default to `expectation_status = provisional`. Recall uses only keys explicitly marked `confirmed` with a human `expectation_reason`. This is a human attestation, not proof established by Python. Mark outstanding disputes explicitly before drawing performance conclusions.

Version the answer key if new valid issues are incorporated, and regrade all compared configurations consistently. The `rekey` command copies saved reviews under revised expectations and resets all labels without calling a model; original artifacts remain intact. Keep the case set, criteria and rubric fixed for a comparison. Paired regression/repair cases stay in the same data split and represent one underlying scenario.

## Worked examples (authored for this draft)

These are deliberately written example findings, **not outputs from model review runs or calibration results**. They use three pinned public diffs already in [the selected cases](CASES.md).

### 1. Accept an ETag finding

Case: [Express ETag regression](https://github.com/expressjs/express/compare/59e205a57a04fced6bb7b8ec0b5dec29461a9996...18e5985b8a9d5e8423db0a9121f22bdaecd5b120).

Example finding: "When Transfer-Encoding is set and ETag generation is enabled, the new condition skips body-length calculation. The later ETag branch requires that length, so the response loses its validator. Restrict the framing-header assignment without skipping the metadata needed for ETag generation."

Judgment: **pass / pass / pass -> match**, issue `etag-skipped`. The changed condition and downstream guard establish the mechanism. Narrow execution before/regression/repair corroborates it. Full HTTP integration tests have not run locally.

### 2. Reject a materially wrong explanation despite similar keywords

Same case. Example finding: "Transfer-Encoding causes this change to produce both Content-Length and Transfer-Encoding, so clients reject the response."

Judgment: **fail / pass / pass -> wrong**. The new condition skips Content-Length assignment when Transfer-Encoding is present. The finding names the relevant headers but alleges the opposite behavior. Do not award an ETag match based on shared vocabulary.

### 3. Count a repeated ETag allegation once

Same case, after example 1 was already reported. Example finding: "Chunked responses no longer get an automatically generated ETag because len is left undefined."

Judgment: **pass / pass / pass -> duplicate**, issue `etag-skipped`, referring to example 1. Different wording does not establish a second defect. Keep one valid issue and count the extra finding as duplication.

### 4. Reject the old allegation after its repair

Case: [Express ETag repair](https://github.com/expressjs/express/compare/3ce6d0eb86e9d93529ff3191c6bb5db8ce6e72c8...9a34acf03cb818ff3f8bc40e44176e277a25cbb9).

Example finding: "Transfer-Encoding prevents len from being assigned, so ETag generation is skipped."

Judgment: **fail / fail / pass -> wrong**. At this head, the body metadata is computed regardless of Transfer-Encoding; only setting Content-Length is conditional. The allegation describes the earlier bug, which this diff repairs. This rejects that claim, not every possible finding on the repair diff.

### 5. Accept the client-certificate finding without demanding exact terminology

Case: [HTTPX client-certificate regression](https://github.com/encode/httpx/compare/a33c87852b8a0dddc65e5f739af1e0a6fca4b91f...80960fa31918d7663c3f4c3ad61661cf0e80628f).

Example finding: "Supplying cert together with verify=False returns an SSL context before load_cert_chain is reached. A configured client certificate is therefore omitted, which breaks requests to servers requiring client-certificate authentication. Apply the certificate configuration before returning."

Judgment: **pass / pass / pass -> match**, issue `client-certificate-ignored`. The restored cert argument and early return establish the contract mismatch. A narrow helper probe establishes that certificate loading is bypassed; no real mutual-TLS handshake has run. The judge need not insist on the phrase 'mutual TLS' or a particular criterion ID.

### 6. Reject a generic security preference

Same case. Example finding: "Delete support for verify=False because TLS verification should never be disabled."

Judgment: **fail / fail / fail -> wrong**. This is an explicitly supported option, and the finding establishes no repository-defined policy violation or new unsafe call site. It also confuses server-certificate verification with configuring the client's own certificate. Generic advice is not the expected defect.

### 7. Leave a specific but unresolved additional claim uncertain

Same case. Example finding: "An HTTPS proxy receives the client certificate after this change, disclosing it to the proxy."

Judgment: **uncertain correctness; remaining checks not yet established -> uncertain** if the judge has not traced the proxy and destination SSL contexts. Do not accept it because certificates are involved, reject it just because it is missing from expected, or declare it true from this example. Obtain repository evidence for the claimed path before deciding. A later investigation may establish validity or demonstrate a failure of a check.

## What to settle next

Review these decision boundaries rather than asking the user to understand more unfamiliar PRs. Apply the draft to actual findings when available and refine it through error analysis. An LLM judge, if later selected, must be tested against human labels on cases not used to tune its prompt. Do not report judge reliability or model superiority from these authored examples.

Public cases help establish the grading vocabulary. Before choosing a production model/pass configuration, apply the rubric to a few representative production changes privately. Keep production manifests, source references, answer keys, outputs, labels and detailed reports physically outside this repository; do not embed their paths or identifiers here. No production project has been selected or accessed for this task, and no private storage directory has been created.
