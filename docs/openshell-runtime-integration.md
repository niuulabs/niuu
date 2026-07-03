# NVIDIA OpenShell as a Forge Session Runtime

Status: **proposal — not implemented**. Research notes and integration path for
running Forge sessions inside NVIDIA OpenShell sandboxes as an alternative to
the current mini-mode local-process runtime.

## What OpenShell is

[NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) is an open-source
(Apache-2.0, Rust) sandboxed runtime for autonomous AI agents. Alpha software —
v0.0.76 as of 2026-07, self-described "proof-of-life, single-player mode".

Components:

- **Gateway** — control plane (local daemon on `:18080`, or in-cluster via
  Helm). Owns sandbox lifecycle, policy delivery, credential mapping,
  inference routing. Driven via the `openshell` CLI (gRPC/HTTP under the
  hood; **no public API docs, no Python SDK** — the CLI with `-o json` is the
  stable programmatic surface today).
- **Sandbox + supervisor** — each sandbox runs a supervisor as PID 1 that
  dials *outbound* to the gateway, then launches the agent as a restricted
  child process. No inbound reachability needed from gateway to sandbox.
- **Compute drivers** — Docker, Podman, MicroVM (libkrun on macOS / KVM on
  Linux), Kubernetes (agent-sandbox CRDs), plus custom drivers over gRPC.
  Auto-detect order: Kubernetes → Podman → Docker.
- **Policy engine** — declarative YAML, schema v1:
  - `filesystem_policy` + `landlock` + `process` — **static**, locked at
    sandbox creation (Landlock LSM, seccomp, unprivileged UID).
  - `network_policies` — **dynamic**, hot-reloadable. Per-binary egress
    allowlists with L7 rules (REST method/path globs, WebSocket, GraphQL,
    **MCP method/tool-level rules**, JSON-RPC), enforce vs audit mode.
- **Providers / privacy router** — credentials mapped by the gateway and
  injected at the proxy, so raw secrets never sit in the sandbox. Agent LLM
  traffic can be forced through `https://inference.local` and routed to a
  configured backend.
- **Observability** — sandbox + security logs pushed to the gateway,
  OCSF JSON export.

Agents supported unmodified: Claude Code (full policy coverage), OpenCode
(partial), Codex, Copilot CLI, OpenClaw. Arbitrary commands work:
`openshell sandbox create --name x --from <image> -- <command>`.

CLI surface relevant to us:

```bash
openshell gateway add http://127.0.0.1:18080 --local --name local
openshell sandbox create --name forge-<id> --from <image> \
  --cpu 2 --memory 4Gi --env K=V --label session=<id> \
  --driver-config-json '{"docker":{"mounts":[{"type":"bind","source":"...","target":"/sandbox/work"}]}}' \
  -- <command>
openshell sandbox list -o json          # lifecycle: Provisioning → Ready → Error → Deleting
openshell sandbox get forge-<id> -o json
openshell policy set forge-<id> --policy policy.yaml   # dynamic sections hot-reload
openshell sandbox exec -n forge-<id> --tty -- bash
openshell forward start <port> forge-<id>              # host port → sandbox port
openshell service expose forge-<id> <port>
openshell sandbox upload / download                    # workspace-scoped file transfer
openshell logs forge-<id> --source sandbox
openshell sandbox delete forge-<id>
```

Bind mounts require `enable_bind_mounts = true` in the gateway TOML
(`[openshell.drivers.docker]` / `[openshell.drivers.podman]`).

## Why this matters for us

Mini mode today (`LocalProcessPodManager`) spawns Skuld + the agent CLI as
**raw processes on the host**: full access to the user's filesystem,
credentials, and network. That is the exact gap OpenShell closes, and it
lines up with work already in flight:

