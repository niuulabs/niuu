# Workflow catalog storage

Ting stores workflow definitions as one versioned YAML document per workflow.
Runs, campaigns, approvals, saga assignments, and historical execution snapshots
remain in PostgreSQL.

New installations use the filesystem adapter with an editable catalog at
`~/.niuu/workflows`. Mini mode uses that path under the operator's normal home.
The Niuu Docker launcher sets `HOME` to `data_root/home`, so its host catalog is
`data_root/home/.niuu/workflows`. The Ting Helm chart mounts a persistent volume
at `/data/ting/workflows`. Multiple replicas require storage that supports
`ReadWriteMany`; the adapter uses advisory file locks and atomic replacement
across workers.

Packaged definitions are loaded from `ting/workflows` and are read-only. Editing
one requires an explicit copy with a new UUID. Editable filenames are for humans:
renaming a file does not change the UUID inside it. Local ownership, tenant,
timestamps, imported persona source documents, and unresolved binding
requirements live in `.metadata` sidecars and are not exported as authority.

## Portable document

See the complete packaged
[`Code & Review Flow`](../../src/ting/workflows/code-review-flow.yaml) for a
real document with its full graph and exact current persona pins.

`schema_version` versions the file contract. `version` is the workflow's own
release label. Each `personaId` used by a stage is an alias declared exactly
once in `persona_dependencies`. Digests pin source-authored Ravn persona content;
they exclude local bindings and runtime prompt injection. Unknown graph fields
are retained so editor positions, gates, resources, event bindings, stage
overrides, and artifact paths round trip without loss.

Invalid YAML, duplicate keys or identities, unsafe aliases or bundle paths,
unsupported schema versions, and undeclared persona aliases make catalog reads
fail with the file and cause. Ting does not serve a stale cached definition.

## Sharing and editing

The workflow list and builder support importing a YAML file or a ZIP bundle.
Export YAML when the recipient already has the required persona revisions;
export a bundle to include their exact source definitions. A bundle contains
`workflow.yaml` and one file per persona under `personas/`.

Import previews show persona dependencies and any required local resource
bindings. Bundled personas stay scoped to the imported workflow; they do not
overwrite the recipient's persona registry. Missing or conflicting personas can
be mapped explicitly to a local persona. Imports create a new workflow identity
by default. The API also supports updating an existing editable workflow with
its expected revision. A changed preview must be reviewed again before applying.

Environment-specific resource connections must be bound to an accessible local
registry entry. A workflow can be saved with unresolved resource requirements,
but cannot launch until they are resolved. Exports reject inline credential fields;
secret environment references remain references, not exported secret values.

Saved workflows retain their pinned persona definitions when the registry
changes. Refreshing a persona is an explicit editor action. Every new execution
captures those definitions in its snapshot so later edits do not change that
execution. The persona registry currently exposes its current source revision;
older revisions remain available through workflows and snapshots that captured
them, rather than through a global version archive.

## Migrating an existing database catalog

Create the durable target directory, then run a dry inventory:

```bash
python -m ting.migrate_workflows --catalog-path /durable/ting/workflows
```

By default, the command reads the `volundr` database on Ting's configured
PostgreSQL host and resolves each persona through the workflow owner's registry
scope. Use `--persona-database NAME` when that registry database has another
name. If the personas have been deliberately exported to one shared source,
pass one `--persona-dir /durable/exported-personas` option per directory; this
explicit mode does not apply per-owner registry overrides. The command
inventories every workflow and saga/campaign reference, compares
packaged system rows, resolves exact current persona revisions, checks ownership
and destination conflicts, and reports missing personas or divergent packaged
rows without writing. Resolve every reported conflict, freeze workflow-definition
writes, and apply:

```bash
python -m ting.migrate_workflows --catalog-path /durable/ting/workflows --apply
```

Apply rechecks the complete inventory, publishes editable workflows, verifies
every referenced UUID, and writes a migration marker only after validation.
Startup refuses filesystem cutover when PostgreSQL contains rows without a
matching marker and complete file inventory. Reruns are idempotent. A divergent
packaged system row is never shadowed implicitly. Inspect its diff, then pass
`--replace-divergent-bundled` to preserve that row under its existing UUID and
record the corresponding packaged identity as explicitly replaced.

If apply is interrupted, keep workflow-definition writes frozen and rerun the
same command, including `--replace-divergent-bundled` when selected. Replacement
authorization can be recorded before every replacement is published; the
completion marker and startup guard prevent serving that incomplete cutover.

The old workflow rows remain intact. To roll back, explicitly configure
`ting.adapters.postgres_workflows.PostgresWorkflowRepository`, set
`seed_bundled: true`, and reconcile any file edits made after cutover first.
PostgreSQL is not synchronized with the file catalog after migration. Existing
historical snapshots are kept as recorded; migrations do not invent persona pins
for old runs.

## Configuration

The default is:

```yaml
workflow_repository:
  adapter: ting.adapters.filesystem_workflows.FilesystemWorkflowRepository
  seed_bundled: false
  kwargs:
    catalog_path: ~/.niuu/workflows
    create_directory: true
```

For operator-supplied paths, omit `create_directory` to require a pre-existing,
readable, writable mount. Missing or unwritable configured storage stops startup.
Archive imports default to a 4 MiB upload limit, 16 MiB expanded limit, and 128
entries under `workflow_import`; adjust these through Ting configuration.
