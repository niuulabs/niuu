# Forge MCP, injected skills, and durable notifications

Status: proposed design, 2026-09-22. This document does not claim that the proposed
MCP server, notification API, skill injection or automatic runtime refresh exists.
The accompanying change implements runtime version inspection only.

## Decision

Give each Forge-launched session a small, authenticated Forge MCP connection. Keep
GBrain as a separately configured MCP resource, shared by sessions. Resolve the
Guild, project and launch-spec skill selections once at launch, then materialize a
versioned, session-private bundle. Both the connection and skill bundle become
visible launch metadata. Existing running harnesses are not silently reconfigured.

Forge owns lifecycle and authorization. Skuld adapts the tools, native harness and
human channels. Shared notification contracts/storage mechanics belong in Niuu;
Ravn retains semantic decisions about when to report a milestone or ask for help.
Ordinary coding sessions do not acquire a resident or collaboration room just to
publish one notification.

## What is already present

The live Lexi agent-service source at
`lexi-frontend/services/lexi-agent-service/tools/forge/index.ts` exposes ten wrappers:
health, stats, list/create/get sessions, session events, start/stop/restore, and
send message. This is a useful tool surface, not a standalone Forge MCP server.
Its send wrapper still uses WebSocket steering. Forge now also has REST message
submission and request-ID delivery tracking; the new adapter should use those.

Niuu has MCP transport/manager code under `src/ravn/adapters/mcp/` and a real Mimir
MCP server in `src/mimir/mcp.py`. Reuse protocol infrastructure without importing
Ravn's cognition into Forge. `SessionMCPContributor` currently injects resource MCP
servers, including Mimir. Native Claude/Codex adapters consume MCP configuration;
Pi needs an explicit adapter/capability check before claiming equivalent support.

`LaunchSpec.skills` and rules can be stored, but
`src/volundr/adapters/outbound/contributors/launch_spec.py` currently applies env,
MCP servers, system prompt and workload config, not those skill selections. A saved
skills field therefore does not yet prove that a launched agent received a skill.
System instructions already travel through runtime flags/config; do not overwrite
the user's `AGENTS.md` or `CLAUDE.md` to implement this feature.

The live Lexi brain client (`tools/brain/client.ts`) owns one long-lived
`gbrain serve` process against its local brain. Its comments document a single-writer
lock and OAuth for HTTP. Do not launch one database-owning process per Forge
session or point every node at a copied writable brain directory. Validate the
installed GBrain HTTP service/auth configuration before exposing a network endpoint.

## Proposed tool contract

Names below are proposed, not existing endpoints. Keep the first release small.

| MCP tool | Responsibility and authorization |
| --- | --- |
| `forge_environment` | Current node, authenticated session, workspace, project, permitted nodes, runtime build, attached MCPs and skill bundle versions; no secrets. |
| `forge_list_sessions`, `forge_get_session` | Bounded, authorized metadata; qualify every session by node ID and session ID. No recursive Guild fan-out. |
| `forge_create_session` | Create on an explicitly permitted node with workspace, harness/model and initial instruction; return launch state, not a claim of completion. |
| `forge_start_session`, `forge_stop_session` | Existing lifecycle services with explicit mutation permission; do not expose delete in the initial tool set. |
| `forge_send_message` | Existing REST message delivery ledger, stable request ID, submit/queue/steer mode supported by that harness, plus delivery-status lookup. |
| `forge_notify` | Persist a typed event for the authenticated current session; return canonical event ID and committed sequence. |
| `forge_list_notifications` | Bounded cursor feed for authorized sessions/projects/nodes. |

Default access is current session plus permitted project reads/notifications.
Launching/stopping/steering peers requires an operator-configured grant. A process
must not mint its own claimed sender or inherit the operator's unrestricted PAT.
Reuse Niuu workload identity/credential services, audience validation, expiry,
revocation and audit. Guild routing uses registered node identities and configured
credentials; advertising a node does not authorize access to its sessions.
Distinguish the caller's registry entry ID from the owning node's stable identity:
resolve the former locally, strip that routing selector before forwarding, and
stamp events with the authenticated owner's canonical node identity. A registry
UUID copied from Thor must not be interpreted as BuildBro's local registry UUID.

