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
| `access` | `read`, or `read_write` with the page prefixes it may write. |
| `write_prefixes` | Relative page paths such as `project/`. Required for `read_write`, refused for `read`. |
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

## Launches that cannot get their memory fail

- A project mount whose registry entry is gone, is disabled, has no way to
  connect, or is not reachable by the session's owner fails the start with
  `422` naming the mount and the entry. The session is not started without it.
- A session's own Mímir configuration that cannot be launched (an instance
  with no name, a ref with no connection, a binding for a mount the session
  does not have, a duplicate name) raises, with the remedy in the message.
  Nothing is skipped.

## What the harness is told

When a session has Mímir mounts, Forge appends to its system prompt, for every
harness: call `mimir_fact_context_pack` for the work at the start; call
`mimir_fact_recall` before re-deriving something; call `mimir_fact_remember`
only for outcomes it checked, with the evidence.

Facts a session writes are an **agent's**: Mímir assigns the level from the
caller's verified identity, never from the request. A session's token comes from
the workload-identity exchange, which sets the actor claim whenever the workload
is not the token's own subject, and Mímir resolves any principal with an actor
to an agent (a tool at most, and never higher). The same user's own login would
resolve through its roles, which is why the session's token must stay the
session's. Trust is not a field of the tool. A mount carries no trust setting, and the instructions say the
session cannot raise its level. A derived fact takes the lowest trust of its
inputs, so a session cannot launder a guess through a person's fact.

## Configuration

```yaml
projects:
  memory:
    repository_adapter: volundr.adapters.outbound.postgres_project_memory.PostgresProjectMemoryRepository
    directory_adapter: volundr.adapters.outbound.mimir_registry_directory.MimirRegistryFileDirectory
    directory_kwargs:
      registry_path: ~/.ravn/mimir/.mimir-registry.json
```

The default directory reads the Mímir registry file, so the Forge process must
be able to open it. A missing file is an error at add time and at the launch of
any session in a project that has mounts. A project without mounts never reads
it. Point `directory_adapter` at another `MemoryMountDirectory` to look
entries up elsewhere.

## Not enforced by Forge today

`access` and `write_prefixes` are recorded and passed to the session's Mímir
configuration as bindings, the same way a workflow's bindings are. The Mímir
MCP servers a session talks to do not receive them yet: what a session may
write is still decided by the Mímir behind each mount. Do not treat a `read`
mount as a security boundary until the servers enforce it.