| Our need | OpenShell feature |
|---|---|
| Valkyrie tool-build spine: "real security", least-privilege build scopes | Per-sandbox static fs/process policy + per-binary network allowlists |
| Ravn self-evolution: agents that modify their own environment safely | "Break-safe" isolated sandboxes, locked host boundary |
| Bifrost as the single model gateway | Privacy router: force agent LLM traffic to `inference.local` → backend = Bifrost; API keys never enter the sandbox |
| Audit/chronicles | Full allow/deny audit trail, OCSF JSON export |
| MCP tool governance | L7 MCP policy: allow/deny per method and per tool name |

## Where it plugs in

The user-facing framing "a new runtime" maps onto the **`PodManager` port**
(`src/volundr/domain/ports.py:265` — `start/stop/status/wait_for_ready`),
which already has three implementations selected via the dynamic-adapter
pattern:

- mini mode → `volundr/adapters/outbound/local_process.py` (`LocalProcessPodManager`)
- cluster mode, direct → `volundr/adapters/outbound/direct_k8s_pod_manager.py`
  (`DirectK8sPodManager`; `niuu platform up` cluster default, e.g. k3d)
- cluster mode, GitOps → `volundr/adapters/outbound/flux.py` (`FluxPodManager`;
  HelmRelease CRs reconciled by Flux — the production default in the volundr
  Helm chart and `volundr/config.py`)

### Option A — `OpenShellPodManager` (recommended)

A fourth `PodManager` adapter. Per session, it:

1. renders a policy YAML from the session spec (workspace paths → filesystem
   policy; session scopes → network policies),
2. `openshell sandbox create --name forge-<session_id> --from <skuld image>
   --label session=<id> ... -- skuld` with the workspace bind-mounted (or
   volume-mounted) at the workspace path,
3. `openshell forward start <sdk_port> forge-<session_id>` (or
   `service expose`) so the niuu host can reach Skuld's WebSocket exactly as
   it does today via `/s/{session_id}/session`,
4. maps `status()`/`wait_for_ready()` onto
   `openshell sandbox get -o json` lifecycle phases
   (Provisioning → Ready → Error → Deleting),
5. `stop()` → `openshell sandbox delete`.

Skuld runs **inside** the sandbox and drives the agent CLI through the
existing `CLITransport` adapters, completely unchanged. This is the cluster
topology (Skuld-in-pod, as in DirectK8s/Flux) applied locally — architecture
stays symmetric across all runtimes:

```
niuu host :8080 ──ws──► forwarded port ──► [OpenShell sandbox]
                                             supervisor (PID 1)
                                             └─ skuld broker
                                                 └─ claude / codex / opencode CLI
                                             egress ──► policy proxy ──► allowlisted hosts
                                             LLM calls ► inference.local ──► bifrost
[openshell gateway :18080] ◄──outbound──── supervisor control session
```

Wiring follows `.claude/rules/dynamic-adapters.md` — no new mode enum, just
config:

```yaml
pod_manager:
  adapter: "volundr.adapters.outbound.openshell_pod_manager.OpenShellPodManager"
  gateway_url: "http://127.0.0.1:18080"
  sandbox_image: "ghcr.io/niuulabs/skuld:<tag>"
  workspaces_dir: "~/.niuu/workspaces"
  mount_mode: "bind"            # bind | volume | upload
  policy_template: "default"    # or path to a template
  max_concurrent: 8
  sdk_port_start: 9100
```

### Option B — agent-only in sandbox, Skuld outside (rejected)

A new `CLITransport` that drives `openshell sandbox exec`. Rejected: Skuld's
file manager and workspace operations assume co-location with the workspace;
transports assume streaming I/O with the CLI process; and it breaks
symmetry with cluster mode. Only worth revisiting if sandbox-per-*tool-call*
(rather than per-session) ever becomes a goal.

### Teams / flocks — multi-process sessions

Team sessions today are one Skuld broker + N `ravn daemon` persona processes
per session (mini mode: local processes via `ravn flock init/start`,
`local_process.py:856`; cluster mode: `ravn-*` containers composed into the
session pod). OpenShell handles this:

