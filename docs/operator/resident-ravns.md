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

Mini mode composes the same resident control port with
`LocalContainerResidentRuntimeController`. The adapter uses the Docker API to
run the configured Ravn, NemoClaw, or NemoHermes image without Kubernetes. The
Ravn deployment wizard therefore remains unchanged: select `Local Forge`, then
one of `ravn-local`, `nemoclaw-local`, or `nemohermes-local`.

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
resident containers call the shared host through `host.docker.internal`.

Each resident receives a private loopback-only host port and durable directories
under `~/.niuu/residents/<resident-id>/`: `workspace`, `.volundr`, `.codex`, and
`.claude`. Existing Codex and Claude credential files are copied with mode
`0600` only when the resident's durable destination does not exist. Restart and
suspend preserve these directories; deleting the resident removes its container,
durable directory, and engine machine credential unless retention is configured.

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
          wakefulness:
            enabled: true
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

Key chart values:

| Value | Meaning |
|---|---|
| `resident.enabled` | Deploy this release as a resident (broker room mode + ravn sidecar) |
| `resident.name` | Display identity (e.g. `"Muninn"`); defaults to the persona |
| `resident.persona` | Ravn persona the resident runs (required) |
| `resident.routeId` | Path segment for the gateway HTTPRoute (`/s/<routeId>`) and the session id; unique per gateway, defaults to `<namespace>-<release>` |
| `resident.maxConcurrentTasks`, `resident.dailyBudgetUsd`, `resident.llm` | Runtime limits and LLM config |
| `resident.wakefulness` | Ravn wakefulness trigger configuration passed through to the resident daemon |
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

## Ownership

Skuld enforces session ownership at WebSocket accept (`ws_auth` in the
broker config): browser and ravn connections must present an identity
matching `session.ownerId` (Envoy headers, dev params, or bearer token;
admin roles bypass; unauthenticated loopback stays open for in-pod peers).
The resident authenticates with its platform identity, so it can join
exactly the sessions its owner owns — nothing else.

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
