# Forge Projects: implementation contract

This document supersedes the design snapshot where it assumed one current coordinator or a Ting dependency. The project owner's accepted direction on 2026-09-11 is a durable Git meta-repository with any number of coordinator sessions. Coordination is an agent skill using Forge's public REST API and its thin CLI.

## Durable identity and storage

`xteo/project-lexi` is the first project repository, checked out on Thor at `/home/thor/projects/lexi`. Its stable UUID is `4b011f7f-87fb-4f4f-a53d-5eb4025c510f` in `project.json`. Mission, checkpoint, host preferences, source-repository bindings, accepted decisions, reusable knowledge, and curated handoffs live in Git. No coordinator session owns or defines the project's lifetime.

PostgreSQL stores a project index on each participating Forge host, immutable session membership/parent references, dispatch deduplication records, and an append-only receipt inbox. Registrations on different hosts use the same project UUID and Git remote with their own local checkout paths. The current implementation does not clone arbitrary repositories through an HTTP request: the existing Git/SSH workflow prepares a checkout and then registers it.

The additive migration is `000066_forge_projects`. Existing sessions have NULL coordination. Old binaries continue to read and update their existing columns; a code rollback does not require dropping project data. The migration is shipped in the root, packaged CLI, and Helm catalogs.

## Session contract

The ordinary `POST /api/v1/forge/sessions` accepts two optional fields:

```json
{
  "dispatch_id": "caller-saved-uuid",
  "coordination": {
    "project_id": "project-uuid",
    "role": "coordinator",
    "parent": {"instance_id": "thor", "session_id": "parent-uuid"},
    "objective": "The assignment",
    "context_revision": "optional expected checkpoint revision",
    "labels": ["review"]
  }
}
```

Role is an open string, not a closed workflow enum. Coordinator, worker, reviewer, and future roles share the normal session lifecycle, messages, tools, persistence, and replay. Membership and parent references are established before runtime launch and survive restart. They are immutable through the session update API. A coordinator can stop or be archived while its project and peers remain available.

Project launches require a saved dispatch UUID. Retries with the same request return the original session; a changed request conflicts. PostgreSQL advisory locks serialize duplicate requests across API workers, with reserved pool capacity for session writes. A deleted session leaves a dispatch tombstone. Restarting a stopped/failed session remains an explicit start operation.

`GET /sessions` can filter `project_id`, `role`, `parent_session_id`, and `parent_instance_id`. The mesh keeps the composite `(instance_id, session_id)` identity, including collisions between hosts. An explicit instance hint never silently falls back to another host.

Local parent links are checked for project membership and access. Foreign-host links carry no authorization and may be unresolved while a host is offline. They must be resolved through that host's normal authorized session API; accepting the link does not certify its existence or make the global graph transactionally consistent. The coordinator skill and cross-host acceptance workflow inspect those references.

## Project REST surface

All paths below are relative to `/api/v1/forge`:

| Operation | Contract |
|---|---|
| `GET /projects` | List visible registrations; mesh replies retain instance identity. |
| `POST /projects` | Register an existing meta-repository, idempotently by project UUID. |
| `GET /projects/{id}` | Read project metadata within the current owner/tenant scope. |
| `PATCH /projects/{id}` | Update name, description, status, or checkout using an expected `revision`. |
| `GET /projects/{id}/context` | Return a bounded checkpoint and its revision. |
| `GET /projects/{id}/receipts?after=N&limit=N` | Read a bounded page of handoffs and a next cursor. |
| `POST /projects/{id}/receipts` | Append a receipt with a caller-saved UUID; conflicting reuse returns 409. |
| `POST /projects/{id}/receipts/{id}/ack` | Record inspection acknowledgement, independently of work acceptance. |
| `POST /projects/{id}/export?after=N&limit=N` | Atomically write a receipt page into `history/handoffs/`. |

Feature flags advertise `projects_enabled`, `project_contract_version`, and `project_instance_id`. The mesh facade reports partial project-list failures in `X-Forge-Unavailable-Instances`; it fails explicitly if every host fails. Newer iOS clients can keep cached metadata visible when an older or offline host cannot answer.

## Context, handoffs, and recovery