- **A sandbox is a container/VM, not a single-process jail.** The supervisor
  launches the entrypoint as a restricted child, which may spawn children
  (agent CLIs fork shells/git/npm constantly); `sandbox_pids_limit` is a
  driver knob because many processes are expected. `sandbox exec` can start
  more processes post-create; `service expose` supports multiple named ports
  per sandbox.
- **Model: team-in-one-sandbox.** Entrypoint = Skuld (or a flock bootstrap)
  which starts the ravn daemons exactly as mini mode does. The mini-mode
  *process* model maps to one sandbox better than the K8s multi-container
  pod does — OpenShell has no multi-container composition.
- **Caveat: policy, identity, resources are per-sandbox.** One
  `run_as_user`, one CPU/memory envelope, one policy set shared by the team.
  Network rules are per-*binary*, so `skuld`/`claude`/`ravn` get distinct
  egress rules, but two personas running the same binary are
  indistinguishable to the policy engine. This gives a hard wall around the
  team, not between teammates.
- **Later refinement: sandbox-per-member** (NVIDIA's own "sandbox per agent
  and sub-agent" philosophy) with the workspace bind-mounted into each and
  per-persona policies — relevant for Valkyries with differing scopes. Costs
  real plumbing: ravn mesh traffic then crosses sandbox boundaries via
  host-forwarded ports and the policy proxy. Not phase 1.

### Non-goal — replacing mini mode wholesale

Mini mode's raw-process runtime stays the default. It is dependency-free
(no Docker/Podman), instant-start, and lets developers run Skuld from the
working tree. OpenShell becomes an *opt-in* runtime for security-sensitive
sessions (Valkyrie tool builds, autonomous Ting runs) and, later, a hardened
default.

## Integration path

### Phase 0 — spike (no code)

Manual validation on macOS + Linux; kills the proposal cheaply if any fail:

1. Install (`uv tool install openshell`), gateway up, Docker driver,
   `enable_bind_mounts = true`.
2. `openshell sandbox create --from ghcr.io/niuulabs/skuld:<tag>` with a
   bind-mounted workspace, Skuld as the command; confirm Skuld starts and its
   WebSocket is reachable through `openshell forward`.
3. Confirm the agent CLI inside can reach the niuu host API (`:8080` from
   sandbox — host-gateway reachability per driver) and Anthropic (or
   `inference.local` → Bifrost) under an enforce-mode policy.
4. Measure create→Ready latency and per-sandbox memory overhead vs
   `LocalProcessPodManager` at `max_concurrent` sessions.
5. Flock viability: start a flock (Skuld + 2 ravn daemons) inside one
   sandbox; confirm intra-sandbox **loopback** traffic (Skuld ↔ ravn mesh on
   127.0.0.1) is not routed through the policy proxy, and that the pids
   limit accommodates a full team.
6. Probe the gateway's gRPC/HTTP API (it exists — CLI/TUI use it) to see if
   shelling out can be replaced later; check `compute_driver.proto`.
7. macOS specifics: Docker Desktop vs `podman machine` vs libkrun MicroVM;
   bind-mount I/O throughput across the VM boundary.

### Phase 1 — `OpenShellPodManager` adapter

- New adapter in `src/volundr/adapters/outbound/openshell_pod_manager.py`,
  shelling out to the `openshell` CLI with `-o json` (pin the version;
  CLI contract is the API until the gateway API is documented).
- Static default policy: workspace read-write, `/usr` `/lib` `/etc`
  read-only, non-root user, network audit-mode allowlist seeded with
  Anthropic/OpenAI endpoints + niuu host + git remotes.
- Config via the dynamic-adapter pattern (above); mini mode default
  untouched. Tests mock the CLI boundary (no Docker in tests, per
  `.claude/rules/database.md` spirit).
- Session labels (`--label session=<id> user=<id>`) for reconciliation on
  restart, mirroring `state_file` recovery in `LocalProcessPodManager`.

### Phase 2 — per-session least-privilege policy

- A `SessionContributor` (`ports.py:1406`) that renders the policy from the
  session spec: repo remotes → git host endpoints, declared MCP servers →
  MCP method/tool rules, tracker/API scopes → REST path rules.
- Directly consumes the Valkyrie tool-build scope work (least-privilege
  build scopes at workload exchange) — those scopes become `network_policies`
  entries instead of advisory metadata.
- Flip from `audit` to `enforce` once the audit logs show the allowlist is
  complete.

### Phase 3 — credentials + inference through the gateway

- Route agent LLM traffic via `inference.local` with Bifrost as the backend;
  drop `ANTHROPIC_API_KEY` injection into session env entirely.
- Move git/tracker tokens to OpenShell **providers** with request-time
  credential injection at the proxy (`request_body_credential_rewrite`,
  `credential_signing`) so Valkyrie-built tools never see raw secrets.

### Phase 4 — audit into the platform

- Ingest OCSF JSON export (`openshell logs` / export pipeline) into
  sleipnir events + the audit plugin; attach allow/deny decisions to session
  chronicles.

### Phase 5 — cluster mode convergence (later, separate decision)

- OpenShell's Kubernetes driver (Helm chart, agent-sandbox `Sandbox` CRDs,
  supervisor sideload) as an alternative to the cluster adapters. The bar
  is highest here: production runs `FluxPodManager` (GitOps — HelmRelease
  CRs reconciled by Flux), and OpenShell's gateway provisions sandboxes
  imperatively, which cuts against that reconciliation model. Only after
  the local runtime has proven out and OpenShell is past alpha — this
  replaces a working production path and needs its own evaluation.

