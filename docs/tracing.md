# Local review traces

Optional Langfuse tracing records how a review executes: stages, model calls, read tools, retries, validation counts, usage and outcomes. Both strategies and providers use the same recorder. Tracing does not change prompts, budgets, stopping rules, findings or posting.

## Start local Langfuse

Use a running Docker engine with Compose and sufficient memory. Allocate about **6 GiB to Docker** when sharing it with other apps; the full Langfuse stack does not fit reliably in the observed 2 GiB Colima VM. Resizing an existing Docker VM can interrupt other containers, so schedule that separately. Langfuse runs in Docker, outside the dedicated Lima/Firecracker review worker.

From the repository root:

```bash
python3 deploy/langfuse/setup.py
docker-compose --env-file "$HOME/.code-reviewer/langfuse/local.env" \
  -f deploy/langfuse/compose.yaml up -d
```

Use `docker compose` instead of `docker-compose` if Compose is installed as a Docker plugin. Initial migrations can take a few minutes. Open **http://localhost:4000**. The local account is `reviewer@localhost.invalid`; its generated password is the `LANGFUSE_USER_PASSWORD` entry in `~/.code-reviewer/langfuse/local.env`. Setup creates the `reviewer` project and its keys automatically. The setup script preserves an existing file rather than rotating credentials underneath stored data.

Only `127.0.0.1:4000` is published; host port 3000 and supporting database/storage ports are untouched. Web/worker server version is 4.72.0. All six images are digest-pinned in [compose.yaml](../deploy/langfuse/compose.yaml), including the MinIO storage/client images. Deployment telemetry is disabled. No Langfuse Cloud account is involved.

The private environment file contains deployment passwords and local project keys; keep it outside Git. Persistent named Docker volumes contain Langfuse's data. Container logs rotate at 10 MiB × 3 files per service. This does not cap database/object-storage growth: inspect `docker system df` and `docker stats`, delete unwanted traces in Langfuse, and choose an explicit retention/backup policy before accumulating long-lived data. This integration does not promise automatic age-based retention or backups.

Stop/start without deleting traces:

```bash
docker-compose --env-file "$HOME/.code-reviewer/langfuse/local.env" \
  -f deploy/langfuse/compose.yaml stop
docker-compose --env-file "$HOME/.code-reviewer/langfuse/local.env" \
  -f deploy/langfuse/compose.yaml up -d
```

`down` removes this stack's containers/network while preserving named volumes. `down --volumes` destroys its stored traces, accounts and project data; it is an explicit reset, not routine cleanup. Updating image versions/digests requires checking server/SDK compatibility and database migrations.

## Configure tracing for reviews

