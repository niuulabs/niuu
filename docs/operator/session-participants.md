# Session participants (shared agent rooms)

A Forge session's owner can invite other same-tenant users into the session's
room as durable participants, with one of two roles:

- **viewer** — can read the transcript, tool results, capabilities, workflow
  gates, and presented files, and can send room chat, but cannot change
  session state, resolve gates, or answer an operator's pending question.
- **approver** — everything a viewer can, plus resolving workflow gates and
  answering `ask_user_answer`/`permission_response` operator waits.

The owner always keeps full control regardless of any grant. Invite, accept,
and revoke are exposed under `/api/v1/forge/sessions/{id}/participants`; see
the OpenAPI schema for the exact request/response shapes.

## Kubernetes: participants need a `process`-backend session, or `room_role_source: remote`

**By default, participant grants only take effect on sessions whose pods run
on the `process` runtime backend (mini mode / local dev).** On a Kubernetes
deployment, a session's WebSocket and HTTP traffic reaches its pod through
the shared Gateway (`charts/skuld/templates/httproute.yaml` +
`securitypolicy.yaml`), which forwards straight to the session pod's own
Envoy sidecar. When that sidecar's `ext_authz` check is enabled
(`wsAuth.enforce_ownership`), it authorizes only the Cedar `start` action —
owner/admin, by design. Once a caller has reached the pod, its room-role
resolution (`ws_auth.room_role_source`, below) previously had no way to
consult `session_participants` grants either, so a viewer or approver
invited to a Kubernetes-backed session could never exercise the grant.

Rather than let an invite succeed and the room show up in the invitee's
session listing while every attach attempt silently 403s or the room role
never resolves below owner, the invite endpoint refuses up front unless the
deployment has opted in:

```
POST /api/v1/forge/sessions/{id}/participants
409 Conflict
"This deployment's session pods authorize attach by ownership only
(Kubernetes Gateway ext_authz), so a participant grant could never be used
to attach. Participant invites require a session backend whose pod
authorization consults session_participants grants (currently: process), or
set pod_manager.room_role_source: remote (volundr/config.py's
PodManagerConfig) to deploy this backend's session pods with
ws_auth.room_role_source: remote."
```

**Remedy — two options:**

