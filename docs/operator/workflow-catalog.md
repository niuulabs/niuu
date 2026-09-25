# Workflow catalog storage

Ting stores workflow definitions as one versioned YAML document per workflow.
Runs, campaigns, approvals, saga assignments, and historical execution snapshots
remain in PostgreSQL.

New installations use the filesystem adapter with an editable catalog at
`~/.niuu/workflows`. Mini mode uses that path under the operator's normal home.
The Niuu Docker launcher sets `HOME` to `data_root/home`, so its host catalog is
`data_root/home/.niuu/workflows`. The Ting Helm chart mounts a persistent volume
at `/data/ting/workflows`. Multiple replicas require storage that supports
`ReadWriteMany` — the chart's own render fails otherwise (see below); the
adapter uses advisory file locks and atomic replacement across workers.
For example, static NFS: the export must be owned by uid `65532` (the same
runtime user `podSecurityContext.fsGroup` targets — kubelet does not apply
`fsGroup` to NFS mounts, so ownership has to come from the export itself),
with attribute and lookup caching disabled (e.g. `nfsvers=4.1,hard,noac,
lookupcache=none`) so every pod sees every other pod's writes immediately —
the adapter's own read paths already reread from disk under its lock rather
than caching in-process, but a caching NFS mount would still let one pod see
a stale directory listing.

The **primary supported path today is a single replica on `ReadWriteOnce`
storage** (below); multi-replica `ReadWriteMany` is supported by the chart's
validation but not yet the exercised production configuration.

## Running on single-node (`ReadWriteOnce`) storage

Many real clusters only offer single-node block storage — the CSI driver on a
Harvester cluster, for example, supports `ReadWriteOnce`/`ReadWriteOncePod`
only, never `ReadWriteMany`. The chart supports this directly:

- Set `workflowPersistence.accessModes: [ReadWriteOnce]` (or
  `ReadWriteOncePod`). `workflowPersistence.accessModes` defaults to
  `ReadWriteMany` and the chart never changes that default itself — an
  existing PVC's `accessModes` is immutable, so switching it for you on
  upgrade would break the running install. Set it explicitly on a new
  install, or when reprovisioning storage for an existing one.
- Run a single replica: `replicaCount: 1`, `autoscaling.enabled: false`. A
  `ReadWriteOnce`/`ReadWriteOncePod` volume can only be attached to one node
  at a time, so a second replica (or an autoscaler that could create one)
  cannot schedule.