The launch brief reads `PROJECT.md` and `context/CURRENT.md`, bounded to 8 KiB by default. A revision contains the Git HEAD plus a hash of the actual checkpoint content, so uncommitted context is never mislabeled as a clean commit. Forge persists the snapshot privately with the session and reuses it on restart. It includes the session's own full reference, project identity, role, assignment, and source location. It does not overwrite repository-owned `AGENTS.md` or `CLAUDE.md`.

The project repository contains `forge-coordinator` in `.agents/skills/`, with a Claude skill link and repository instructions. The skill guides recovery, host/harness selection, isolated worktrees, stable launch requests, evidence inspection, checkpoints, and Git reconciliation. It runs no second model loop and requires no Ting service.

Receipts survive a stopped recipient or API restart. Receipt cursor allocation is serialized per project so a reader cannot advance past an earlier uncommitted write. Export is explicit and retryable: an unavailable disk does not erase the database receipt. Export does not stage files, create commits, or push Git. The coordinator commits curated results and reconciles concurrent Git updates. The skill checks the relevant host inboxes; there is no claim that a background scheduler automatically delivers every receipt into a model's live turn.

## CLI

Install the package entry point `forge`, or run `python -m volundr.cli` from the configured environment. `forge --url URL --instance HOST` chooses an endpoint and optional facade target. Credentials come from `FORGE_TOKEN` (or a named `--token-env`); request bodies are JSON files or stdin so the full REST launch contract remains available.

Commands cover projects list/register/context/receipts/handoff/ack/export and sessions list/create/get/message/start/stop. `forge request METHOD /relative/path --body FILE` exposes the remainder of the REST API. `forge new-id` generates a UUID to save in a request file. The CLI does not automatically retry non-idempotent operations or interpret delivery as completion.

## iOS track

Branch `dev/project` adds Project between Voice and Code. Projects list durable active registrations, with search, an archive toggle, cached/offline metadata, and a repository registration flow. A project opens its scoped ordinary Forge sessions, allows new coordinators and tasks on a selected registered host, and retains the existing status filters and replay experience. Code adds grouping by project. Multiple coordinators are visible; a stopped coordinator does not hide its project.

Project and Code use the same app-owned session store, so they share transcript models instead of creating a second renderer. The Loops tab remains retired. Physical-phone testing is excluded; new UI behavior is validated on a simulator before TestFlight release.

## Validation and delivery status

The implementation is on local branch `dev/forge/forge-project`, tracking remote `forge/forge-project`, retaining the original design files. GitHub already has a `dev` branch, which prevents publishing a nested `dev/...` ref. Targeted project tests cover duplicate launches, payload conflicts, stopped/replaced coordinators, ownership, archive/restore, stale checkpoints, foreign references, receipt/export recovery, filesystem escapes, CLI routing, and partial mesh responses. Separate PostgreSQL tests exercise the complete migration catalog, persistence, contention exceeding pool size, receipts, and compare-and-swap updates in disposable schemas.

Code commit `673b5000` is running on Thor and Spark. The complete backend suite passed 19,493 tests with warnings treated as errors. Coverage is 86.50% including four real PostgreSQL acceptance tests, above the unchanged 85% gate. All 616 preexisting Thor session rows were preserved.

Live acceptance verified Codex coordination on Thor, Opus 5 context recovery, a Codex child on Spark, idempotent dispatch retry, replay, receipt acknowledgement/export, native stop/start/resume, and both API servers restarting without losing project state. Separate Codex coordinators recovered the project and committed their findings to its Git repository. Codex Astra with xhigh effort is the preferred driver; Claude uses Opus 5 through tmux.

The iOS track passed 288 ForgeKit tests, 27 focused simulator tests, and actual project registration, coordinator launch, and cross-host child → parent → project navigation. Build 2254 source and three inspected simulator screenshots are published on `xteo/lexi-ios` branch `dev/project`. [Detailed acceptance and practical boundaries](https://github.com/xteo/lexi-ios/blob/dev/project/docs/forge/projects-acceptance-2254.md) distinguish verified behavior from remaining activity-state work and release processing. The signed release is a separate gate; consult App Store Connect evidence before claiming TestFlight availability.
