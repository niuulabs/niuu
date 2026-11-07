# Resident ravns

A **resident** is a long-lived, named ravn with a chat room — same format as
a Valkyrie, but conversational. You join and leave its chat; it keeps
working, remembers via Mimir, launches platform work (research campaigns,
spec stacks, planning), and reports back into the room when results land.

## Deploy

Residents are **infrastructure, not Forge sessions**. Helm-managed residents
are deployments of the existing skuld chart (`charts/skuld`) in resident mode.
The pod is a Skuld broker in room mode plus one ravn daemon sidecar — a flock
of one. Nothing creates residents through the Forge sessions API.

Volundr persists control-plane-managed residents in `resident_runtimes` with a
global UUID, owner, tenant, deployment profile, backend, engine, desired and
observed state, backend reference, endpoints, capabilities, and conditions.
Ravn reads these records through the authenticated target Volundr API. Guild
adds the owning `instance_id` when aggregating through Yggdrasil.

Available deployment profiles are operator configuration, not request input.
Configure `residentRuntimeProfiles` in the Volundr chart; disabled profiles and
backend-only `deployment` data are not returned by
`GET /api/v1/ravn/deployment-profiles`. No profiles are enabled by default.

On a target configured with `FluxPodManager`, an enabled `helmrelease` + `ravn`
profile is deployable through the same adapter instance that owns Forge session
HelmReleases. The profile's private `deployment.values` are merged with the
runtime identity and passed to the existing skuld chart; no second Flux client,
chart, state store, CRD, or Session row is involved.

On a target configured with `OpenShellGatewayPodManager`, an enabled `openshell`
+ `ravn` profile is handled by that same adapter instance. It creates one native
OpenShell sandbox, attaches owner-bound dynamic credential providers, starts the
existing Skuld and Ravn processes, and exposes Skuld through the gateway. It does
not create a Forge Session row or a parallel OpenShell client. OpenShell profiles
support chat, restart, gateway logs, and Skuld usage reporting; they must not
advertise suspend or metrics.

### Local mini runtime

Mini mode composes the same resident control port with one of two local
runtimes, selected in `~/.niuu/config.yaml` (or `NIUU_RESIDENTS__RUNTIME`):

```yaml
residents:
  runtime: process   # default; `docker` opts into the container runtime
```

- **`process` (default)** — `HostProcessResidentRuntimeController` runs each
  Ravn resident as a Skuld broker and a `ravn daemon` process on this host. No
  container engine is needed. It offers the `ravn-local` profile only: NemoClaw
  and NemoHermes ship as images and need the Docker runtime.
- **`docker`** — `LocalContainerResidentRuntimeController` uses the Docker API
  to run the Ravn, NemoClaw, or NemoHermes image without Kubernetes, offering
  `ravn-local`, `nemoclaw-local`, and `nemohermes-local`. Docker must be
  running: when it is not, the Forge plugin fails to start with the remedy in
  the error instead of silently running without residents.

The Ravn deployment wizard is the same for both: select `Local Forge`, then a
profile. `ravn-local` keeps its id across runtimes, so switching runtime
redeploys existing Ravn residents on the next reconciliation with their durable
state intact. Stop any containers the Docker runtime left running before
switching to `process`, or both copies run.

Configure the models the local Bifröst can actually route in `~/.niuu/config.yaml`:

```yaml
bifrost:
  providers:
    local-vllm:
      base_url: https://vllm.example.test
      models:
        - nvidia/nemotron-3-super
      # Optional credentials stay in an environment variable, not this file.
      api_key_env: LOCAL_VLLM_API_KEY
```

Mini passes this configuration to its hosted Bifröst and derives the resident
profile model choices from the configured provider models. `./start-dev` binds
the server on all host interfaces while publishing its detected LAN address;
resident containers call the shared host through `host.docker.internal`, and
resident processes call it on loopback.

