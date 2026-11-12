# Project memory

A Forge project owns the Mímir memory its sessions use. Every session attached
to the project, and every member session of a workflow run in it, is launched
with the project's mounts, and every harness (Claude Code, Codex, Ravn) is told
how to use them. Nobody re-shares context between sessions by hand.

## Who may change it

The same rules as the project's [team integrations](external-integrations.md):

| Who | May |
|---|---|
| Project owner (or tenant admin) | add, change and remove mounts |
| Any member (viewer, contributor, approver, owner) | list the mounts |
| Anyone else | learn nothing: the project reads as not found (404) |

A member without the owner role gets a 403 naming the role the change needs.
A mount cannot be pointed at a registry entry the project's organization
cannot reach; a missing entry and an unreachable one give one refusal, so an
owner cannot probe which entries other organizations have.

## What a mount is

| Field | Meaning |
|---|---|
| `name` | The mount's name in the project (unique per project). A session sees it as the MCP server `mimir-<name>`. Lowercase letters, digits, `-` and `_`. |
| `registry_entry_id` | The Mímir registry entry it points to. A resource node of a workflow graph is only a pointer to a registry entry, so a project mounts the entry itself. |
| `access` | `read`, or `read_write`. Held by the memory gateway; without one Forge can hold neither `read` nor prefixes, so mount `read_write` with no prefixes (see below). |
| `write_prefixes` | Relative page paths such as `project/` that a `read_write` mount may write. Empty means any page. Refused for `read`. |
| `added_by` | The owner who added it. |

Routes, under `/api/v1/forge/projects/{id}/memory`:

```text
GET    /memory           list (every member)
POST   /memory           add: {"name", "registry_entry_id", "access", "write_prefixes"}
PATCH  /memory/{name}    change access and write prefixes
DELETE /memory/{name}    remove
```

## How sessions and runs inherit

Forge derives the session's `workload_config.mimir` again at **every launch**
(start, restart, and the start of a workflow run's unit), from three sources:

1. the project's mounts, resolved against the registry for the session owner's
   organization;
2. the session's launch spec, when it sets `workload_config.mimir`;
3. the `workload_config.mimir` the session was created with.

A mount added or removed since the last launch applies to the next one. A
workflow run's members are created in the run's project, so they go through
the same path; Ting does nothing extra.

**One name, one target.** If two sources use the same mount name for different
targets, the launch is refused with `409` naming the mount and both sources.
Nothing wins silently. Repeating the same target is fine.

## Read-only and write prefixes need the memory gateway

A limit (`read`, or `read_write` with prefixes) can only be held, never left to
the model's good behaviour. Who holds it depends on whether Forge is given the
memory gateway (`memory.gateway_url`, see below).

**With the gateway**, the session's one `memory` server carries every mount and
the gateway enforces each mount's access on every call, for every harness. Forge
accepts a limit when a mount is added or changed and at launch, starts no
per-mount `mimir-<name>` server, and appends no `mimir_*` instructions to the
prompt. The launch's mounts (the project's, the launch spec's and the session's
own) reach the gateway in the session's memory context as `attached`; see
[the memory context](#what-the-gateway-asks-forge). A mount the gateway could not
open (one in `instances` or `hosted_url`, an `ephemeral_locals` entry, or a ref
with no `registry_entry_id`) fails the launch naming the fix.

**Without the gateway**, Forge reaches a registry entry over its **url** or
**adapter**: the session talks to that Mímir directly (which is also where the
typed-fact tools live), and Forge no longer starts a Mímir of its own for a
mount, so it holds no limit anywhere. Forge refuses one for every target: adding
or changing the mount fails with `422`, and so does a launch of a mount that
already carries one. Mount the entry `read_write` with no prefixes; that Mímir
decides what the session may write. A registry entry no longer points at a
directory.

## Launches that cannot get their memory fail

- A project mount whose registry entry is gone, is disabled, has no way to
  connect, or is not reachable by the session's owner fails the start with
  `422` naming the mount and the entry. The session is not started without it.
- A registry that cannot be asked (the hosted Mímir is down, refuses Forge's
  token, or answers something that is not its registry) fails with `503` naming
  `projects.memory.directory_kwargs`. "Could not ask" is never "not found".
- A session's own Mímir configuration that cannot be launched (an instance
  with no name, a ref with no connection, a binding for a mount the session
  does not have, a duplicate name, an adapter mount with no url to serve
  it) raises, with the remedy in the message. Nothing is skipped. A workflow ref
  with no connection that is not in the registry raises when it is resolved too.

