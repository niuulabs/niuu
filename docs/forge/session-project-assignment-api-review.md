# Organising existing sessions into projects

Reviewed September 19, 2026 on `forge/ux-improvement` at `de5e912e` and against
the five live Guild hosts. This is an API review and proposed contract; the new
relationship endpoints described below are not implemented or deployed.

## What creates a project today

A Forge project is a durable, owner/tenant-scoped registration of a Git repository
and optionally its checkout on a particular host. It has a stable UUID, name,
description, active/archive state and revision. It can supply shared instructions
and receive durable handoff receipts. It exists independently of any coordinator;
several coordinator sessions can belong to the same project.

The existing APIs, under `/api/v1/forge`, are:

| Operation | API | Behaviour |
| --- | --- | --- |
| Inspect an existing checkout | `POST /projects/discover` | Validates the folder, remote and context without registering or editing files. |
| Connect that checkout | `POST /projects/connect` | Discovers and registers it; accepts `workspace_path` and an optional name. The facade accepts a selected Guild `instance_id`. |
| Register known project metadata | `POST /projects` | Requires an explicit stable project UUID and repository URL. A host can register a replica without a checkout for browsing. |
| Read/edit a project | `GET/PATCH /projects/{project_id}` | Editing uses the expected project revision; identity stays immutable. |
| Launch into a project | `POST /sessions` | Accepts `coordination` and requires a stable `dispatch_id` for project launches. |
| Find related sessions | `GET /sessions` | Supports project, role, parent session and parent instance filters. |

Checkout discovery uses `project.json` when present; otherwise it derives a stable
UUID from the canonical Git remote. Connecting does not create a Git repository,
clone one, move files, create a coordinator or start a session. A project's context
repository may describe work across several code repositories; membership does not
mean the session must execute in that repository's folder.

For example, connecting an existing host checkout uses:

```http
POST /api/v1/forge/projects/connect
Content-Type: application/json

{
  "instance_id": "56499f00-02b3-4d80-8137-09d6d3238056",
  "workspace_path": "/home/thor/projects/lexi",
  "name": "Lexi"
}
```

This illustrates the existing API; no registration write was performed during
this review. Reconnecting an existing registration does not rename it.

## The missing operation

`SessionCoordination` already stores `project_id`, `role`, `parent`, `objective`,
`context_revision` and `labels`. A parent is an instance/session pair. A session
can have one project and one parent; it can belong to a project without a parent.
Roles are metadata, not permission grants or an automatic scheduling policy.

These fields are accepted at launch, persisted in `sessions.coordination`, and
returned to clients. There is no supported API to attach, move, detach, reparent
or change a session's project role after launch. The ordinary session PUT accepts
only name, model, branch and tracker issue. An isolated validation check confirmed
that adding `coordination` to that request is silently discarded by the current
request model. A successful ordinary update is not evidence that membership changed.

The web port currently reads projects and relationships for grouping/tree display;
it has no project-assignment mutation. This needs a server contract usable by both
the web and native clients, not browser-local grouping preferences or a database
edit exposed through the UI.

## Live findings

Read-only GETs inspected direct host feature flags/OpenAPI, Guild-scoped project
and session lists, and current parent references. No sessions were modified or
messaged.

| Host | Direct project identity | Registered projects observed | Relationship edit API |
| --- | --- | --- | --- |
| Thor | `thor` | Physics, Lexi | Absent |
| Build | No Projects capability advertised | Projects API absent | Absent |
| Build Bro | `100.115.8.110` | Kit | Absent |
| Build-Kit | `100.90.20.64` | None | Absent |
| Spark | `spark` | Lexi, with the same UUID as Thor's Lexi | Absent |

Thor's facade currently ignores `instance_id` on `/feature-flags` and selects its
default host. Thus it reported Thor's Projects support and identity even for
Build, where Projects routes are absent. Selected project reads from Build returned
503 through the facade. Capability discovery must become host-specific before an
editor can reliably decide whether a selected host supports an operation.

Guild's registry UUIDs are also distinct from durable project-reference instance
IDs. Existing links use `thor`, `spark`, and Build Bro's IP. The facade replaces
top-level session `instance_id` with the receiving registry's UUID but leaves the
stored parent reference intact. A new editor must resolve both forms explicitly;
a display name or a foreign registry UUID is not a portable parent identity.

Current server validation checks local parents for access and project membership,
but accepts foreign-host references as links without resolving them. That is not
sufficient validation for a user-facing relationship editor. The web tree also
uses a session-ID-only match when there is a single candidate, which should be
replaced with resolved instance/session identity when adding the editor.

## Proposed API

Reuse `coordination`; add a dedicated resource with explicit replacement and
concurrency semantics:

```http
GET /api/v1/forge/sessions/{session_id}/coordination?instance_id={owning_guild_id}
PUT /api/v1/forge/sessions/{session_id}/coordination?instance_id={owning_guild_id}
```

GET returns the canonical session reference, coordination object or null, and a
`coordination_revision` (zero for an unassigned legacy session). PUT requires the
revision and the desired complete writable relationship. The following proposed
body assigns an existing session to Lexi under the existing Thor coordinator:

```json
{
  "expected_revision": 0,
  "coordination": {
    "project_id": "4b011f7f-87fb-4f4f-a53d-5eb4025c510f",
    "role": "worker",
    "parent": {
      "instance_id": "thor",
      "session_id": "8f20102d-6da7-58aa-98c2-e0bce2deab97"
    },
    "objective": "",
    "labels": []
  }
}
```