Each resident receives a private loopback-only port and durable directories
under `~/.niuu/residents/<resident-id>/sandbox/`: `workspace`, `config` (the
container's `.volundr`), and `home` with `.codex` and `.claude`. Existing Codex
and Claude credential files are copied with mode `0600` only when the resident's
durable destination does not exist. Restart and suspend preserve these
directories; deleting the resident removes its container or processes, durable
directory, and engine machine credential unless retention is configured.

Resident processes start from the same materialized spec as the container:
`/sandbox` paths map onto the directories above, `HOME` is the resident's
`home`, the Skuld service and the Ravn HTTP channel bind `127.0.0.1` on ports the
OS assigns, and the mesh uses per-resident IPC sockets. They inherit only
process-bootstrap variables (`PATH`, locale, proxy and CA settings) from the
platform, never its `SKULD__*`, `RAVN_*`, or `NIUU_*` configuration. Their output
is recorded, timestamped, in `config/skuld.log` and `config/ravn.log`, which the
resident logs view reads. They live as long as the platform: shutting it down
stops them, suspend stops them, and resume starts them again. If the platform
dies without stopping them, the next start stops the orphans recorded in
`host-processes.json` before starting the resident again, so a resident never
runs twice.

The browser still reaches chat through `/s/<resident-id>/sessions/<session-id>/session`.
The root host sends that route to Guild, Guild authorizes the owning target, and
embedded mini targets call the existing Volundr resident service directly. No
local-only Ravn API or alternate chat protocol is involved.

Configure persistent storage in the profile when suspend/resume must retain
workspace data. Suspension sets the existing chart's `replicaCount` to `0` and
resume restores it to `1`; `emptyDir` data does not survive that transition.

```yaml
residentRuntimeProfiles:
  - id: ravn-helm
    enabled: true
    displayName: Resident Ravn (Helm)
    backend: helmrelease
    engine: ravn
    capabilities: [chat, runtime.restart, runtime.suspend, logs, metrics, usage]
    defaultModel: gpt-5.6-sol
    allowedModels: [gpt-5.6-sol]
    deployment:
      values:
        persistence:
          enabled: true
          emptyDir: false
          existingClaim: volundr-sessions
        resident:
          persona: product-steward
          platform:
            enabled: true
            baseUrl: http://niuu-volundr.volundr.svc.cluster.local
```

Deploy with `POST /api/v1/ravn/ravens`. The request contains the selected
Guild `instance_id`, profile ID, resident name, persona, and an allowed model.
The returned resident carries that `instance_id`, normalized desired/observed
state, HelmRelease and Deployment references, chat endpoint, capabilities, and
Flux/workload conditions.

Restart, suspend, resume, and delete use the resident UUID in the `/ravens`
path and its returned `instance_id` as the query parameter. Guild resolves that
visible target and forwards the authenticated command without probing another
cluster.

### Upgrades follow the platform

A resident runs the version its Volundr deploys now, not the one it was created
with. When Volundr is deployed with a newer Skuld chart or image (the
`podManager` `chart_version`, the profile's `deployment.values` images, or
OpenShell's sandbox image), reconciliation moves each running resident to it
once and logs `Upgrading resident <name> from <old> to <new>`. The running
version is in the resident's `backendRef` (`platformVersion` on Flux, `image` on
OpenShell).

- **Flux:** the HelmRelease is replaced with the chart version and values the
  profile renders now, keeping the storage it mounts (its own
  `resident-<id>-home`/`-workspace` PVCs) and its shim token Secret, so it is a
  rolling update with its disks and channels intact. The release records the
  version in `niuu.world/platform-version` and the one it replaced in
  `niuu.world/replaced-platform-version`; neither is applied again by
  reconciliation, so an edited release is left alone and an old Volundr replica
  does not move a resident back during Volundr's own rollout.
- **OpenShell:** a sandbox's image is fixed at creation, so the sandbox is
  recreated on the new image and the resident keeps its record, credentials and
  shim token. This happens only when its sandbox keeps the workspace on a claim:
  its own (below), or the profile's `persistence.existingClaim` with a
  `mountPath` at or above the sandbox workspace. Without one (the profile sets
  `persistence.enabled: false`, or Volundr manages no claims), nothing ever
  recreates the sandbox: the upgrade is held with a `PlatformUpgrade` /
  `UpgradeHeld` condition ("No persistent workspace; the sandbox image can't
  change without losing /sandbox"), shown in the resident's conditions, and
  Restart restarts its processes in place as before.

### OpenShell residents' workspaces

Every OpenShell resident gets its own PVC, `resident-<id>-workspace`, mounted
at `/sandbox/workspace`. Volundr creates it on deploy and deletes it with the
resident, in the namespace where the OpenShell gateway runs sandboxes
(`podManager.kwargs.resident_workspace_namespace`, which the chart defaults to
`storageAdapter.kwargs.namespace`; the chart grants Volundr the PVC rights there
with the `<release>-resident-workspaces` Role). Size, storage class and access
mode come from `resident_workspace_size`, `resident_storage_class` and
`resident_access_mode`, and a profile overrides them in
`deployment.volumes.workspace`. The claim holds Skuld's conversations, Ravn's
`RAVN_STATE_DIR` (channels and the notification outbox), Hermes' and OpenClaw's
state; a Ravn resident's `~/.ravn` (memory.db, the drive-loop queue and cron
state, skills) is a link to `/sandbox/workspace/.ravn-home` on it. The rest of
`/sandbox` (rendered config, logs, the image's tools, the Codex CLI home) is
rebuilt with each sandbox.

The claim is ReadWriteOnce and carries no node affinity of its own: a sandbox
mounting it is scheduled wherever Kubernetes places it, and the volume attaches
to that node. A recreated sandbox is created only once the old one is gone, so
the volume is free to move. A storage class whose volumes are node-local binds
the resident to that node.

A resident whose sandbox predates its claim is moved onto it once, by its next
deployment (reconcile hands it over with a `WorkspaceMigration` / `Migrating`
condition) or its next Restart. Volundr logs `Migrating resident <name>'s
workspace to a persistent volume` and:

1. measures the workspace (and `~/.ravn`); more than
   `resident_workspace_migration_max_size` (default 1Gi) or the claim's size is
   refused before anything changes;
2. creates the claim and takes the migration's lease on it;
3. stops the resident's processes and archives its state out through the
   sandbox exec channel;
4. creates a new sandbox on the current image with the claim mounted, restores
   the archive into it before any resident process starts, and reads it back:
   every path, size and SHA-256 must match;
5. only then deletes the old sandbox and starts the resident on its claim.

Until step 5 the old sandbox is never touched beyond stopping its processes. If
anything fails, the new sandbox is removed, the old one's processes are started
again, and the resident reports `WorkspaceMigration` with the reason
(`WorkspaceTooLarge`, `VerificationFailed`, `ClaimInUse`, `MigrationFailed`); it
is retried on Restart or when Volundr restarts. Progress is kept on the claim's
annotations (`volundr.niuu.io/workspace-migration`: `copying`, `verified`,
`complete`, with the holding Volundr and its lease), so a Volundr that restarts
mid-copy, or a second replica, resumes or waits rather than starting over. A
claim that already holds a workspace is never overwritten.
- **Restart** re-renders the resident on the current version, where that keeps
  its state. **Resume** starts a suspended Flux resident on the current
  version; suspended residents are not woken to upgrade.
- A failed upgrade marks the resident failed with a `PlatformUpgrade` /
  `UpgradeFailed` condition and is not retried until the version changes again,
  the resident is restarted, or Volundr restarts.
- An OpenShell resident's old sandbox that is slow to go is not a failed
  upgrade. Every removal of a resident sandbox waits
  `resident_sandbox_delete_timeout_seconds` (180s; sessions keep
  `resource_delete_timeout`, 120s). If the sandbox is still being deleted after
  that, the resident reports `PlatformUpgrade` / `Upgrading` and its
  redeployment waits for the sandbox to go, then recreates it on its workspace
  claim, retrying on later passes. If the gateway refuses the delete, the
  resident keeps running on its old sandbox (`PlatformUpgrade` /
  `UpgradeRetrying`) and the next pass tries again.

Key chart values:

| Value | Meaning |
|---|---|
| `resident.enabled` | Deploy this release as a resident (broker room mode + ravn sidecar) |
| `resident.name` | Display identity (e.g. `"Muninn"`); defaults to the persona |
| `resident.persona` | Ravn persona the resident runs (required) |
| `resident.routeId` | Path segment for the gateway HTTPRoute (`/s/<routeId>`) and the session id; unique per gateway, defaults to `<namespace>-<release>` |
| `resident.maxConcurrentTasks`, `resident.dailyBudgetUsd`, `resident.llm` | Runtime limits and LLM config |
| `resident.platform.enabled` / `resident.platform.baseUrl` | Platform access: Volundr gateway tools via workload identity |
| `session.ownerId` | Owning user id (IDP sub), rendered into the broker config — Skuld enforces WebSocket ownership against it |
| `mimir.instances` | Mímir memory instances handed to the resident's ravn daemon |
| `persistence.emptyDir: true` | Pod-local workspace; residents don't need the shared sessions PVC |

A live example ships in the infrastructure repo under
`valhalla/resident-ravn/` — copy that as the starting point for a new
resident.

### Platform access (workload identity)

When `resident.platform.enabled` is set, the pod gets a projected
ServiceAccount token (`resident.workloadIdentity.audience`, default
`volundr-api`) and exchanges it at `POST /api/v1/tokens/workload/exchange`
for a short-lived Niuu JWT. Map the resident's ServiceAccount subject to its
owning user in Volundr's `workload_identity.mappings` (subject →
`owner_id`/`tenant_id`/roles) or the exchange rejects it.

## Discover and chat

Kubernetes discovery remains the compatibility path for residents not yet
represented by a durable Volundr record. Residents advertise themselves via
pod labels set by resident mode: `niuu.world/kind: resident` and
`niuu.world/persona`, plus the `niuu.world/resident-name` annotation. The Ravn
API finds them through its `resident_discovery` config (e.g. the Kubernetes
adapter, which watches for `niuu.world/kind=resident`); no adapters are
configured by default — discovery is an explicit deployment decision:

```yaml
resident_discovery:
  adapters:
    - adapter: ravn.adapters.resident_discovery.kubernetes.KubernetesResidentDiscoveryAdapter
      namespace: volundr
```

Compatibility deployments may also carry:

| Annotation | Meaning |
|---|---|
| `niuu.world/resident-id` | Stable resident id; defaults to the Deployment name |
| `niuu.world/visibility` | `system`, `tenant`, or `user`; defaults to `system` for existing deployments |
| `niuu.world/owner-id` | Required owner for `user` visibility |
| `niuu.world/tenant-id` | Required tenant for `tenant` visibility and recommended for user residents |

Invalid visibility values are rejected from discovery. User-scoped residents
are visible only to their owner or an admin in the same tenant; tenant-scoped
residents are visible only in that tenant. New managed deployments use durable
UUIDs and explicit ownership rather than relying on these compatibility IDs.

`GET /api/v1/ravn/ravens` returns managed and visible compatibility residents; the fleet UI
renders them with a **Chat** tab wired to the broker's Skuld room via the
gateway HTTPRoute (`/s/<routeId>`). Plain messages route to the resident
automatically (`room.default_target_peer_id`), so any chat client works.
Live managed and compatibility residents also appear in
`GET /api/v1/ravn/sessions` alongside `ravn_flock` workflow sessions.

Nothing auto-stops a resident — it lives until you scale down or delete the
release.

### The shared (system) resident list

`GET /api/v1/ravn/ravens?visibility=system` returns only the discovered and
catalog residents whose visibility is `system`. It never asks Forge for the
caller's own resident runtimes, so it suits views that one identity builds and
every signed-in user sees. The Observatory topology uses it: each site's
Observatory has its own site identity, which owns no residents and, on a Forge
with `membership_authority: local`, is no tenant member either. A user's own
residents therefore do not appear on the Observatory map; they stay visible to
their owner in Lexi and the Residents page.

## Ownership

Skuld enforces session ownership at WebSocket accept (`ws_auth` in the
broker config): browser and ravn connections must present an identity
matching `session.ownerId` (Envoy headers, dev params, or bearer token;
admin roles bypass; unauthenticated loopback stays open for in-pod peers).
The resident authenticates with its platform identity, so it can join
exactly the sessions its owner owns — nothing else.

The resident's own room connection (`skuld.broker_url`) presents that same
platform identity as a bearer token, so a room hosted remotely behind JWT
(`wss://yggdrasil.niuu.world/rooms/<id>/ws/ravn`) accepts it. A `wss://`
`skuld.broker_url` therefore requires `gateway.platform.enabled` (with
`gateway.platform.access_token`, or workload identity); without it the
resident refuses to start. A plain in-cluster `ws://` broker needs no
identity.

## The loop back

Two mechanisms resume the resident when launched work lands:

- **Observation relay** (`observation_relay` in the broker config): platform
  events matching the resident's own `consumes_event_types` declaration become
  neutral evidence in a directed turn plus a `room_notification` in chat
  history. The relay does not prescribe a conclusion or action.
- **Session join** (`session_join` tool): the resident joins the room of a
  session it launched (`{"action": "join", "session_id": ..., "chat_endpoint": ...}`),
  appears in that session's participant list, receives messages directed
  at it there (tagged perception, never confused with your chat), can
  `post` answers into that room, and leaves when the work completes.

## Driving pipelines from chat

The `product-steward` persona ships with the full chain:

| Tool | Purpose |
|---|---|
| `ting_workflow` | Launch Research Campaign / Specification Stack (supports `provenance` and `gate_auto_forward_after`; pass `""` to make gates wait for you — encoded as a very long duration so the downstream default never re-enables auto-forward) |
| `ting_spec` | Follow spec campaigns; approve / request changes on PRD/SRD/SDD gates |
| `ting_plan` | Spawn planning from an approved spec, read the draft breakdown, approve gates |
| `ting_saga` | `commit` the approved breakdown into tracker tickets; `dispatch` runs |
| `mimir` tools | Curate `projects/<slug>/` initiative pages between stages |
