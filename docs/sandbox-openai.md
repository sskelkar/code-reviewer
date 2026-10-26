# OpenAI backend for sandbox tests

Updated 2026-10-26: test the sandbox using an OpenAI API key and the operator's selected **non-sensitive personal repository**. There is no requirement to author a sample project. Production reviews remain Claude-only; the normal `reviewer` CLI provider is unchanged. Existing synthetic inputs remain offline regressions and boundary probes. The [agreed connected-pilot plan](sandbox-architecture.md#agreed-connected-pilot-2026-10-26) supersedes the earlier synthetic-only live-test scope.

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

POSIX directory-descriptor traversal and no-follow opens reject escaping paths, symlinks, hardlinked files and special files; `.git` is excluded. Reads are capped at 1 MiB per file; enumeration at 10,000 entries/32 directory levels; a text scan at 16 MiB; result rows at 100 and text at 16,000 characters. Grep skips binary, oversized and non-UTF-8 files; raw bytes actually read from skipped files consume its scan allowance too. One-byte lookahead detects overflow and returns a refusal. Rejected tool requests return a generic error without host paths. File/count limits can leave review evidence incomplete: this is an experimental bounded tool implementation, not feature parity with Claude Code.

These checks reduce tool authority; they do not contain arbitrary Python/native execution after guest compromise. The connected pilot adds an unprivileged guest worker and external restrictions. An immutable admitted snapshot/runtime and guest execution/syscall policy remain separate production gates.

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

The default fake-Claude smoke and the real-Claude no-auth startup probe remain available separately. These offline commands accept no real key or connectivity; the separate [connected launcher](sandbox-connected.md) supports the controlled live pilot.

## Connected personal-repository pilot

The [connected runbook](sandbox-connected.md) documents the implemented Linux supervisor, private input/bootstrap, external network policy, runtime key prompt/descriptor and bounded report export. It follows the [agreed sequence](sandbox-architecture.md#connected-implementation-sequence). The ordinary `reviewer` CLI remains unchanged; this is a separate explicitly selected module.

Prepare a bounded capsule from the chosen non-sensitive personal clone and full base/head commit hashes. No Git/config/hooks execute on the host; clean Git reconstruction and tree admission happen inside the guest. The first live review does not require a new synthetic project. Keep the existing synthetic fixtures for automated regressions and no-key boundary tests only. A live review may validly find no defects; fixed smoke finding counts do not apply.

The connected client uses an explicit trusted CONNECT route through a standard Squid process outside the guest. The default backend still ignores ambient proxy variables; `proxy=('192.0.2.1',3128)` is explicitly injected only in the guest worker. TLS targets `api.openai.com`, verifies its certificate using the public CA bundle and never sends the API authorization header in the CONNECT request. External nftables rules block direct guest egress; the proxy permits only the approved CONNECT authority and denies private/reserved destinations, with an additional host packet guard on the proxy UID. This is not a custom inference relay or credential-injection gateway and does not inspect encrypted HTTP content or enforce API paths/model spending.

A dedicated disposable key is requested only after admission and boundary probes pass. It is supplied privately to guest memory, outside the checkout, via one-shot vsock bootstrap; no image, command-line argument, host home/keychain or repo configuration carries it. **Guest compromise can expose/reuse the key and bypass the Python ledger.** The operator must revoke it after testing; VM cleanup does not revoke it.

The two recorded prototype defects are fixed: image generation checks all archive stages; Grep charges raw read bytes even when a file is skipped. Seventeen new offline regressions cover the connected admission/policy/key/export path, plus actual no-key Firecracker boundary/cancellation/deadline checks in the dedicated worker. No real API key, model request or personal repository has been used during implementation checks. Account/model/schema compatibility and finding quality remain unmeasured.

The [production gates](security-roadmap.md) still include a jailer, immutable snapshot against native compromise, guest syscall enforcement, stronger crash recovery and Claude-specific qualification. OpenAI pilot success does not authorize or qualify production-code use.

## Official API references

Checked against [Responses function calling](https://developers.openai.com/api/docs/guides/function-calling) and [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs). The protocol supports local function dispatch plus structured final JSON; output items must be preserved for stateless tool continuations. Live API/schema/account compatibility remains unverified until the connected test.