The session stays on its current host, with the same ID, runtime, workspace and
conversation. The registry selector routes the write to that session's owner;
the parent uses a resolved canonical project-reference identity.

| User action | Desired relationship |
| --- | --- |
| Add a free-floating session to a project | Set project and role; parent may remain null. |
| Attach/change coordinator | Keep project, replace parent; preserve the session's objective and labels. |
| Move to another project | Replace project and explicitly choose a compatible parent or null. |
| Remove coordinator only | Keep project and role, set parent to null. |
| Make free-floating again | Set the entire coordination object to null. |
| Promote to coordinator | Set role to coordinator; the UI exposes parent selection separately. |

PUT returns the stored relationship and incremented revision. Stale revisions
return 409 with the current version; clients reload and reconcile rather than
blindly resubmitting. After a lost response, GET establishes whether the requested
state was stored. Unknown request fields must be rejected. Advertise relationship
editing separately from Projects v1, per host; older hosts must not appear to save
an edit they ignore.

## Required semantics and implementation boundaries

- Check session update permission, project access/status, and parent access. Resolve
  foreign parents through their authenticated host; verify same-project membership.
  Never infer a parent from its name or accept an unresolved host as validated.
- Keep a project registration with the same stable identity on the session's
  owning host. Existing metadata-only registration can support organisation without
  requiring a checkout. Preparing a checkout is a separate prerequisite for context
  loading or project launches, not for changing the sidebar grouping.
- Reject self-parenting and cycles. Changing project or detaching a coordinator
  with dependent sessions must return a conflict and describe the affected links;
  moving an entire subtree should be a separate, explicit operation. Do not
  silently move its workers. An incomplete/offline dependency inventory cannot be
  treated as proof that it has no children.
- Cross-host cycle and dependent-child guarantees require a project-scoped
  authoritative relationship index or an equivalent serialized protocol. A local
  revision check plus remote GETs cannot prevent simultaneous conflicting graph
  edits. This is a backend design gate before claiming arbitrary cross-host tree
  reorganisation; do not hide this limitation behind a UI-only ancestor check.
- Add a dedicated repository operation that updates relationship fields with
  compare-and-swap and records old/new values and actor in one transaction. Ordinary
  lifecycle writes must preserve those fields; the current whole-session update
  also writes coordination and could overwrite a concurrent assignment. A separate
  relationship revision needs migrations in both repository migration locations.
- Publish a normal session update after persistence so existing polling/SSE and
  shared client stores refresh the tree. The write must not start/stop a runtime,
  alter Git state, change the working folder, rewrite replay or send a model message.
- Preserve historical receipts and dispatch identities under their original
  project. Moving a session must not rewrite earlier handoffs, context provenance
  or idempotent launch records. New receipts validate the current membership.

## Project instructions are a separate action

Launch currently snapshots project context into
`workload_config.project_context` and records its revision. Changing metadata does
not change instructions already delivered to a running Claude/Codex session.

The relationship API should report that distinction. It must not label a newly
attached session as having consumed project context or overwrite the running
system prompt. Moving/detaching must invalidate any saved project launch brief
that would otherwise reinject the former project on a subsequent start; changes
to role/objective need the same stale-brief handling. Preserve historical context
provenance rather than relabelling it as the destination project's revision.

An explicit **Apply project context / send handoff** action can later fetch the
bounded context and use the existing durable message-delivery API. Organisation
alone should not wake a coordinator or add an unsolicited message to a worker.

## UI and implementation order

Add **Organise session…** to both the session-row actions and title bar. The compact
editor has Project (including No project), Coordinator (including None, labelled
with host), and optional Role. Preserve existing objective/labels unless edited.
Selecting a coordinator suggests its project and makes any project move explicit.
Selecting a free-floating coordinator first requires assigning it a project under
the current data contract. Save persists through the API, then updates the project
tree immediately; host/capability/network errors keep the editor open with retry.

There is no name-only, repository-free project creation contract today. That could
be a separate extension for lightweight organisational projects; it is not needed
to support reassignment among the existing Lexi, Physics and Kit projects.

Implementation checklist:

- [ ] Fix selected-host feature discovery and expose canonical relationship identities.
- [ ] Settle cross-host graph authority/serialization before mutable parent links ship.
- [ ] Add relationship read/write service, owner routing, authorization, revision,
  persistence, audit and stale-context handling.
- [ ] Cover attach/move/detach, same/cross-host parents, stale writes, cycle races,
  dependent coordinators, offline hosts, lifecycle-write races and retained servers.
- [ ] Verify session/runtime/transcript preservation and correct updates in two clients.
- [ ] Add web service methods and the title/sidebar editor, using the existing tree.
- [ ] Add the same contract to ForgeKit for iOS/macOS; verify cross-client changes.
- [ ] Keep explicit context delivery and bulk subtree movement as separate features.

## Source references

- [Project models](../../src/volundr/domain/projects.py)
- [Project registration, dispatch and validation](../../src/volundr/domain/services/projects.py)
- [Project REST API](../../src/volundr/adapters/inbound/rest_projects.py)
- [Session request/update API](../../src/volundr/adapters/inbound/rest.py)
- [Session persistence](../../src/volundr/adapters/outbound/postgres.py)
- [Guild facade routing](../../src/niuu/adapters/inbound/rest_volundr.py)
- [Checkout discovery](project-checkout-discovery.md)
- [Web project tree](../../web-next/packages/plugin-volundr/src/domain/projectTree.ts)
