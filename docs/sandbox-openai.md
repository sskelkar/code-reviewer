# OpenAI backend for sandbox tests

Updated 2026-10-26: test the sandbox using an OpenAI API key and the operator's selected **non-sensitive personal repository**. There is no requirement to author a sample project. Production reviews remain Claude-only; the normal `reviewer` CLI provider is unchanged. Existing synthetic inputs remain offline regressions and boundary probes. The [agreed connected-pilot plan](sandbox-architecture.md#agreed-connected-pilot-2026-10-26-not-implemented) supersedes the earlier synthetic-only live-test scope.

## Implementation

`backends/openai.py:OpenAIRunner` implements the existing injected runner interface. The two review flows retain their criteria, prompts, scheduling, validators, grounding, consolidation and reporting. The existing `ClaudeResult` is reused as a compatibility result container; that class name does not mean Claude processed an OpenAI request. No dependency or OpenAI SDK was added.

The adapter uses the Responses API with a fixed TLS destination, `api.openai.com/v1/responses`. It ignores ambient proxy/base-URL settings, refuses non-200 responses including redirects, bounds request/response sizes and does not copy HTTP error bodies or authorization headers into run logs. External network enforcement remains necessary: a fixed API destination in Python is not a firewall against a compromised guest.

Every call starts a fresh in-memory conversation. It sends the existing system/user prompt and translated structured-output schema. Translation closes object properties and makes declared fields required; it does not mutate the common prompt assets. Final replies still pass through the existing harness validators. Responses use `store: false`; continuations resend output/tool results and encrypted reasoning items where returned. This is an API storage setting, not a promise of zero provider retention. No automatic API retry is added; the harness owns retries and accounting.

An explicit model ID and operator-supplied input/cached-input/output USD-per-million-token rates are required. There is no implicit mapping from Haiku/Sonnet/Opus and no guessed price table. Rates and supported schemas must be checked for the selected model before a live test. No model or price has yet been selected for connected use.

## Local read-only tools

`backends/read_tools.py` implements only these functions inside the checkout:

| Tool | Semantics and bounds |
|---|---|
| Read | Relative path, one-based offset and up to 200 lines from a regular UTF-8 file |
| Grep | Case-sensitive literal text search, not a regular expression |
| Glob | Relative path matching using Python `fnmatchcase`, not a shell |

There is no Bash, code execution, write, web, MCP, skill or LSP tool. Each invocation exposes exactly the tool subset the role already allows; Comparator/Resolver-style calls with an empty tool set cannot acquire file tools by requesting them. Additional directories and provider/model changes in a call are rejected.

POSIX directory-descriptor traversal and no-follow opens reject escaping paths, symlinks, hardlinked files and special files; `.git` is excluded. Reads are capped at 1 MiB per file; enumeration at 10,000 entries/32 directory levels; a text scan at 16 MiB; result rows at 100 and text at 16,000 characters. Grep skips binary, oversized and non-UTF-8 files. Rejected tool requests return a generic error without host paths. File/count limits can leave review evidence incomplete: this is an experimental bounded tool implementation, not feature parity with Claude Code.

These checks reduce tool authority; they do not contain arbitrary Python/native execution after guest compromise. A read-only admitted snapshot, immutable runtime, unprivileged processes and external restrictions remain separate architecture gates.

## Cost, deadlines and cancellation

Each runner call permits at most eight API rounds, 32 tool calls, 2048 output tokens per API request, a 2 MiB request and a 4 MiB response. Before requesting another round, it estimates an allowance from serialized input bytes plus overhead, maximum output and explicit rates. Afterward it sums reported token usage using those rates. Cached input is counted separately so it is not charged twice. Persona reservations/reconciliation continue through the existing pool ledger.

These are local admission checks and cost estimates, **not a provider-enforced dollar limit or invoice**. Overhead/model behavior/rates can differ; in-flight billing may continue after a client disconnect. Missing usage or an ambiguous transport failure marks cost unknown, preserving the persona pool's conservative unknown-cost handling. Prior token counts remain visible where known.