## Risks and open questions

- **Alpha software.** v0.0.76, "proof-of-life". Pin the version, wrap all CLI
  parsing in one module, expect breaking changes. Do not put it in the
  default path yet.
- **No documented gateway API / SDK.** Shelling out to the CLI is the
  contract. Acceptable for an adapter; revisit when the gRPC surface is
  documented (spike item 5).
- **macOS overhead.** Requires Docker Desktop / podman machine / libkrun;
  container-crossing bind mounts are slow on macOS. May need
  `mount_mode: volume` + upload/download sync for the file manager, which
  changes workspace persistence semantics (spike item 6).
- **Startup latency + density.** Container/VM per session vs ~instant
  process spawn; matters for Ting saga fan-out. Measure in phase 0.
- **Dev loop.** Skuld runs from an image, not the working tree — bind-mount
  the source dir in dev, or keep mini mode for Skuld development.
- **Host API reachability.** Skuld inside the sandbox must reach niuu on
  `:8080`; per-driver host-gateway addressing differs (Podman machine uses
  `192.168.127.254`). Must be encoded in the default policy.
- **Policy attachment default.** Docs don't state per-sandbox vs gateway
  default precedence clearly — verify in spike.
- **Chronicle/workspace lifecycle.** Sandbox deletion vs workspace
  retention: workspace must outlive the sandbox (bind/volume mount, never
  sandbox-internal overlay disk).

## Sources

- <https://github.com/NVIDIA/OpenShell>
- <https://docs.nvidia.com/openshell/about/how-it-works>
- <https://docs.nvidia.com/openshell/sandboxes/manage-sandboxes>
- <https://docs.nvidia.com/openshell/reference/sandbox-compute-drivers>
- <https://docs.nvidia.com/openshell/reference/policy-schema>
- <https://docs.nvidia.com/openshell/about/supported-agents>
- <https://blogs.nvidia.com/blog/secure-autonomous-ai-agents-openshell/>
- <https://canonical.com/blog/nvidia-openshell-ubuntu-announcement>
