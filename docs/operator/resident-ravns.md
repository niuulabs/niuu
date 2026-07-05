# Resident ravns

A **resident** is a long-lived, named ravn with a chat room — same format as
a Valkyrie, but conversational. You join and leave its chat; it keeps
working, remembers via Mimir, launches platform work (research campaigns,
spec stacks, planning), and reports back into the room when results land.

## Deploy

Residents are **infrastructure, not Forge sessions**: each one is a
deployment of the skuld chart (`charts/skuld`) in resident mode, declared in
gitops. The pod is a Skuld broker in room mode plus one ravn daemon sidecar —
a flock of one. Nothing creates residents through the Forge sessions API.

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

Residents advertise themselves via pod labels the chart sets when resident
mode is on: `niuu.world/kind: resident` and `niuu.world/persona`, plus the
`niuu.world/resident-name` annotation. The Ravn API finds them through its
`resident_discovery` config (e.g. the Kubernetes adapter, which watches for
`niuu.world/kind=resident`); no adapters are configured by default —
discovery is an explicit deployment decision:

```yaml
resident_discovery:
  adapters:
    - adapter: ravn.adapters.resident_discovery.kubernetes.KubernetesResidentDiscoveryAdapter
      namespace: volundr
```

`GET /api/v1/ravn/ravens` returns the discovered residents; the fleet UI
renders them with a **Chat** tab wired to the broker's Skuld room via the
gateway HTTPRoute (`/s/<routeId>`). Plain messages route to the resident
automatically (`room.default_target_peer_id`), so any chat client works.
Discovered residents also appear in `GET /api/v1/ravn/sessions` alongside
live `ravn_flock` workflow sessions.

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