The desired agent-message envelope is `origin: agent`, authenticated sender node /
session, target node / session, request ID and correlation ID. Current user-role
steering is not authenticated A2A merely because its text says 'from an agent'.
Until the envelope is implemented end to end, surface the delivery honestly as
operator-authorized steering. A2A protocol expansion remains a separate discussion.
Retries with the same request ID must never cause a second native prompt. Return
accepted, pending, delivered or failed truthfully; an HTTP ACK is not completion.

## MCP transport and credentials

Use one Forge-owned Streamable HTTP endpoint, provisionally `/mcp/forge`, with an
optional stateless stdio adapter for harnesses that need it. The stdio process is a
client of the owning Forge, not a second Forge/database. Validate origins and use
scoped authentication. The standard defines these transports in the
[MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
For network authorization, integrate the existing identity service with the
[MCP authorization model](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
rather than introducing a new password/header scheme. Keep tokens in protected
credential/env channels, never the prompt, skill text or transcript.

GBrain is another configured server, with project-scoped search/get/list/remember
and write tools enabled intentionally. Store its endpoint and credential reference
once in the Guild/project resource configuration. Use a single service owner for
the brain storage; each session is an authenticated client. Do not proxy all ~89
brain operations into every agent by default. Verify GBrain's current authorization
and project isolation before making the network resource generally available.

## Skill injection

Resolve an immutable launch manifest from Guild defaults, project attachments and
explicit session selections. The operator controls allowed tools/resources; a
project skill may request capabilities but cannot grant them. Explicit session
choices may remove inherited optional skills. Reject missing required skills or
unsupported harness injection instead of silently dropping them.

Each manifest records skill ID, version/content digest, origin, runtime capability
requirements and resource bindings. Materialize outside the user's repository in a
session-owned directory. Mount/read this directory using each harness's supported
skill/config mechanism. Preserve its native discovery/precedence rules and test the
actual launch path; Claude, Codex and Pi must each prove discovery independently.
Expose attached skill versions in the environment tool and UI. Apply updates to
new launches or a later explicit safe refresh, not to loaded contexts.

The first bundled skill is `forge-notify`: explain when to report milestone,
decision, attention and reply-ready events, keep text brief, link artifacts, avoid
repeated progress spam, and never mark a whole task complete just because one turn
ended. Project skills compose with this bundle. Do not rely on parsing XML from
arbitrary assistant text: code fences and quoted examples would cause false events.
A structured tool/API call is the authoritative submission path for all harnesses.

## Durable notification contract and reuse

Proposed event fields: canonical event ID, owning node ID, authenticated session ID,
project reference, type, severity, title, short Markdown body, structured artifact
links, server timestamp, per-session event sequence, caller idempotency key and
optional correlation/source-event IDs. Types initially: milestone, decision,
attention, reply_ready, maintenance. Final reply is not synonymous with task done.

Write the notification and delivery-outbox entry transactionally before acknowledging
`forge_notify`. Retrying the same key returns the same event. Publish committed
events into the session timeline and Guild feed; a reconnect replays the same IDs
and timestamps. A notification during a Forge outage remains pending locally or
fails explicitly until it is committed; it must not disappear after a success ACK.

Reuse the neutral collaboration envelope in `src/niuu/collaboration/models.py`.
Ravn already projects HELP_NEEDED and OUTCOME in
`src/ravn/adapters/collaboration/projection.py`; DECISION/TASK_COMPLETE need explicit
mapping. Preserve source/correlation identifiers. Skuld's current
`_handle_notification` broadcasts `room_notification` directly, unlike the durable
`_emit_broker_frame` path. Bridge it through persistence; do not create a second
unrelated notification protocol or claim today's broadcast is a durable inbox.

Web `MeshEventCard` already renders notification cards. `useSkuldChat` currently
creates receipt-time/random IDs for these frames; replace those with canonical IDs
for replay/deduplication. Add a typed ForgeKit event and iOS card, preserving unknown
events for older clients. Both render the same data in conversation and a dedicated
feed, with source host/session/project links and expandable details.

Reuse the Skuld Telegram formatter/channel and Ravn projection; current launch
injection is limited to `ravn_flock`. Generalize its configuration for opted-in
Forge notifications, with durable outbox retries, deduplication and user
subscriptions. Do not send external notifications by default. Ting's subscription
concepts may inform the shared service, but Ting must not import Volundr.

Feed API proposal: POST under the authenticated session for submission; GET
session/project/node notification feeds with opaque cursor and bounded page size;
SSE after a committed cursor; reader-specific seen/read markers. Guild aggregation
keeps an independent cursor per node, deterministic ordering and explicit partial
node failures, so an offline node cannot block the feed. Preserve origin node IDs
and avoid facade recursion. Notification read state and session reply read state
remain distinct, even if one user action can update both.

## Maintenance and refresh

The proposed artifact-based fleet path is detailed in
[fleet-container-deployment-design.md](fleet-container-deployment-design.md).
It separates API replacement, per-session runtime adoption and database/engine
maintenance, with a node updater that remains available during API replacement.

A Forge API update can preserve live gateways only through the qualified local
release path in [local-api-release.md](local-api-release.md): isolated immutable
candidate and rollback environments, compatible migrations, process identity
snapshots and bounded readiness/replay checks. Systemd must stop only the API
process; Supervisor needs the equivalent verified process-group behavior. API
clients reconnect; no prompt is resent. Do not restart PostgreSQL or all services.

The new read-only `/sessions/{id}/runtime-version` compares the actual gateway health
identity with the owning Forge's available local-process source. It returns
current, different, unknown, unavailable or not_running. Failed probes do not mean
idle/stopped. It checks session access and the broker's reported session ID and
routes through the owning node. Other deployment backends report no candidate until
they can identify their actual deployable runtime image. The web title bar exposes
the comparison. This is visibility, not automatic migration or proof of ordering
between Git revisions. A missing registered chat endpoint currently returns
`not_running`; that describes Forge's endpoint registration, not proof that no OS
process remains. Historical terminal rows with live stray gateways are preserved
by the release inventory and require separate owner-led reconciliation. Never use
this endpoint alone as authorization to stop a process or automatically refresh.

Existing explicit stop/resume controls remain the manual route after a saved turn
boundary; stopped sessions use the selected source when next started. No existing
running or idle broker is refreshed by this rollout. Stale database lifecycle
states do not authorize killing a live process.

A future one-click refresh needs a per-session operation lock and fresh native
inactivity observation, no pending deliveries/approval/question, committed event
log checkpoint, saved native thread/session ID and a persisted restart intent.
Recheck those conditions atomically before stopping; new work cancels a queued
refresh. Restart without initial-prompt replay, verify the resumed native identity,
and record failure without guessing whether to resend. The current best-effort
shutdown flush is insufficient evidence for an automatic checkpoint contract.
An idle timeout alone is not a safe refresh window. Automatic 'update when idle'
remains off until these guarantees and harness-specific recovery tests exist.

## Implementation sequence and acceptance

1. Ship runtime visibility and qualified node API updates, preserving all brokers.
2. Add Forge MCP read/lifecycle adapters with workload grants and idempotent message
   delivery. Prove cross-node ownership and denied access, then inject into fresh
   Claude/Codex sessions; add Pi only after its adapter passes the same contract.
3. Wire skill manifests to real launch injection. Prove discovery, version reporting,
   no repository-file overwrite, and required-capability failure behavior.
4. Add notification migration/outbox/API plus Ravn projection bridge. Test duplicate
   submissions, offline retries, restart replay, cursor pagination and per-reader
   isolation. Update web and ForgeKit/iOS cards and Guild feed together.
5. Configure one GBrain network service/resource with supported auth and storage
   ownership; verify scoped reads/writes from each approved node.
6. Add opt-in delivery subscriptions and then guarded manual/queued runtime refresh.
   Test a racing new prompt, pending question, interrupted flush and failed resume.

No iOS release, Telegram send, GBrain deployment or peer-session steering is implied
by accepting this design document.
