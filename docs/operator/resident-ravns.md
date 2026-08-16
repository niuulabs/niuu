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

## When a resident stops making progress

A resident that sleeps and rechecks is normal. A resident that reaches the
*same* conclusion on every recheck is stuck, and left alone it stays stuck:
it wakes, restates the verdict, sleeps, and never runs out of budget, because
each wake opens a fresh case and the per-case turn budget only ever sees one
turn. One resident spent 30 hours re-deciding "watch — waiting for research
campaign findings" about a campaign that had failed to launch and did not
exist. Nothing in the loop could discover that, because nothing in the loop
ever changed.

```yaml
resident_state:
  repeated_decision_escalate_after: 5   # 0 disables the guard
```

After this many consecutive turns the resident asks you instead of sleeping
again, and the case waits for your answer.

A turn counts toward the streak when it chose to **sleep** and none of these
changed since the previous turn:

- the decision (`watch`, `investigate`, …);
- the working state's `objectives`;
- what its **tools actually returned**.

The last one carries the weight. Everything the model narrates — rationale,
state summary, its own observations — is deliberately excluded, because a
stuck resident restates one belief differently every turn: across 55 real
stuck turns the rationale took 40 distinct forms and the `attempts` list grew
by one entry each time. A guard keyed on that narration matches nothing and
never fires. Tool results are the honest signal — re-reading one unchanged
fact returns byte-identical results, while watching a real condition returns
new numbers. Timestamps inside tool results are normalised away first: the
clock moving is not the world changing.

So the counter resets when a tool returns something new, when the verdict
changes, or when the objective moves — and does **not** reset merely because
the resident found new words for the same conclusion. Escalating also resets
it, so you get one question rather than one per turn.

**Choosing a value.** The streak counts turns, so the wall-clock delay is the
value times that resident's wake cadence — mostly
`resident_state.stewardship_interval_seconds`, plus whatever `wake_at` its
turns request. At the default 5, a resident on a 30-minute stewardship
interval asks after about 2.5 hours; one waking every 5 minutes asks after
about 25.

The default was calibrated by replaying two live residents' recorded turns:

| Resident | Turns | Span | Longest unchanged run | Escalations at 5 |
|---|---|---|---|---|
| Stuck on a campaign that did not exist | 55 | 1.3d | 8 | 2 (≈1.6/day) |
| Watching real, ongoing etcd latency | 657 | 8.4d | 4 | 0 |

The second resident sleeps on the same verdict for dozens of turns and must
never be escalated — it is doing its job, and each turn reads a new
measurement. Five sits in the gap between the two. Raise it for a resident
whose normal work involves genuinely long unchanged waits; set `0` only when
you would rather have silence than the question, and know that a stuck
resident will then stay stuck until you notice.

The streak is keyed on the resident, not the case, and is persisted at
`resident/continuation/decision-streak/<resident>.md`, so it survives both
case boundaries and restarts. Escalations log at WARNING
(`resident …: reached decision … times running without its tools returning
anything new`) and emit `ravn.resident.repeated_decision_escalated`; every
counted turn increments `ravn.resident.repeated_decisions`, which is the
metric to watch if you want to tune the threshold from real behaviour.

## The health scorecard

"Is this resident healthy?" has one answer surface instead of anecdotes. Every
resident maintains a scorecard of its durable state and re-states it as gauges
on every telemetry heartbeat:

| Gauge | Meaning |
|---|---|
| `ravn.resident.cases.live` | Durable cases something can still resume (pending wake or unanswered operator question) |
| `ravn.resident.cases.total` | All cases on disk, live and dead |
| `ravn.resident.scheduled_wakes.pending` | Wakes waiting to fire |
| `ravn.resident.inbox.pending` | Inbox signals not yet triaged |
| `ravn.resident.decision_streak` | Consecutive turns reaching the same conclusion (see above) |
| `ravn.resident.cron_refusals` (counter) | Cron creations refused by the backlog guard — a resident repeatedly hitting the cap or restating jobs is thrashing |

The same numbers ride the HUD payload (`GET /resident/hud-data`, under
`health`), so one unauthenticated read-only call answers the question without
Grafana. Existing gauges complete the picture: `ravn.queue.depth`,
`ravn.learned_tool.count` / `.installed`, `ravn.capabilities.available`, and
the `ravn.memory.corpus.*` family.

The counts behind the gauges are recomputed on a coarser cadence
(`resident_state.health_refresh_interval_seconds`, default 300) because they
walk the case store; the gauges themselves never age out between recounts. A
Mimir-backed resident state cannot count cases cheaply and omits the two case
gauges — absence there means "store cannot answer", not zero.

### Related: verify what a resident is waiting for

The loop above was possible partly because the resident could start a research
campaign but not check one. `ting_research` now supports `list`, `get` and
`artifacts` alongside `launch`, addressed by the **slug** the launch returns —
campaigns are not addressable by id, and searching Mimir for one is not a
substitute. A campaign that does not exist answers as an error saying so,
rather than as an empty result that reads like "running, no findings yet".

## Driving pipelines from chat

The `product-steward` persona ships with the full chain:

| Tool | Purpose |
|---|---|
| `ting_workflow` | Launch Research Campaign / Specification Stack (supports `provenance` and `gate_auto_forward_after`; pass `""` to make gates wait for you — encoded as a very long duration so the downstream default never re-enables auto-forward) |
| `ting_spec` | Follow spec campaigns; approve / request changes on PRD/SRD/SDD gates |
| `ting_plan` | Spawn planning from an approved spec, read the draft breakdown, approve gates |
| `ting_saga` | `commit` the approved breakdown into tracker tickets; `dispatch` runs |
| `mimir` tools | Curate `projects/<slug>/` initiative pages between stages |