The optional `run_cancellable` runner protocol passes the persona cancellation event into this adapter. It checks between requests/tools and after replies; HTTP waits use finite socket timeouts and replies have byte/deadline checks. An in-flight request cannot be instantly cancelled, and socket timeouts are not an external process deadline. The job launcher must still enforce a whole-VM deadline and termination. This does not change Claude's existing process-group cancellation behavior.

## Offline workload and commands

The dedicated `sandbox/openai_smoke.py` workload uses the actual adapter with a stateless simulated Responses transport. Every synthetic Reviewer must call the actual local Read dispatcher before returning its deterministic fixture finding. Both full review flows then validate, ground, consolidate and render reports as before. It never reads `OPENAI_API_KEY` or contacts the network; its placeholder key has no authority. This is protocol/deployment evidence, not an eval or model-quality result.

Run locally without a key:

```bash
python -m code_reviewer.sandbox.openai_smoke
python -m unittest tests.test_openai_backend
```

Build a separate no-network guest from the trusted bundle:

```bash
sh /tmp/build-offline-guest.sh /tmp/reviewer-source.tar /tmp/reviewer-openai-guest openai-offline
```

Launch with the three explicit artifact paths/digests from the [runbook](../sandbox/README.md), adding `--workload openai-offline`. This uses 2 vCPUs/1024 MiB and no NIC or attached disk. Host validation requires the offline OpenAI marker, both completed flows, expected fake call counts and one consolidated synthetic finding per mode. API round trips are internal to a runner call; the harness's call count is not an HTTP-request count.

The default fake-Claude smoke and the real-Claude no-auth startup probe remain available separately. No CLI command currently connects the guest to OpenAI or accepts a real key.

## Connected test: agreed next work

Follow the [canonical pilot sequence](sandbox-architecture.md#connected-implementation-sequence). It is planned, not implemented. No live-key or personal-repository command exists yet.

The first live review uses the operator-selected non-sensitive personal repository, a pinned change, explicit model/token rates and declared limits. Add a bounded input/report path instead of repurposing `openai_smoke.py`, whose fake replies and fixed finding counts are intentionally offline-only. A live review may validly find zero defects. Do not copy the target's names, code, PR metadata or resulting findings into this repository's tests/docs.

Use externally enforced default-deny networking and permit the required OpenAI HTTPS destination during review. Admission happens before that review phase, so the first pilot need not add GitHub egress or credentials to the review guest. If existing network tooling requires an explicit proxy, adapt the current direct HTTPS transport through trusted configuration; it currently ignores ambient proxy variables. A fixed destination in code or a single DNS-to-IP lookup is not proof of the external hostname boundary.

Provide a dedicated disposable API key to the guest through a bounded private runtime bootstrap, then keep it only in worker memory/environment outside the checkout. No key in images, source bundles, command arguments, logs, shared homes/keychains or chat. Revoke it after testing and destroy the VM on success/error/cancellation. **Guest compromise can expose/reuse this key and bypass the Python cost ledger**; that tradeoff was explicitly accepted for the controlled pilot. This is not production credential isolation.

The custom API relay, host-held credential broker and extra orchestration services are deferred. Standard network enforcement tooling does not become a custom inference service and must not inject the API key or grant arbitrary destinations. No silent host fallback and no OpenAI production-code path. Keep production jailer/non-root/read-only/execution/syscall gates and later Claude qualification explicit.

The [pre-implementation review](sandbox-architecture.md#review-before-connected-implementation-2026-10-26) records two current prototype defects to fix first: image pipeline status propagation and accounting for IO on skipped scan files. This documentation pass does not fix them or implement live access.

## Official API references

Checked against [Responses function calling](https://developers.openai.com/api/docs/guides/function-calling) and [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs). The protocol supports local function dispatch plus structured final JSON; output items must be preserved for stateless tool continuations. Live API/schema/account compatibility remains unverified until the connected test.
