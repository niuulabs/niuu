# Lightweight Forge projects — review and evolution

**26 September 2026 · base: live Forge `5abeb85e` (forge-session-notifications-20260926c) and Lexi build 2287 (`f9876ca3`)**

The owner's direction on 2026-09-26: a project is a **logical grouping on the Forge server** — an
identity sessions attach to, closer to a tag than to a repository. **Coordinator** sessions belong
to a project; **worker** sessions belong to a coordinator. A Git repository is **optional**: when
present it persists custom agents, workflows and skills, and it can be added, replaced or removed
later without re-creating the project.

This document reviews what shipped between 11 and 19 September, then specifies the evolution.
It supersedes [IMPLEMENTATION.md](IMPLEMENTATION.md) where they disagree: that contract made the
Git meta-repository the project.

---

## Part 1 — Review: how projects work today

### What a project is

A project is a row in `forge_projects` (`id`, `owner_id`, `tenant_id`, `document JSONB`,
`revision`) on **each** Forge host that knows it. The JSON document is `ForgeProject`
(`src/volundr/domain/projects.py`): `id`, `slug`, `name`, `description`, **`repo_url` (required,
validated HTTPS or `git@`)**, `workspace_path`, `status`, scope and revision.

Live today (Thor facade): **Lexi** (registered twice — Thor and Spark, same UUID, different
checkouts), **Physics** (Thor) and **Kit** (Build Bro). About ten sessions carry coordination; four
are workers with a parent.

### Where Git is load-bearing

| # | Coupling | Where |
|---|---|---|
| 1 | `repo_url` is mandatory and validated; the mesh also refuses a project without it and compares it to detect conflicts | `projects.py:47,56-75`; `rest_volundr.py` assignment replica |
| 2 | `POST /projects` demands the client-supplied UUID "from project.json"; checkout discovery derives identity from the Git remote (uuid5) | `rest_projects.py:93-96`; `project_workspace.py:128` |
| 3 | **Every coordinated launch reads a checkout** — `briefing()` → `workspace.context()` fails with "Project has no local checkout on this host" when `workspace_path` is empty | `services/projects.py:155`; `project_workspace.py:27-35` |
| 4 | The brief is Git-shaped: "Meta-repository", "Local project checkout", revision = `HEAD@sha256:…` | `services/projects.py:161-171` |
| 5 | `repo_url` and `slug` are immutable; a repository can never be added, replaced or removed | `services/projects.py:91-93` |
| 6 | Creating a project in the app requires a host **and** an absolute folder containing a Git checkout with exactly one usable remote under an allowed prefix | iOS `RegisterForgeProjectView`; `project_workspace.py:25-128` |
| 7 | A metadata-only replica (what assignment creates on another host) can never launch a coordinated session | consequence of 3 |

### Where the relationships stop short

- Session `coordination` (`project_id`, open-string `role`, `parent {instance_id, session_id}`,
  `objective`, `labels`) is a good, harness-neutral shape, stored as JSONB with a
  `coordination_revision` compare-and-swap. **Reuse it.**
- `PUT /sessions/{id}/project` (19 Sep) can attach a session to a project, but **cannot set a role
  or a parent, cannot detach, and refuses to move a coordinator**.
- Launch requires a caller-saved `dispatch_id` and (in the apps) a non-blank objective.
- Nothing checks that a worker's parent is a coordinator; nothing follows a coordinator when it
  moves or stops.
- The Forge MCP `create_session` cannot pass `coordination`, so **a coordinator cannot create its
  own workers** through the tool it actually has. Project routes are closed to session tokens.
- Project identity is per host. The facade forwards `POST /sessions` without mirroring the project,
  so launching into a project on a host that has not registered it fails.

### The apps (build 2287)

- **Create** = the heavy "Connect repository" sheet: host + absolute Git folder, name optional.
  There is no create-by-name API, no rename/archive UI, no "attach repository later".
- `FKProject` decodes `repo_url`, `workspace_path`, `slug`, `description`, `revision` as
  **required**: one repository-less project would fail the whole host's project list.
- **Attach an existing session**: not possible on iOS or Mac (Mac says so in its workspace view).
- **Worker under a coordinator**: Mac has "New related session…"; iOS never passes a parent.
- **Hierarchy**: Mac builds a coordinator → worker tree (`MacProjectGroup.rows`, macOS-only). iOS
  shows active coordinators, then workers grouped by repository/status — no nesting; stopped
  coordinators drop out of the pinned section.
- Every project launch requires a folder and a written objective.

### Unmerged work

`xteo/project-documents-20260913` (niuu) and `xteo/project-records/-authorship/-coordination-next`
(lexi-ios) add a "Knowledge" reader over committed project documents and message-authorship
witnesses. They are 140–250 commits behind, deferred (UX-017/018/019), and depend on the
repository-first model. They stay parked; the documents idea becomes an optional capability of a
project that has a repository.

---

## Part 2 — Evolution

### Model

```
Project (identity, name, brief, optional repository)
 ├── Coordinator session(s)          role = "coordinator", no parent
 │    ├── Worker session             role = "worker", parent = coordinator
 │    └── Worker session
 └── Unparented member session(s)    role = "worker", no parent
```

- **A project is an identity first.** `POST /projects {"name": "Lexi"}` is a complete request. The
  server generates the UUID and a unique slug. Nothing touches a filesystem.
- **The project lives on a home host** (where it was created; the facade defaults to the local
  host). Other hosts receive a **metadata replica** automatically the first time a session there
  joins the project — at launch or on attach — exactly as assignment already does.
