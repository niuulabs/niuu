# Assigning existing sessions to projects

Implemented on `forge/ux-improvement`, September 19, 2026. Deployment requires the
updated Guild facade and assignment support on each session-owning Forge host.

## What creates a project

A Forge project is an owner/tenant-scoped registration with a stable UUID,
repository URL, name, active/archive state and optional host checkout. It exists
independently of its coordinator sessions. Several hosts can register the same
project UUID; a session belongs to one project and keeps its own execution host.

Under `/api/v1/forge`:

| Operation | API |
| --- | --- |
| Inspect an existing checkout | `POST /projects/discover` |
| Register its project | `POST /projects/connect` |
| Register known metadata, without a checkout | `POST /projects` |
| Read/edit project metadata | `GET/PATCH /projects/{project_id}` |
| Launch a coordinator or worker in a project | `POST /sessions` with `coordination` and a stable `dispatch_id` |
| Find a project's sessions | `GET /sessions?project_id={project_id}` |

Discovery uses `project.json` when present; otherwise it derives the UUID from the
canonical Git remote. Connecting a checkout does not clone a repository, create a
coordinator or start a session. A project may coordinate work in several code
repositories, so membership does not require changing the session's workspace.

## Assign or move an existing session

The title bar and session-row actions now have **Assign or move to project**.
Select `Project · Host` and save. Healthy Guild hosts load independently, so an
unavailable node does not prevent using projects from the other nodes. Group by
Project reflects the persisted assignment while the row continues to show the
session's actual host.

The dedicated resource is:

```http
GET /api/v1/forge/sessions/{session_id}/project?instance_id={session_host}
PUT /api/v1/forge/sessions/{session_id}/project?instance_id={session_host}
```

GET returns:

```json
{
  "session_id": "00000000-0000-0000-0000-000000000001",
  "revision": 0,
  "coordination": null
}
```

The facade also includes the session-owning instance's ID and name. PUT accepts:

```json
{
  "project_id": "dd605b70-4d95-5f10-b698-e25a1b7b3bca",
  "project_instance_id": "be6dfc3b-3bdc-4e2e-805f-69e945038775",
  "expected_revision": 0
}
```

`instance_id` selects the session's owning node; `project_instance_id` selects the
node where the chosen project is registered. Both are Guild registry selectors,
not display names. For example, a Thor session can join Kit registered on Build
Bro. Omitting the project selector uses the session's node. A direct Forge host
accepts only `project_id` and `expected_revision`, after project registration.

The facade verifies session access, assignment support and revision before
registering anything. It then reads the chosen active project from its host. If
needed, it registers a metadata-only replica on the session's host using the
**same project UUID**, name, slug, description and repository URL. It does not
copy another host's checkout path, scope or credentials. Conflicting registrations
fail explicitly. The session owner then performs the authoritative assignment.

PUT returns the stored membership and revision. Revision mismatches return 409;
reload before retrying. A lost response can be resolved with GET. Unknown fields
and null project IDs are rejected. Older session hosts return an upgrade-required
error instead of appearing to accept an ignored update. Per-host feature flags
now expose `project_assignment_enabled` and honor the selected Guild host.

## Preservation and concurrency

- Assignment keeps the session ID, execution host, running process, working
  directory, conversation and lifecycle state. It sends no model message and
  performs no runtime start or stop.
- A free-floating session becomes a worker. Moving an existing worker preserves
  its role, objective and labels but clears its former parent and context revision.
- The former `workload_config.project_context` launch snapshot is removed so a
  future restart cannot inject the old project's saved brief. Already-delivered
  model instructions remain unchanged. Assignment does not claim the destination
  project's instructions have been consumed.
- An atomic repository operation compares `coordination_revision` and owner/tenant
  scope, persists the relationship and increments the revision. Ordinary lifecycle
  updates preserve membership and cannot restore a stale project brief. Migration
  `000068` (formerly `000067` on the UI branch, retained as a checksum alias) adds the revision with default zero in the source, CLI bundle and Helm chart.
- A successful write emits the normal session-updated event. Historical receipts
  and launch dispatch identities retain their original project provenance.
- Cross-host registration and assignment are not one distributed transaction. An
  unsuccessful final assignment may leave a valid metadata-only registration,
  which is reused by a retry; no session is reassigned until the owner commits.

## Deliberately limited scope

Create new coordinators inside their project and archive them when no longer
needed. This editor does not move existing coordinators, change parents or roles,
remove membership, move dependent subtrees, or deliver project instructions.
Mutable cross-host coordinator graphs need a separate authoritative relationship
protocol and are not part of this assignment feature.

A metadata-only replica supports organisation and project session discovery. It
does not make shared project files available on that node; preparing a checkout
for context loading or future project launches remains a separate action.

The September 19 read-only inventory found Kit registered on **Build Bro**,
Lexi on Thor and Spark with the same UUID, Physics on Thor, and no projects on
Build-Kit. Build lacked the Projects API entirely. Session-owning hosts must be
upgraded before their existing sessions can be assigned. The project-holding host
only needs the existing Projects read API. ForgeKit/iOS/macOS can adopt this same
resource; their native assignment editors are not implemented by this web change.

## Validation

Tests cover native and facade APIs, cross-host metadata replication, session
ownership/access, stale edits, offline/unsupported hosts, archived projects,
coordinator restrictions and late lifecycle writes. Web tests cover owner routing,
per-host loading, successful cache updates, conflicts and retries. Browser tests
exercise title/sidebar assignment on desktop and touch layouts, retained host
identity and persistence after reload. The real PostgreSQL compare-and-swap race
and stale-writer test is marked integration and runs in PostgreSQL CI.

## Source references

- [Project service](../../src/volundr/domain/services/projects.py)
- [Native project API](../../src/volundr/adapters/inbound/rest_projects.py)
- [Session persistence](../../src/volundr/adapters/outbound/postgres.py)
- [Guild facade](../../src/niuu/adapters/inbound/rest_volundr.py)
- [Web editor](../../web-next/packages/plugin-volundr/src/ui/AssignSessionProject.tsx)
- [Checkout discovery](project-checkout-discovery.md)
