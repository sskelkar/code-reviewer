# Claude adapter VM startup and authentication

Technical startup/authentication record for the runtime-selected Claude adapter. It uses the same review core as the other adapter; VM/host execution is a separate operator choice. The recorded no-key startup check does not implement authenticated dispatch. See the [shared contract](security-contract.md) and [VM runbook](sandbox-check.md).

## What this check establishes

The real Linux ARM64 Claude executable can be tested without a login, network interface or target repository. The runtime probe checks the pinned version, required CLI flags, an unauthenticated status and refusal of a headless invocation using the harness's command builder. It uses a newly created home/configuration directory and constructs the process environment explicitly, rather than inheriting host credentials, proxies or provider settings.

A passing offline probe establishes startup/argument compatibility and rejection without credentials. It does **not** establish that a subscription is authorized, that token refresh works, that inference succeeds, that tools are contained or that parallel model calls fit the memory limit. A provider response is still a separate test.

**Deployment result:** passed in the nested Firecracker guest. Claude Code 2.1.301 reported no credentials and rejected the harness-style headless invocation before inference. This used a 4 GiB guest with no NIC or attached disks, inside the dedicated 6 GiB mountless outer VM. The authentication method remains an operator choice; no real token or API key was used.

## Runtime artifact and provenance

Use the native Linux ARM64 distribution for this guest; the installed macOS executable cannot run in its Linux kernel. Pin the same release as the current workstation for the feasibility check: **Claude Code 2.1.301**.

The check acquires `manifest.json`, its detached signature and the platform binary from Anthropic's release service. Verify the manifest with an isolated GPG keyring and compare the signing fingerprint with the one in the official setup documentation:

```text
31DD DE24 DDFA B679 F42D 7BD2 BAA9 29FF 1A7E CACE
```

The verified manifest identifies `linux-arm64/claude` as 267,247,608 bytes with SHA-256:

```text
1e119036a802d359ba5f9886428e031586e8796f7a858d5a8ee179ac7b8604f0
```

For this release, the manifest signature verified and the downloaded binary matched that size/digest. This verification relies on Anthropic's published signing key and HTTPS documentation; it is not an independent audit of the binary. The builder checks an explicit expected binary digest again. Artifacts, keys, logs and VM disks stay outside Git. Do not run a downloaded installer against the operator's home as part of a guest image build.

The runtime probe uses 2 vCPUs and **4096 MiB RAM**, matching the documented 4 GiB minimum; the dedicated outer Lima VM needs additional RAM (6 GiB in this check). The fake-Claude smoke retains its 1024 MiB guest. The initial 1 GiB real-runtime attempt failed while unpacking the larger initramfs; this was an image/memory limit, not an authentication failure. The prototype copies installed Ubuntu libraries and uses a writable in-memory root. Replace that packaging with a qualified immutable image before real reviews; the startup check does not qualify an image for production.

## Supported authentication choices

Select the method explicitly; never silently switch billing/provider or reuse whichever credential happens to exist on the host.

| Operator's access | Supported candidate | Consequence |
|---|---|---|
| Claude Pro/Max/Team/Enterprise subscription | A dedicated `claude setup-token` OAuth token supplied through `CLAUDE_CODE_OAUTH_TOKEN`, or a fresh manual guest login | Avoids copying the host keychain/login files; token availability and organization policy still need qualification |
| Anthropic Console billing | `ANTHROPIC_API_KEY` | Separate API billing; do not assume a subscription covers these requests |
| Bedrock/Vertex/Foundry | The provider's supported guest credentials | Separate integration and endpoint requirements; not selected implicitly |

Anthropic documents `setup-token` for scripts and CI, with a one-year token. It is a long-lived credential, **not a job-scoped or harmless token**. Organization restrictions still matter; setup-token does not itself establish that a token belongs to the required organization. For a first subscription feasibility test, the operator must create a dedicated token using the supported flow and retain control of it. Do not paste it into chat, commit it, pass it as a process argument, bake it into an image or mount the entire home/keychain to supply it. Claude token transfer/expiry/revocation qualification is still pending; the connected OpenAI key path must not be repurposed for Claude without that work.

A fresh manual login is another supported path. The documentation supports pasting a returned authorization code when a browser cannot reach the CLI's callback server, as with SSH or containers. It requires authentication-network access and produces guest credentials; it has not been exercised here. Do not solve the callback problem by broadly exposing guest ports to the host.

**Do not add `--bare` to the subscription invocation.** In the checked CLI/docs it excludes OAuth credentials, including `CLAUDE_CODE_OAUTH_TOKEN`; API-key/API-helper authentication is supported there instead. The existing harness uses its explicit tools, MCP, slash-command and isolation flags. The runtime probe reuses that command builder. Its extra empty `--setting-sources` setting is only for this clean synthetic probe; no normal review behavior changes.

Environment switches disabling updates, nonessential traffic and cloud connectors reduce optional behavior. They are **not** an external firewall or proof that all integrations are disabled. Keep hard OS/network enforcement as its own gate.

## Network and credentials: next connected test

The offline Claude probe still has no NIC or credentials. The shared [VM supervisor](sandbox-connected.md) already supplies external policy and private bootstrap; this adapter's authentication/endpoints still need integration. Before introducing a real credential:

1. Select the intended authentication/billing method with the operator. Use a minimal prompt to qualify authentication without fetching a project first; supply the selected credential explicitly rather than copying a host login file.
2. Implement external default-deny networking for the guest. Allow only the explicitly selected inference/auth endpoints, with mandatory TLS verification; install only approved public CA material if required. A proxy environment variable alone is insufficient because the guest could bypass it.
3. Supply the credential only at job runtime through a bounded, private transfer. Keep it out of the immutable image, command arguments and captured console/logs. Treat guest compromise as credential exposure; destruction of the VM does not revoke a copied token.
4. With separate authorization, make one minimal synthetic inference call. Check JSON output, authentication method, provider/billing, deadline and usage. Then verify the actual Reviewer's Read/Grep/Glob restrictions and later persona concurrency in the qualified runtime.
5. Destroy the guest and verify termination. Confirm the token's supported revocation/rotation procedure before authenticated review. Do not claim account limits or the Python dollar ledger are an externally enforced spending limit.

The official network table includes `api.anthropic.com` for direct inference, `claude.ai`/`claude.com` for subscription login, `platform.claude.com` for OAuth exchange/refresh/revocation, and SSO endpoints where applicable. Which routes a setup-token inference actually requires must be measured under policy; do not allow every documented feature endpoint by default. Updates, plugins, MCP, browser integration and documentation lookups are outside this review workload.

We do not need to build a custom credential gateway preemptively. First qualify the supported direct authentication route with external network enforcement; add a gateway only if an actual credential/policy requirement demands it.

## Sources

- [Official setup and signature verification](https://code.claude.com/docs/en/setup): native ARM64 support, memory requirements, pinned releases and signing fingerprint.
- [Official authentication](https://code.claude.com/docs/en/authentication): subscription/API/provider choices, manual code entry, setup-token, credential precedence and bare-mode constraint.
- [Official CLI reference](https://code.claude.com/docs/en/cli-reference): headless invocation and auth-status interface.
- [Official network configuration](https://code.claude.com/docs/en/network-config): auth/inference destinations, optional traffic and proxy behavior.

Recheck the documentation and repeat the probes when changing the Claude release or authentication method. No successful authenticated inference is recorded by this offline check.
