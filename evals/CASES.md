# Initial open-source review cases

Updated 2026-10-19. The active set is capped at **eight cases**: four historical regression/repair pairs across three mature open-source repositories. Stop searching for additional cases; refine evidence and rubric examples next. The earlier six-defect/two-control mix was a suggestion, not a requirement; four pairs provide concrete negative examples without adding more unrelated scenarios.

The user delegated case curation to the agent after finding unfamiliar PRs difficult to assess. That is permission to curate, not independent human verification of every expected finding. No model experiment has run. The local harness is now implemented and tested offline; see [README.md](README.md). Keys remain provisional and do not enter recall until explicitly human-confirmed.

## Pinned sources

These default-branch tips were observed when cloning the repositories on 2026-10-19:

| Repository | Branch | Source revision |
|---|---|---|
| [Express](https://github.com/expressjs/express) | master | `0dee2c52c7c873498f8ed7d8ee4ddd0a04d3ada8` |
| [HTTPX](https://github.com/encode/httpx) | master | `b5addb64f0161ff6bfe94c124ef76f6a1fba5254` |
| [Resilience4j](https://github.com/resilience4j/resilience4j) | master | `b7c0069802e373ed72dff0b817c270fbb6e966ba` |

Each case also pins its own full base/head SHAs in `evals/suites/`. The source tip records provenance; the historical case head determines the checkout. Future branch pushes must not silently change inputs. A retained clone or private git bundle is needed for long-term availability if upstream history is rewritten. Source code is not vendored here.

## The eight cases

Changed-line counts come from the review parser with standard exclusions. The links show complete, pinned diffs, not hand-picked fragments.

| Case | Diff | Changed lines | Expected behavior |
|---|---|---:|---|
| Express ETag regression | [59e205a -> 18e5985](https://github.com/expressjs/express/compare/59e205a57a04fced6bb7b8ec0b5dec29461a9996...18e5985b8a9d5e8423db0a9121f22bdaecd5b120) | 39 | Detect that the new framing-header condition also skips ETag generation. |
| Express ETag repair | [3ce6d0e -> 9a34acf](https://github.com/expressjs/express/compare/3ce6d0eb86e9d93529ff3191c6bb5db8ce6e72c8...9a34acf03cb818ff3f8bc40e44176e277a25cbb9) | 25 | Do not report the repaired ETag issue as still present. |
| HTTPX client-certificate regression | [a33c878 -> 80960fa](https://github.com/encode/httpx/compare/a33c87852b8a0dddc65e5f739af1e0a6fca4b91f...80960fa31918d7663c3f4c3ad61661cf0e80628f) | 139 | Detect that verify=False returns before applying the supplied client certificate. |
| HTTPX client-certificate repair | [8ecb86f -> 89599a9](https://github.com/encode/httpx/compare/8ecb86f0d74ffc52d4663214fae9526bee89358d...89599a9541af14bcf906fc4ed58ccbdf403802ba) | 11 | Recognize that certificate loading now occurs; verify=False is a supported option. |
| HTTPX empty-Zstandard regression | [7df47ce -> 392dbe4](https://github.com/encode/httpx/compare/7df47ce4d93a06f2c3310cd692b4c2336d7663ba...392dbe45f086d0877bd288c5d68abf860653b680) | 174 | Detect erroneous decoding failure when an empty body reaches decoder flush. |
| HTTPX empty-Zstandard repair | [189fc4b -> 47f4a96](https://github.com/encode/httpx/compare/189fc4bcbe5f314128775dec66a616ac9a31ad48...47f4a96ffaaaa07dca1614409549b5d7a6e7af49) | 31 | Distinguish empty bodies from truncated compressed streams. |
| Resilience4j configuration-copy regression | [c846f9e -> ec72266](https://github.com/resilience4j/resilience4j/compare/c846f9e5f6e5f56d979a4e20e3a1fae1aa57ab95...ec72266fe7e34cd4804972b1499bac17b871e2a2) | 1746 | Detect that deriving a builder aliases and mutates the original configuration. |
| Resilience4j configuration-copy repair | [158b64c -> cec580d](https://github.com/resilience4j/resilience4j/compare/158b64c235ecec44791923e4155d7e74e1217c30...cec580d01b3fc33dcb501043463f18ca881b9779) | 59 | Recognize independent field copying and the regression tests. |

The Java introduction exceeds the normal 1500-line cap. Including it needs an explicit eval configuration (`max_diff_lines = 2000`); the manifest's recommendation is metadata and does not override the engine. No input limit or tool permissions have been changed. If we decide against that larger case, replace it rather than increasing the eight-case cap.

## Evidence and its limits

- Express: narrow execution of the actual send function with Transfer-Encoding and an ETag callback generates a validator before the regression, omits it at the defective head, and restores it at the repair. The upstream repair also includes an HTTP regression test. Full Express integration tests have not run locally.
- HTTPX certificates: narrow execution of the actual helper with a standard-library SSLContext and minimal certifi import stub ignores a deliberately nonexistent certificate path at the defective head. The repaired helper attempts to load it and raises FileNotFoundError. This establishes the bypass; a real mutual-TLS handshake has not run.
- HTTPX Zstandard: source inspection and upstream test_zstd_empty/test_zstd_truncated support the expected regression and repair. zstandard is not installed locally, so no local decoder test result is claimed.
- Resilience4j: the original introduction adds a builder that directly aliases the supplied configuration. The later fix copies fields and adds fromShouldNotMutateBaseConfig/fromShouldCopyAllFields. No local Java tests have run because no JDK is installed. Full history was fetched to locate the introduction; a shallow-history boundary was not treated as the original commit.

These are supported, agent-curated expectations, not an exhaustive issue inventory. Repairs are provisional controls, not certified clean diffs. Review their complete changes and adjudicate additional findings. A merged PR is not automatically correct, and an open/rejected PR is not automatically defective. Old public history may be familiar to models; this small set is a diagnostic baseline, not evidence of broad model superiority.

## Grading and isolation

Use [RUBRIC.md](RUBRIC.md), version draft-2, as the proposed shared judging policy. Its authored illustrations use three public diffs; they are not actual review results. Use the public cases to develop the rubric, then privately validate on representative production changes before choosing a production model/pass configuration. No private production case or storage directory has been created.

The same ten default criteria apply to every case. Expected answers, later fixes and evaluator notes stay outside the Reviewer checkout. Do not put answer-revealing commit messages or the above table in Reviewer inputs. Review only issues introduced or worsened by each case's own diff.

Keep each regression and its repair in the same calibration/held-out split. Eight cases contain only four underlying scenarios; report paired outcomes and do not treat the eight as independent samples. Repetitions measure variability but do not create additional independent scenarios. Freeze the case set, rubric, criteria and judge version for a comparison.

The version-1 custom JSON format and local runner are implemented; the rubric remains provisional pending actual judgments. `curation_status`, `pair`, source provenance, and line counts are evaluator metadata, not Reviewer instructions. Pending judgments must not silently become passes. Cross-repository aggregation is implemented and covered offline; no real model experiment or LLM judging has run.

## Earlier personal-project candidates

The user switched away from their projects because they wanted deeper examples. Those manifests are retained under `evals/reserve/`, outside the active set:

- Trip creation: user uncertain about the proposed issue.
- Resident pickup filter: user saw no issue and requested another diff.
- Amount entry: user agreed with the unbounded/non-finite amount finding.
- Async action tests: user saw no issue; still a possible control, but outside the current set.
- Finance profile introduction/repair: no user adjudication received.

The smartGC probe remains under `evals/verify_smartgc.cjs` as reserve evidence. It is not a verifier for the current open-source suites. No personal-project source, real profile data or historical author metadata is vendored here.
