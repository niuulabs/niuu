# Resident ravns

A **resident** is a long-lived, named ravn with a chat room — same format as
a Valkyrie, but conversational. You join and leave its chat; it keeps
working, remembers via Mimir, launches platform work (research campaigns,
spec stacks, planning), and reports back into the room when results land.

## Deploy

Residents are **infrastructure, not Forge sessions**. Existing residents are
deployments of the skuld chart (`charts/skuld`) in resident mode, declared in
GitOps. The pod is a Skuld broker in room mode plus one ravn daemon sidecar —
a flock of one. Nothing creates residents through the Forge sessions API.

Volundr persists control-plane-managed residents in `resident_runtimes` with a
global UUID, owner, tenant, deployment profile, backend, engine, desired and
observed state, backend reference, endpoints, capabilities, and conditions.
Ravn reads these records through the authenticated target Volundr API. Guild
adds the owning `instance_id` when aggregating through Yggdrasil.

Available deployment profiles are operator configuration, not request input.
Configure `residentRuntimeProfiles` in the Volundr chart; disabled profiles and
backend-only `deployment` data are not returned by
`GET /api/v1/ravn/deployment-profiles`. No profiles are enabled by default.

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

## Ownership

Skuld enforces session ownership at WebSocket accept (`ws_auth` in the
broker config): browser and ravn connections must present an identity
matching `session.ownerId` (Envoy headers, dev params, or bearer token;
admin roles bypass; unauthenticated loopback stays open for in-pod peers).
The resident authenticates with its platform identity, so it can join
exactly the sessions its owner owns — nothing else.

## The loop back

Two mechanisms resume the chat when launched work lands:

- **Resident relay** (`resident_relay` in the broker config): platform
  events matching the resident's persona `consumes_event_types`
  declaration (research/spec/plan completions by default) become a
  directed turn plus a `room_notification` in the chat history.
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