- **The repository is optional and replaceable.** `repository` = `repo_url` (+ per-host
  `workspace_path` checkout). Attach later with `PATCH` or by connecting a folder; detach by
  clearing it. When a checkout is present, launches keep reading its bounded context
  (`PROJECT.md`, `context/CURRENT.md`, `context_files`) exactly as today, and receipts can still be
  exported into it.
- **The brief lives in Forge.** A new optional `brief` (≤ 8 KiB) is edited in the app and injected
  into every project launch. A repository's context is appended when a checkout exists on the
  launch host. Revision = `brief:<project revision>` or the existing `HEAD@sha256:` form.
- **Roles.** `coordinator` and `worker` are the product roles; the field stays an open string so
  `reviewer` and friends keep working. A worker's `parent` must be a member of the same project;
  the apps only offer coordinators as parents.

### API changes (contract version 2)

| Operation | Change |
|---|---|
| `POST /projects` | `id` optional (server generates), `slug` optional (derived from name, uniquified per owner), `repo_url` optional, new `brief`. A full v1 body still works unchanged. |
| `PATCH /projects/{id}` | Also accepts `brief`, `repo_url` (set or `""` to detach). Clearing `repo_url` clears the local checkout path. |
| `GET /projects/{id}/context` | Works without a checkout (returns the brief). |
| `POST /sessions` + `coordination` | `dispatch_id` optional (without it the launch is simply not deduplicated); objective optional; no checkout required. |
| `PUT /sessions/{id}/project` | New optional fields: `role`, `parent` (`null` clears), and `project_id: null` to **detach**. Moving a **coordinator** is allowed; its local workers move with it. Attaching with a `parent` and no `project_id` joins the parent's project. |
| Facade `POST /sessions` | Mirrors the project to the target host before launching when coordination names a project that host lacks. |
| Facade `PUT /sessions/{id}/project` | Accepts repository-less projects (no longer requires `repo_url`); passes role/parent through. |
| Forge MCP `create_session` | New `role`, `project_id`, `parent_session_id`, `objective`. A coordinator that omits them creates a **worker of itself** in its own project. `environment` already reports `project_id`. |
| Features | `project_contract_version: 2`, `project_lightweight: true`. |

Every change is additive. `repo_url` and `workspace_path` still serialize as strings (`""` when
absent), so build ≤ 2287 decodes new projects. No migration is required: the project document is
JSONB and `coordination` already has its revision.

### App changes (Lexi iOS + Mac, one shared implementation)

- **New Project = a name** (description/brief optional, host defaults to the primary host).
  Connecting a repository folder moves into project settings as an optional step.
- **Project settings**: rename, brief, repository (attach folder / detach), archive/restore.
- **Project workspace tree**: coordinators (all states, active first) with their workers nested;
  then unparented members. Shared pure tree builder used by iOS and Mac.
- **Attach existing sessions**: "Add sessions…" in the project menu (multi-select of sessions not
  yet in a project), plus **Project…** on every session row/detail: choose project, role
  (coordinator/worker) and coordinator; detach.
- **New worker** directly from a coordinator row; **New coordinator** from the project.
- Folder and objective become optional for project launches; the coordinator prompt no longer
  assumes a Git checkpoint.
- Tolerant `FKProject` decoding; per-host project lists are merged by project id.

### Compatibility and rollout

1. Forge server first (Thor, then Spark/Build Bro as they upgrade). Old apps keep working:
   strings stay strings, v1 requests are unchanged.
2. Apps detect `project_contract_version >= 2` for create-by-name and the richer assignment; on an
   old host they fall back to "Connect repository" and show "update this host" for assignment.
3. Existing repository projects (Lexi, Physics, Kit) keep their checkouts, context and receipts.
   The coordinator skill in `xteo/project-lexi` keeps working: its CLI calls are v1-compatible.

### Out of scope for this step

Nested projects, a project-level durable scheduler, cross-host subtree moves (remote workers of a
moved coordinator keep their parent link and are reported), the parked Knowledge/authorship work,
and project voice routing.

---

## Part 3 — Delivered (26 September 2026)

| Piece | Where | Evidence |
|---|---|---|
| Forge contract v2 (this document) | niuu `forge/lightweight-projects` `a2abcda4` (live build + 1 commit) | 20,893 backend tests green; 7 real-PostgreSQL project tests green |
| Live on Thor | release `forge-lightweight-projects-20260926`, drop-in `zzzzzzzzzzzzz-lightweight-projects-20260926.conf` | guarded cutover: healthy in 4 s; 54 protected processes and 656 sessions PRESERVED |
| Live acceptance | `~/.local/share/niuu/rollouts/forge-lightweight-projects-20260926/acceptance.py` | name-only create, brief + context, attach as coordinator, worker launch with Guild-id → `thor` parent translation, parent/project filters, detach keeps workers; smoke data archived |
| Lexi iOS + Mac | lexi-ios `xteo/lightweight-projects-20260926`, build 2288 | ForgeKit 331 tests, project unit + UI journeys, Mac compile, screenshots in `apps/chat/build-screenshots/build-2288/` |

Rollback: remove the drop-in, `systemctl --user daemon-reload && systemctl --user restart
volundr-forge.service` → `forge-session-notifications-20260926c`. A repository-less project
created after the cutover is not readable by the older build (its `repo_url` is empty).

Not yet done: Spark, Build and Build Bro still run contract 1 (the app shows "update this host"
for the new actions there); the web UI keeps its existing assignment editor; a moved or
re-parented session's saved launch brief is cleared (as in contract 1) and is not rebuilt on
restart; the `forge-coordinator` skill in `xteo/project-lexi` can adopt `projects create` /
`sessions attach` and the MCP defaults.