- The chart then automatically renders `strategy: {type: Recreate}` on the
  Deployment. `RollingUpdate` (Kubernetes' own default) would try to start
  the new pod before stopping the old one; on an RWO volume the surge pod
  cannot attach it and the rollout hangs on `Multi-Attach`. `Recreate` stops
  the old pod first, so there is a short window of downtime on every deploy —
  unavoidable with single-node storage, and why `ReadWriteMany` is still
  preferred where it's available. You can also set `strategy.type: Recreate`
  explicitly (e.g. to pin it before switching `accessModes`); on an RWO
  volume, only `Recreate` is accepted.
- More generally, `replicaCount > 1` or `autoscaling.enabled` requires
  `workflowPersistence.accessModes` to include `ReadWriteMany` — any other
  access mode (not just RWO/RWOPod) can only attach to one pod at a time, so
  the chart fails the render with the conflict rather than deadlocking a
  rollout later. This also governs `workflowPersistence.existingClaim`:
  `accessModes` is read as the operator's statement about what that claim
  supports, regardless of whether the chart or the operator provisioned it.
- `podSecurityContext.fsGroup` defaults to `65532` — the unified `niuu`
  image's runtime user (`USER 65532:65532`, non-root) — with
  `fsGroupChangePolicy: OnRootMismatch`. Without this, a freshly provisioned
  block volume mounts root-owned (`0755`) and the filesystem workflow
  catalog refuses to start ("unwritable configured storage"). This applies
  regardless of access mode; it's called out here because it's what makes a
  brand-new RWO volume usable at all.

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
*operator-authored* packaged-identity row (`version_origin: authored` — a
saved edit that kept a packaged workflow's own UUID) is never shadowed
implicitly. Inspect its diff, then pass `--replace-divergent-bundled` to
preserve that row under its existing UUID and record the corresponding
packaged identity as explicitly replaced.

A row whose content differs from the current packaged definition but whose
`version_origin` is `bundled` (package-seeded, never edited) is not
divergence to resolve — it is simply an older release of the package's own
content, superseded by what is now packaged under the same identity. The
dry-run and apply reports count these separately as `bundled_superseded`;
`--replace-divergent-bundled` is neither needed nor consulted for them, and
nothing is written — the file catalog already serves the current packaged
definition for that id. `bundled_matches` (identical to the current package)
and `bundled_superseded` (an older release of it) are both "no action
needed"; only rows outside either bucket can raise a divergence error.

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

### Upgrading from a pre-#1012 install

PR #1012 introduced `version_origin` (`migrations/ting/000044_workflow_versions.up.sql`)
to tell an operator-authored workflow apart from package-seeded content. Before
that PR, every `scope: system` row was, by construction, exactly package
content — there was no "authored system workflow" yet — but `000044`'s
`ADD COLUMN ... DEFAULT 'authored'` backfills every pre-existing row,
including those legacy system ones, to `authored`. Left uncorrected, that
mislabels them: the PostgreSQL-path reseed then treats each as a protected
operator successor and stops updating it, and the file-migration path reports
each as a divergent authored row requiring `--replace-divergent-bundled`, even
though nothing was ever edited.

Migration `000046_reclassify_legacy_system_workflows` reclassifies exactly the
rows that can only be pre-#1012 legacy content back to `version_origin:
bundled` — never a row a genuine post-#1012 save could have produced (see the
predicate's comment in the migration file for the full reasoning: it keys off
a row never having a `workflow_versions` archive entry, which the versioned
save path always creates, including on a row's very first save). Applying
`000046` (via the standard schema migration — the chart's `migrate` init
container runs it automatically) is what makes those rows read as
`bundled_superseded` rather than a divergence error during migration, and
lets the PostgreSQL-path reseed keep updating them as the package evolves.

Separately, PR #1012 itself restructured the bundled `Research Campaign`
workflow's graph without bumping its `version` — a bundled-content-changed-
under-the-same-version defect independent of `000046`, since it would trip
`seed_system_workflows`'s own "changed content; publish it under a new
version" check even on a fresh install. It now ships as version `2.0.0`. A
[test lock file](../../tests/test_ting/data/bundled_workflow_versions.lock.json)
(`tests/test_ting/test_bundled_workflow_versions_lock.py`) now fails CI if a
bundled workflow's content ever changes again without its `version` moving —
see that test file's module docstring for how to add a new released version.

## Automated GitOps cutover (Helm)

Running `python -m ting.migrate_workflows --apply` by hand does not fit a
GitOps workflow driven entirely by values changes. `workflowMigration.applyOnStart`
runs it automatically, from a second init container (`workflow-catalog-migrate`)
that runs after the schema (`migrate`) init container and before the main
container starts. It passes `--skip-if-migrated`, so once an operator's first
migration has produced a verified marker, every later pod start is a no-op —
the container checks PostgreSQL's row count and the file catalog's marker,
prints a `{"skipped": true, "reason": "..."}` report, and exits 0 without
touching the filesystem. This makes it safe to leave `applyOnStart: true`
indefinitely rather than flipping it off after the first successful rollout.

Because the migration needs workflow-definition writes frozen while it runs,
the chart requires the filesystem adapter, `workflowPersistence.enabled`, and
the `Recreate` strategy (automatic on an RWO volume, or set explicitly) before
it will render the init container — those conditions guarantee no old Ting
pod is still serving writes when the new pod's init container migrates.
`replicaCount` can be greater than 1 under `Recreate` (on `ReadWriteMany`
storage) — that no longer requires a single replica. With N replicas, N
copies of the init container start together and each runs
`--skip-if-migrated --apply`; `ting.migrate_workflows` holds
`FilesystemWorkflowRepository.cutover_lock()` — a dedicated lock file in the
catalog, distinct from the catalog's own read/write lock so the two don't
self-deadlock — around the whole check-then-apply span, so exactly one of
them performs the migration and the rest see the marker it just wrote and
skip. If migration errors (a divergent packaged row, a missing persona, …),
the init container exits non-zero and the pod fails to start — loud, not
swallowed; Kubernetes will keep retrying, and the previous pod (already gone
under `Recreate`) is not there to fall back to, so resolve the reported
conflict and roll a new values change forward.

For an **existing installation** currently on
`PostgresWorkflowRepository`, cut over in two upgrades so a mid-rollout pod is
never running with the wrong catalog:

1. **Provision storage, stay on PostgreSQL.** Upgrade with
   `workflowPersistence.enabled: true` (provisioning the PVC and mounting it)
   while keeping `workflowRepository.adapter` on
   `ting.adapters.postgres_workflows.PostgresWorkflowRepository` and
   `workflowRepository.seedBundled: true`. This just gets the new image and
   volume live; the new image's bundled workflows re-seed their PostgreSQL
   system rows on this pass, same as any other upgrade, so package updates
   aren't lost in the process.
2. **Switch to the file catalog.** Upgrade again with
   `workflowRepository.adapter: ting.adapters.filesystem_workflows.FilesystemWorkflowRepository`,
   `workflowRepository.seedBundled: false`, and
   `workflowMigration.applyOnStart: true`. The init container performs the
   verified migration once (the same inventory/validation `--apply` always
   does), writes the completion marker, and the main container's own startup
   guard (`ting.main._assert_workflow_catalog_migrated`) then starts normally
   against the file catalog. Every subsequent rollout's init container sees
   the current marker and skips.

**Rollback**: revert `workflowRepository.adapter` to
`ting.adapters.postgres_workflows.PostgresWorkflowRepository` with
`seed_bundled: true` (the PostgreSQL rows were never deleted by the
migration) and set `workflowMigration.applyOnStart: false`. As with the
manual rollback above, reconcile any workflow edits made through the file
catalog back into PostgreSQL first — the migration is one-way and PostgreSQL
is not kept in sync after cutover.

### Cutting over a multi-replica (`ReadWriteMany`) cluster

The single-replica path above (RWO, `Recreate` forced automatically) is the
primary supported one today. A cluster that already runs Ting at
`replicaCount > 1` on `ReadWriteMany` storage cuts over the same two-phase
way, with an explicit `strategy` change bracketing the second phase instead
of relying on the RWO auto-`Recreate`:

1. **Upgrade on PostgreSQL with the RWX claim mounted** — same as phase 1
   above (`workflowPersistence.enabled: true`, adapter still
   `PostgresWorkflowRepository`, `seedBundled: true`), at the existing
   `replicaCount`.
2. **Flip the catalog with `strategy.type: Recreate` and
   `workflowMigration.applyOnStart: true`** — `replicaCount` does not need to
   drop to 1; N init containers race for `cutover_lock()` and only one
   migrates. `strategy.type: Recreate` is still required for this upgrade
   specifically so no pod from the previous (PostgreSQL) rollout generation
   is still running while the new generation's init containers migrate.
3. **Afterwards, go back to `RollingUpdate`** (unset `strategy`, or set it
   explicitly) **and scale back to the normal `replicaCount`.**
   `workflowMigration.applyOnStart` can stay `true` indefinitely — it no-ops
   via `--skip-if-migrated` on every later rollout.

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

The Helm chart's relevant values:

```yaml
podSecurityContext:
  fsGroup: 65532                     # matches the niuu image's runtime user
  fsGroupChangePolicy: OnRootMismatch

strategy: {}                         # {} = automatic; see "single-node storage" above

workflowPersistence:
  enabled: true
  accessModes:
    - ReadWriteMany                  # set to [ReadWriteOnce] for single-node storage

workflowMigration:
  applyOnStart: false                # true = automated cutover init container
  personaDatabase: volundr           # reuses Ting's own DB credentials, just a different database name
  replaceDivergentBundled: false
  resources: {}
```

The `workflow-catalog-migrate` init container also receives `extraEnv`, same as
the main container, so anything the migration needs beyond `DATABASE__*` (e.g.
observability exporter settings) can be supplied the same way.