## What the harness is told

Without the memory gateway: when a session has Mímir mounts, Forge appends to its system prompt, for every
harness, how to use them. Which tools it names depends on the mounts:

- If at least one mount's registry entry has `facts: true` (the Mímir behind it
  serves the typed-fact tools): call `mimir_fact_context_pack` for the work at
  the start; call `mimir_fact_recall` before re-deriving something; call
  `mimir_fact_remember` only for outcomes it checked, with the evidence.
- Otherwise (page-only mounts): call `mimir_search` and `mimir_read` at the
  start and before re-deriving; call `mimir_write` only for checked outcomes,
  citing their sources.

Both tell the session that a refused write is final.

Facts a session writes are an **agent's**: Mímir assigns the level from the
caller's verified identity, never from the request. A session's token comes from
the workload-identity exchange, which sets the actor claim whenever the workload
is not the token's own subject, and Mímir resolves any principal with an actor
to an agent (a tool at most, and never higher). The same user's own login would
resolve through its roles, which is why the session's token must stay the
session's. Trust is not a field of the tool.

## Configuration

Where a mount's registry entry is looked up (`projects.memory`):

| Where Forge runs | Directory | Set by |
|---|---|---|
| Mini mode (`niuu platform up`) | `MimirRegistryFileDirectory`: the registry file of the Mímir the host runs (`<mimir path>/.mimir-registry.json`) | the CLI, with `PROJECTS__MEMORY__DIRECTORY_*` (an operator's own value wins) |
| A cluster with a hosted Mímir | `MimirRegistryHttpDirectory`: `GET {mimirUrl}/mimir/registry/mounts` with a workload token Forge mints for the session's owner (audience `mimir`, Forge as the acting workload; no PAT) | the Volundr chart, when `projectMemory.mimirUrl` is set |

The chart refuses to render `projectMemory.mimirUrl` without
`workloadIdentity.enabled`, and adds `mimir` to the workload audiences. Mímir
answers with the entries the owner's tenant can reach, so an entry of another
organization is not found. Set `facts: true` on a registry entry whose Mímir
serves the fact tools.

```yaml
projects:
  memory:
    directory_adapter: volundr.adapters.outbound.mimir_registry_http_directory.MimirRegistryHttpDirectory
    directory_kwargs:
      base_url: https://mimir.example.com
      audience: mimir          # optional
      timeout_seconds: 10.0    # optional
```

Point `directory_adapter` at another `MemoryMountDirectory` to look entries up
elsewhere. A project without mounts never asks.

## The memory server and the project layer

Besides its mounts, a project owns a **memory layer** of its own, reached through the
memory server that every session runs once Forge is told where the memory gateway (the
Mímir service) is.

| Setting | Meaning |
|---|---|
| `memory.gateway_url` (chart: `memory.gatewayUrl`) | Base URL of the gateway as a session reaches it, e.g. `http://127.0.0.1:8080/api/v1/mimir`. Set: every session gets Skuld `memory.enabled: true` and `memory.gateway_url`, whichever pod manager starts it, and the gateway holds every mount's limits (see above). Empty (default): no session runs a memory server, and mount limits are refused. |
| `memory.gateway_service_subject` (chart: `memory.gatewayServiceSubject`) | The workload subject of the gateway's own service credential, e.g. `system:serviceaccount:niuu:mimir`. Required with `gateway_url` while Forge verifies its callers; the allow-all identity of local mini mode needs none. See [the retention question](#when-run-and-session-layers-ended). |
| `memory_write_prefixes` on a project | Page path prefixes, relative (`decisions/`, `runbooks/`), under which sessions of the project write straight into the project layer. Any other write to it becomes a proposal. Empty (default): every write is a proposal. |

A project **owner** sets the prefixes with `PATCH /api/v1/forge/projects/{id}` (body
`{"revision": n, "memory_write_prefixes": ["decisions/"]}`; the list replaces the old one) and anyone on
the project reads them with `GET`. A contributor changing anything else on the project
cannot change them (403). They follow the same rules as a mount's write prefixes: no
leading `/`, no `..`, no repeats.

The memory server authenticates to the gateway with the session's own Forge credential
(scope `forge:memory`, granted to every session). The gateway asks Forge on every call
(`GET /api/v1/forge/sessions/{id}/memory-context`) for the session's owner, tenant,
project, workflow run and the project's write prefixes. That route answers only to the
session's own credential; a person, a PAT or another session is refused (403), and a
stopped, archived or restarted session is refused (401), which ends its memory access.

Forge refuses to start with `memory.gateway_url` set while `forge_mcp.session_tokens` is
off or no pod manager can hand the broker its credential, and the chart refuses to render
`memory.gatewayUrl` without session credentials: name the fix in the message and either
enable them or clear the URL.

## What the gateway asks Forge

Besides the session's own memory context, the gateway asks Forge three things, all under
`/api/v1/forge` (and forwarded by the platform's Forge front door, which answers 404 for a
route it does not carry).

### The session's memory context

`GET /sessions/{id}/memory-context`, with the session's own credential, adds what the gateway
needs for the session's context pack: `model` (the model it was launched with, or null),
`objective` (its coordination objective, or null), `project_brief` (the project's brief, or
null), `since` (when this owner's previous session in the same project began, or null for the
first) and `attached`, one entry per memory mount of the launch:

```json
{"name": "team-notes", "registry_entry_id": "abc", "access": "read|read_write",
 "write_prefixes": ["notes/"], "source": "project|launch_spec|session"}
```

`source` says where the mount comes from; `session` is a mount the session itself set.
A mount with no binding is `read_write` with no prefixes: its own Mímir decides what it
accepts, as before. A launch with a mount the gateway cannot open is a `409` naming the fix.

### A person's role on a project

`GET /projects/{id}/role` with the person's own login answers
`{"project_id", "role": "viewer|contributor|approver|owner"}`; a tenant admin is an owner. A
person who is not on the project gets the same `404` as `GET /projects/{id}`. The gateway
asks it to let an approver or owner decide proposals to the project layer.

### Who owns a workflow run

`GET /memory/runs/{run_id}` with the person's own login answers
`{"run_id", "owner_id", "tenant_id", "session_ids"}`, from the sessions that carry
`workload_config.workflow_run_id`. It is a `404` unless the caller owns all of those sessions
or is a tenant admin of their tenant. The gateway lets only the run's owner promote from the
run layer. The platform's front door asks every Forge node the caller can see and joins the
answers.

The role and run routes answer only to a person's own login: a session credential, a scoped
workload token or PAT, or a workload acting for someone gets `403`.

### When run and session layers ended

`POST /memory/ended` with `{"sessions": ["id"], "runs": ["id"]}` answers, per id,
`{"state": "active|ended|gone", "ended_at": "ISO time or null"}`. A session has ended when its
status is stopped, failed or archived, and `ended_at` is its last update. A run has ended when
none of its sessions is active, at the latest of their ends. `gone` means Forge has no such
session, or no session of that run. The gateway deletes a layer `memory.retention_days` after
it ended, or at once when it is `gone`.

Only the gateway's own service credential may ask. It is the gateway's workload-identity token
(the exchange of its ServiceAccount), whose `act.sub` Forge compares with
`memory.gateway_service_subject`; a person, a session credential or another service gets `403`.
Where Forge's identity is allow-all (local mini mode) nothing verifies callers, so the local
caller passes, still without a session or scoped credential. A Forge that verifies callers and
has `memory.gateway_url` but no `gateway_service_subject` refuses to start. Map the gateway's
ServiceAccount in `workload_identity` to an owner that is not the subject itself (the exchange
sets `act` only for a workload acting for someone else), and let that owner see every Forge
node (the platform's front door asks every node the caller can see, and fails if one cannot
answer, so a node that does not respond never reads as "its sessions are gone").