1. Run the session you want to share on the `process` backend (mini mode).
2. Set Forge's `pod_manager.room_role_source: remote` (default:
   `deployment`, unchanged), **and** deploy Kubernetes session pods with
   `wsAuth.enforce_ownership: false`. This is a property of the **whole
   deployment**, not a per-session override — flipping it changes every
   future session pod's trust boundary, so verify it on a non-production
   cluster first. With it set, Forge:
   - lifts the 409 for `kubernetes` session invites, and
   - renders `wsAuth.room_role_source: remote` plus a
     `wsAuth.room_role_remote` dynamic adapter
     (`skuld.room_role_remote.RemoteAuthorizationAdapter`) into each new
     session pod's Helm values (`RoomRoleSourceContributor`), so the pod
     asks Forge for the caller's grant on every request instead of trusting
     ownership alone.

   **`enforce_ownership` and `remote` are mutually exclusive, enforced at
   render time.** The pod-local ext_authz sidecar (`enforce_ownership: true`
   — this is what ymir's production `values-cedar.yaml` sets) authorizes
   only Cedar's owner/admin-only `start` action, and it runs BEFORE a
   remote room-role lookup ever would: a participant would still 403 at the
   sidecar no matter what `room_role_source` says. The Helm chart's
   `skuld-configmap.yaml` `fail`s the render if both are set together, and
   `WsAuthConfig`'s own validator fails pod startup the same way as a second
   line of defense. On a deployment that runs `enforce_ownership: true`
   (like ymir today), `remote` mode is therefore **not currently usable** —
   moving the ext_authz gate itself from `start` to an `attach` decision
   backed by a remote grant lookup is tracked as follow-up work, not done
   here.

   The adapter authenticates with the pod's own projected workload-identity
   token (the same `niuu-workload` service-account-token exchange used for
   chronicle/event-log calls), requesting the `forge:session:room-role`
   scope so a leaked token minted for this purpose cannot be replayed
   against any other Forge endpoint. It calls
   `GET /api/v1/forge/sessions/{id}/participants/role` and caches the
   answer for `pod_manager.room_role_cache_ttl_seconds` (default 5s) — short
   enough that a revoked grant stops working within a few seconds on a
   fresh request, and an already-open browser WebSocket additionally
   re-checks its own role on `ws_auth.room_role_revalidate_interval_seconds`
   (default 5s) and closes on revoke or demotion, mirroring the same
   revalidation loop mini mode's session proxy already runs
   (`niuu.session_proxy._revalidate_loop`). **Fails closed:** an unreachable
   Forge, a timeout, or a malformed response denies the caller outright (the
   connection or request is refused, and an already-open connection is
   *kept* open only on a transient failure — an explicit "no longer
   entitled" answer still closes it) — it never falls back to owner-only or
   allow-all.

   The role endpoint itself (`GET .../participants/role`) is registered
   ONLY when `room_role_source: remote` is configured — it never goes live
   on a deployment that hasn't opted in — and is gated more strictly than
   every other route on this router: the caller's bearer token must be a
   scoped workload credential (`token_use=valkyrie_build`) carrying the
   `forge:session:room-role` scope, AND its `workload_sub` claim must name
   the specific session being asked about (session pods use the
   `openbao-session-{id}` service account — see
   `OpenBaoAgentInjectionAdapter`), so a token minted for session A's pod
   can never be replayed to probe session B's participants.

**Limited to the `kubernetes` backend only.** `openshell` and `vm` are
refused unconditionally regardless of `room_role_source`, because neither
currently mounts a projected `niuu-workload` service-account token
(`WorkloadIdentityContributor` skips them — OpenShell owns its own workload
identity, and VM sessions have no Kubernetes token issuer at all), so
`RemoteAuthorizationAdapter` would have no credential to exchange there. The
`docker` backend is refused for the same underlying reason (not routed
through the session proxy, no remote-adapter path).

**Status: implemented and covered by unit and Helm-render tests, not yet
verified against a live cluster.** Do not describe `remote` mode as
"verified" on Kubernetes until an actual invite → attach → revoke round
trip has been exercised against a running deployment with
`enforce_ownership: false`.

Room-role resolution follows Skuld's `ws_auth.room_role_source` setting
(`process` renders `proxy`; every other backend keeps the default,
`deployment`, unless the deployment opted into `remote`):

- `deployment` (the default for Kubernetes, OpenShell, VM, docker): this
  pod's own auth boundary (ext_authz / enforce_ownership / the deployment's
  Gateway) already gates every caller who reaches the pod at all, so a
  caller reaching it is owner — identical to this pod's behavior before
  `session_participants` existed. This is intentionally **not** derived
  from loopback/`x-forwarded-for` heuristics: every Skuld pod's nginx
  sidecar sets `x-forwarded-for` on every request it proxies
  (`charts/skuld/templates/nginx-configmap.yaml`), so such a heuristic could
  never distinguish the genuine owner from anyone else on these backends.
- `proxy` (process backend only): the session proxy resolves the role from
  `session_participants` grants and stamps it; trusted directly.
- `remote` (`kubernetes` only, opted in via `pod_manager.room_role_source:
  remote`, and requiring `wsAuth.enforce_ownership: false`):
  `RemoteAuthorizationAdapter` asks Forge for the caller's grant on every
  request (subject to its cache), and denies outright — never a default
  role — when it cannot get an answer. A genuine loopback caller presenting
  no verified identity headers and no `x-forwarded-for` still gets the
  same same-pod-tooling exception `proxy` mode grants (`containers/skuld/
  svc`, hooks, present-file, in-pod Ravn/Ting clients).

## Same-OS-user risk on the `process` backend

On the `process` backend, every session's agent runs as the **same OS
user** as every other session on that host. A loopback caller with no
`x-forwarded-for` header is treated as owner (the same-pod-tooling
exception `ws_auth.room_role_source="proxy"` needs for `containers/skuld/
svc`, hooks, and present-file). Any other process co-located on that host
— including another session's own agent, if it can reach the loopback
broker port directly — inherits that same trust. Prefer viewer-only
invitees for participants you do not fully trust on a shared host, and do
not rely on room-role gating alone as an isolation boundary between
co-located sessions on this backend.

## Observer risk when permission mode is permissive

A viewer or approver shares the session's live agent, not a sandboxed copy
of it. If the session's permission mode allows the agent to act without
per-tool confirmation (e.g. `bypassPermissions` or an auto-approving mode),
anything a participant asks the agent to do executes with the **owner's own
credentials** — repository access, deployed secrets, whatever the agent's
tools reach. Room-role gating restricts which *browser-originated messages*
a participant may send (see the allowlist below); it does not sandbox what
the agent itself can already do once asked. Invite participants only to
sessions whose permission mode you would be comfortable letting them drive
directly, or set the session to a confirming permission mode before
inviting an observer you do not fully trust.

## Enforcement summary

Room-role is resolved once, from Cedar (`admit` / `resolve_gate` /
`read_room` — never a hand-written owner_id/admin-role comparison,
now shared as `SessionParticipantService.effective_room_role`), and
carried as a verified `x-niuu-room-role` header from the session proxy on
the `process` backend, or governed by `ws_auth.room_role_source`'s
`deployment` default (or, when opted in, `remote`) everywhere else (see
above) into Skuld's broker. Both the HTTP middleware and every role-gated
HTTP route call the SAME `skuld.broker_api._effective_room_role`, and the
WebSocket leg's `skuld.websocket_lifecycle._resolve_room_role` mirrors it
exactly, so a caller can never get a different room role on the two legs of
the same session. `remote` mode returns `None` (a real "no grant" answer,
never a default role) or raises `skuld.room_role_port
.RoomRoleResolutionError` (Forge unreachable) — both cases deny the caller
outright rather than falling through to any lower-privilege default. See
`src/niuu/room_access.py` for the exact HTTP allowlist and
`src/skuld/broker.py`'s `_message_role_requirement` for the WebSocket
message-type allowlist.
