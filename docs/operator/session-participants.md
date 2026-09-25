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
path has not been independently traced.

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
carried as a verified `x-niuu-room-role` header from the session proxy (mini
mode) or trusted-loopback/`enforce_ownership` signal (Kubernetes) into
Skuld's broker. See `src/niuu/room_access.py` for the exact HTTP allowlist
and `src/skuld/broker.py`'s `_message_role_requirement` for the WebSocket
message-type allowlist.
