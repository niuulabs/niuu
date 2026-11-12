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
| `access` | `read`, or `read_write`. |
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

## Read-only and write prefixes hold, or the mount is refused

A limit (`read`, or `read_write` with prefixes) is held by the Mímir server Forge
starts for the session, never by the model's good behaviour:

- A **path** registry entry is started as `mimir mcp --path … --read-only` or
  `--write-prefix <p>` (repeatable). That server puts the mount behind the
  composite adapter, which refuses a write that the mount does not allow
  (`upsert_page`, `delete_page`, `ingest`, thread changes, `lint --fix`), counts
  it (`write_refusals`, per mount) and logs it at error. The tool returns the
  refusal to the model as a tool error with the mount named. An ingest or lint
  fix cannot be confined to prefixes, so a prefix-limited mount refuses them.
- An entry reached over a **url or an adapter** is served by its own Mímir, and
  the session talks to that Mímir directly (which is also where the typed-fact
  tools live). Forge cannot hold a limit there, so it refuses one: adding or
  changing the mount fails with `422`, and so does a launch whose entry has since
  become a url entry. Mount such an entry `read_write` with no prefixes; that
  Mímir decides what the session may write.

## Launches that cannot get their memory fail

- A project mount whose registry entry is gone, is disabled, has no way to
  connect, or is not reachable by the session's owner fails the start with
  `422` naming the mount and the entry. The session is not started without it.
- A registry that cannot be asked (the hosted Mímir is down, refuses Forge's
  token, or answers something that is not its registry) fails with `503` naming
  `projects.memory.directory_kwargs`. "Could not ask" is never "not found".
- A session's own Mímir configuration that cannot be launched (an instance
  with no name, a ref with no connection, a binding for a mount the session
  does not have, a duplicate name, an adapter mount with no url or path to serve
  it) raises, with the remedy in the message. Nothing is skipped. A workflow ref
  with no connection that is not in the registry raises when it is resolved too.

## What the harness is told

When a session has Mímir mounts, Forge appends to its system prompt, for every
harness, how to use them. Which tools it names depends on the mounts:

- If at least one mount's registry entry has `facts: true` (the Mímir behind it
  serves the typed-fact tools): call `mimir_fact_context_pack` for the work at
  the start; call `mimir_fact_recall` before re-deriving something; call
  `mimir_fact_remember` only for outcomes it checked, with the evidence.
- Otherwise (wiki-only mounts): call `mimir_search` / `mimir_related` and
  `mimir_read` at the start and before re-deriving; call `mimir_write` only for
  checked outcomes, citing their sources.

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