The core reviewer and VM recorder support Python 3.9+. **Host Langfuse export requires Python 3.11+** and the optional dependency:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[tracing]'
. "$HOME/.code-reviewer/langfuse/local.env"
export LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY
```

The generated file is trusted local operator configuration, not repository input. Only the two exported Langfuse project keys are needed by the reviewer; do not put them in `review.toml`. Tracing is enabled by default. To opt out, add this setting to the existing operator file outside Git:

```toml
tracing = false
```

Run the usual `reviewer review …` command with your selected provider/authentication/strategy. Metadata recording is on by default; local export still needs the SDK and project-key environment variables above. No additional flags, model credentials or Langfuse settings go inside the repository being reviewed. Invalid configuration, preflight and dry-run never contact Langfuse or read its keys, even when tracing is enabled.

After cleanup, trace metadata is saved atomically before export starts. A successful export prints a local trace link and saves it in `stats.json` under `tracing`. Bounded metadata remains under `trace`, including a dropped-observation count. If Langfuse, keys, the SDK or a compatible host Python are unavailable, the review still completes and prints a warning; metadata remains available with the stats. Failed reviews can have a separate private `trace-<opaque ID>/stats.json` directory. There is no automatic retry daemon. The export subprocess has a 20-second total deadline and receives no model credentials or ambient OTLP/proxy settings.

## View a review's trace

After running a review from the configured terminal, open the **`Trace:` URL** printed when export succeeds. Alternatively, open [the local reviewer project's traces](http://localhost:4000/project/reviewer/traces), sign in with the local setup account and select a review. The UI is on your Mac; no Langfuse Cloud login is needed. Leave the local services running while viewing or exporting traces.

To find a particular saved review, open its `stats.json` beside `report.md`:

- `tracing.url` is the direct browser link after a successful export.
- `tracing.trace_id` identifies the review in Langfuse. Trace identifiers are opaque; repository names and PR identifiers are not recorded.
- `tracing.exported` reports whether the exporter delivered the trace successfully.
- `trace.records` contains the bounded local metadata; `trace.dropped` counts omitted observations.

Open the review to inspect the stage/agent/model/tool hierarchy, elapsed times, retries and generation usage. VM traces appear after the review and cleanup; ingestion can take a short time to become visible. Refresh the page if a newly exported trace is not immediately listed.

## Missing traces

| What you see | Meaning and action |
|---|---|
| No `Trace:` link and no `tracing` or `trace` entry in stats | This run did not record tracing, for example because tracing was disabled or it used an older installation. Preflight, dry-run and configuration refusals intentionally skip tracing. Past traces cannot be reconstructed; ensure tracing is enabled for the next review. |
| `trace` exists but `tracing.exported` is false | Local metadata was saved, but export failed. Check the terminal warning, Python 3.11+ tracing environment, installed extra, exported local project keys and running Langfuse services. Fix setup before the next review; saved traces are not automatically retried. |
| `tracing.exported` is true but the list looks empty | Open `tracing.url` directly, sign into the local `reviewer` project and allow ingestion to finish before refreshing. |
| A failed VM review has only host stages | A VM that cannot return results also cannot return its internal trace records. Available host failure metadata can be saved separately under `trace-<opaque ID>/stats.json` in the runs directory. |

Tracing defaults to on. An explicit `tracing = false` in the selected operator file overrides that default; `--config` can select a different operator file. Loading project keys in one terminal does not export them into another terminal session.

## What appears in the UI

One review is one trace. Stable typed observations show review preparation, criteria resolution, review passes, grounding/consolidation, conflict verification, report writing and checkout cleanup. Model-call agents contain generation observations and available tool executions. Rejected output-validation attempts are marked as errors with numeric rejection counts; prompts and validation messages remain excluded. Persona worker threads explicitly inherit trace context, retaining their concurrency and parent relationships.

- **OpenAI:** individual model turns, HTTP attempts and actual Read/Grep/Glob calls. HTTP retries do not duplicate generation usage.
- **Claude:** one aggregate generation per native CLI invocation, with reported total usage/turns. Internal native tool events are not captured by the current adapter; no synthetic tool history is invented.
- **VM:** host execution and guest acquisition/admission/review observations share an opaque identity. The guest returns trace records in `stats.json` through the existing private SSH/vsock path. The host validates one connected guest subtree, rejects collisions with host span IDs and exports after confirmed cleanup. See [the transport and firewall diagrams](sandbox-architecture.md#private-control-and-result-channel).

The recorder times host preparation, VM image preparation and connected VM execution as stages; it does not separately trace every image-build/worker-start subprocess. Guest traces are returned after completion, not streamed live. If a VM dies before returning records, only the host-observed timeline survives. Host/guest wall-clock skew can affect placement; recorded durations are not upload durations.

Metadata contains role/persona/pass/model, fingerprints, counts, timings and closed error codes. It excludes prompts, diffs, source, findings text, raw model replies, PR/repository identifiers, paths, search arguments, exception messages and authentication material. Trace input/output consists of safe root summaries. This helps diagnose execution and compare usage, but cannot reconstruct complete model context or establish finding correctness.

### Tokens and prompt caching

Generation usage uses provider-reported fresh input, output, cache-read and cache-creation categories where available. Flags distinguish missing cache fields from reported zero. OpenAI cache-write counts, when present, are additional metadata because the existing billing adapter has no separate write-price configuration; they are not added again to input totals. Claude uses its aggregate reported categories. Parent agents may carry usage summaries as metadata, but only generations contribute usage to Langfuse totals.

Compare cache-read tokens/share across roles and passes with observed latency. OpenAI API calls show the existing operator-rate cost estimate. `cache_savings_usd` is calculated only when writes are explicitly reported as zero, avoiding an unsupported net-savings claim when write charges are unknown. Claude's reported aggregate cost is retained without inventing an uncached comparison price. Tracing measures caching; it does not enable a cache policy or change prompts.

Subscription traces show usage; review reports retain cost N/A. Langfuse receives a zero cost override for subscriptions and unknown costs to suppress its automatic API-price inference. That UI zero is **not** a subscription invoice or evidence of quota savings. Unknown/incomplete usage is marked; no token counts are inferred from source text.

## Verification and code map

Unit tests use synthetic repositories, fake providers and exporters. With the optional SDK installed, they also verify timestamp/parent/usage mapping in memory. Real local trace ingestion is checked separately; real authenticated provider usage needs a separately authorized review.

| Code | Responsibility |
|---|---|
| [tracing/recorder.py](../src/code_reviewer/tracing/recorder.py) | Standard-library context, bounded recorder, closed metadata schema and guest validation |
| [tracing/review.py](../src/code_reviewer/tracing/review.py) | Host root lifecycle, guest merge, private persistence and nonfatal export |
| [tracing/langfuse.py](../src/code_reviewer/tracing/langfuse.py) | Isolated optional SDK/OTLP export to the fixed loopback endpoint |
| [shared/llm.py](../src/code_reviewer/shared/llm.py), [backends/openai.py](../src/code_reviewer/backends/openai.py) | Role, attempt, generation, HTTP and tool boundaries |
| [sandbox/settings.py](../src/code_reviewer/sandbox/settings.py), [guest.py](../src/code_reviewer/sandbox/guest.py) | Trusted enable/context handoff and bounded stats export |
| [test_tracing.py](../tests/test_tracing.py) | Privacy, failures, bounds, threading, both flows, guest transfer and optional SDK checks |

Local server 4.72.0 ingestion is verified with synthetic reviews in both flows and adapter paths. Stored observations preserve one logical root, parent links, original timing, usage and metadata-only capture. Remaining provider usage and adapter qualification is tracked in [the roadmap](proposal.md#checklist). Tracing does not move prompts into Langfuse, add evaluations, expand guest networking or introduce another review flow.
