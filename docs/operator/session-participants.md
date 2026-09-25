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

## Kubernetes limitation: participants need a `process`-backend session

**Today, participant grants only take effect on sessions whose pods run on
the `process` runtime backend (mini mode / local dev).** On a Kubernetes
deployment, a session's WebSocket and HTTP traffic reaches its pod through
the shared Gateway (`charts/skuld/templates/httproute.yaml` +
`securitypolicy.yaml`), and that Gateway's `ext_authz` check authorizes only
the Cedar `start` action — owner/admin, by design. No `session_participants`
grant is ever authorized for `start`; Cedar simply doesn't offer a viewer or
approver a path through it. A participant invited to a Kubernetes-backed
session would never be able to attach, no matter how the grant is
configured.

Rather than let an invite succeed and the room show up in the invitee's
session listing while every attach attempt silently 403s against the
Gateway, the invite endpoint refuses up front:

```
POST /api/v1/forge/sessions/{id}/participants
409 Conflict
"This deployment's session pods authorize attach by ownership only
(Kubernetes Gateway ext_authz), so a participant grant could never be used
to attach. Participant invites require a session backend whose pod
authorization consults session_participants grants (currently: process)."
```

**Remedy:** run the session you want to share on the `process` backend
(mini mode), or wait for pod-level (Gateway ext_authz) authorization to
learn to consult `session_participants` grants — tracked as follow-up work,
not yet implemented. There is no per-session override; this is a property
of the whole deployment's `pod_manager.runtime_backend` setting.

This has been verified specifically for the `kubernetes` backend. The
`openshell` and `vm` backends are refused defensively for the same reason
(both are pod-based and Gateway-routed by construction), but their attach
path has not been independently traced. The `docker` backend is refused for
the same reason and has not been independently traced either — it is not
routed through the session proxy today, so it stays on the `deployment`
room-role default (see below) like every other non-process backend.

Room-role resolution itself follows the same split, via Skuld's
`ws_auth.room_role_source` setting (`process` renders `proxy`; every other
backend keeps the default, `deployment`):

- `deployment` (Kubernetes, OpenShell, VM, docker): this pod's own auth
  boundary (ext_authz / enforce_ownership / the deployment's Gateway)
  already gates every caller who reaches the pod at all, so a caller
  reaching it is owner — identical to this pod's behavior before
  `session_participants` existed. This is intentionally **not** derived
  from loopback/`x-forwarded-for` heuristics: every Skuld pod's nginx
  sidecar sets `x-forwarded-for` on every request it proxies
  (`charts/skuld/templates/nginx-configmap.yaml`), so such a heuristic could
  never distinguish the genuine owner from anyone else on these backends.
- `proxy` (process backend only): the session proxy resolves the role from
  `session_participants` grants and stamps it; trusted directly.

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
`read_room` — never a hand-written owner_id/admin-role comparison), and
carried as a verified `x-niuu-room-role` header from the session proxy on
the `process` backend, or governed by `ws_auth.room_role_source`'s
`deployment` default everywhere else (see above) into Skuld's broker. Both
the HTTP middleware and every role-gated HTTP route call the SAME
`skuld.broker_api._effective_room_role`, and the WebSocket leg's
`skuld.websocket_lifecycle._resolve_room_role` mirrors it exactly, so a
caller can never get a different room role on the two legs of the same
session. See `src/niuu/room_access.py` for the exact HTTP allowlist and
`src/skuld/broker.py`'s `_message_role_requirement` for the WebSocket
message-type allowlist.
