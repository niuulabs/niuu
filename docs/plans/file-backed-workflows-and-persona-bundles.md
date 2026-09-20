# File-backed workflows and persona bundles

Status: proposed implementation plan.

## Outcome

Make workflow definitions independently editable and shareable as YAML files,
one file per workflow. Keep the visual builder as an editor of those same
definitions. Support exporting either a workflow alone or a bundle containing
its persona dependencies. Keep execution records in PostgreSQL.

This plan covers workflow storage, persona dependency resolution, import/export,
the builder, deployment, migration, and verification. It does not introduce a
marketplace, Git synchronization service, or move all persona registries and
execution data out of the database.

## Current implementation

- `src/ting/system_workflows.yaml` contains the bundled workflows in one list.
  `src/ting/system_workflows.py` assigns deterministic UUIDs and seeds that list
  into PostgreSQL during startup in `src/ting/main.py`.
- `WorkflowRepository` already separates workflow consumers from persistence.
  `PostgresWorkflowRepository` supplies CRUD; an authorization wrapper enforces
  resource permissions and immutable ownership.
- Launches and saga assignments build workflow snapshots. The existing snapshot
  contains the graph and persona references, but does not pin complete persona
  definitions. Pinning the workflow alone therefore does not pin its behavior.
- Ravn already provides individual persona YAML files, `PersonaPort`,
  `PersonaRegistryPort`, and filesystem/HTTP/database adapters. Persona references
  used by workflow stages are currently names, without a revision contract.
- The builder's YAML view uses a separate handwritten serializer. It is not a
  complete round-trip representation of the stored definition.

## Design decisions

### One definition per file

Use a configured catalog directory with individual files, for example:

```text
workflows/
  code-review.yaml
  research-campaign.yaml
  saga-planning.yaml
```

Each document contains a schema version, stable workflow UUID, name, description,
workflow version, persona dependencies, and the full graph. Preserve all existing
graph semantics, including stage overrides, gates, resources, event bindings,
artifact paths, and editor positions. Renaming a file or display name must not
change the workflow's identity. Schema version and workflow version are distinct.

Use the same schema for file loading, builder persistence, and YAML export.
Reject unsupported schema versions, duplicate identities, malformed documents,
and invalid references with actionable errors. Do not silently skip bad files
or substitute a database definition when file loading fails.

Keep owner, tenant, access scope, and local timestamps in server-managed catalog
metadata outside the portable document. A downloaded file cannot grant system
scope or claim ownership. Metadata and definitions must be published consistently.

Bundled workflows become individual packaged files and are read directly.
Treat those files as read-only: editing a bundled workflow creates an explicit
local copy with a new identity. Local editable system workflows remain possible
for authorized administrators. Do not implement implicit same-name shadowing.

### Explicit persona dependencies

Declare each required persona once, keyed by a workflow-local alias. Stage members
reference that alias. Each dependency records a stable persona identifier,
revision, and canonical content digest. Existing `personaId` stage values can
serve as aliases during conversion, avoiding an unnecessary graph rewrite.

Ravn owns persona schemas and resolution semantics. Extend its existing ports
for portable source definitions and exact dependency resolution; do not duplicate
persona parsing inside Ting. Move the relevant shared persona data contracts out
of the concrete loader where necessary to preserve hexagonal boundaries.

Define canonical persona serialization before adding digests. Hash the portable
definition, excluding local metadata and derived runtime prompt injection.
Versions are labels; the digest identifies the exact content. Existing personas
need a defined initial revision during migration, with identity retained and a
digest computed from their canonical source definition.

Separate persona instructions, tool/capability requirements, event contracts,
and portable execution settings from environment-specific bindings. Never export
credential values. Model/provider choices, connections, filesystem locations,
and installed tools must be represented as requirements or explicit local
bindings where they are environment-specific. Preserve portable stage overrides.

Keep referenced persona revisions immutable. Editing a persona creates a new
revision; updating a workflow to use it is explicit. Replacing a current catalog
entry must not change an already pinned revision or an existing run.

### Export formats

1. **Workflow YAML:** a single workflow document containing pinned persona
   references and environment requirements.
2. **Workflow bundle:** a ZIP archive with the same document and its portable
   persona definitions:

   ```text
   code-review/
     workflow.yaml
     personas/
       coder.yaml
       reviewer.yaml
   ```

The workflow dependency declarations identify included persona files and digests;
a separate bundle manifest is unnecessary for the initial one-workflow format.
Bundle paths are relative and validated. Export source definitions, not expanded
runtime prompts. Detect unsupported nonportable dependencies and report them;
do not label an incomplete bundle self-contained. External tools, skills, and
integrations are declared requirements in this first version, not automatically
packaged executable content.

### Import and conflict resolution

Import has a read-only preview followed by an explicit apply operation:

| Situation | Behavior |
| --- | --- |
| Required persona revision and digest already exist | Reuse it |
| Persona is missing and included in the bundle | Propose installing that revision |
| Persona is missing from a standalone workflow import | Require a local mapping or a supplied definition |
| Same persona identifier/revision has different content | Report conflict; offer mapping, importing under a new identity, or an authorized explicit replacement of the current entry |
| Workflow identity already exists | Offer importing as a new workflow or explicitly updating the existing authorized workflow |
| Required local binding is unavailable | Permit saving an explicitly unresolved draft; block launch until resolved |

Mapping to different persona content is an intentional adaptation: rewrite the
dependency pin to the selected revision and digest and show that change in the
preview. Check consumed/produced events and capability compatibility; matching
names alone do not establish compatibility. Renaming an imported persona must
update its dependency mapping without breaking stage references.

Validate permissions for both workflows and personas. Import into the caller's
authorized tenant and user scope by default. Do not overwrite a global persona
as a side effect of importing a user workflow. Display instructions, requested
capabilities, and binding changes before applying; importing does not execute
the workflow, install arbitrary tools, or activate embedded adapter code.

Validate the complete import before writing. Stage files and publish the workflow
only after dependencies are durable. Recheck content versions and permissions at
apply time so an outdated preview cannot overwrite concurrent changes. Define
recovery for interrupted imports across workflow and persona stores; report any
partial dependency installation without exposing a runnable incomplete workflow.

### Execution and persistence

Keep sagas, campaigns, runs, approvals, and execution history in PostgreSQL.
At launch, resolve exact persona dependencies and local bindings, then capture
the workflow, resolved persona definitions/digests, and nonsecret binding
references in the execution snapshot. Runtime delivery must use those resolved
definitions instead of looking up mutable persona names again.

Preserve Ravn's ownership of persona behavior and execution decisions, Skuld's
runtime/session delivery role, and Niuu's shared infrastructure role. Do not put
persona judgment in the catalog or import service.

Implement file storage behind `WorkflowRepository` using the existing dynamic
`adapter` plus kwargs configuration pattern. Preserve authorization behavior.
Use atomic writes, concurrent-edit detection, and safe stable paths. Choose a
cross-process locking/publication mechanism supported by the documented shared
filesystem; an in-process lock is insufficient for multiple workers.

All workers must see the same committed catalog. Read-only configuration mounts
can supply definitions, but builder writes require a durable writable catalog.
Local, container, and Kubernetes configurations must specify their storage paths
and persistence. Do not use an ephemeral container directory as the default
durable store. Missing or unwritable configured storage must fail explicitly.

## Implementation sequence

### 1. Define and verify the portable contracts

- Add versioned workflow documents, persona dependency records, local metadata,
  and import preview/result contracts in the appropriate domain layers.
- Establish lossless canonical persona serialization and digest calculation.
- Specify which existing persona/workflow fields are portable and how local
  bindings are represented. Audit the full current persona schema rather than
  relying only on the fields exposed by the existing persona REST response.
- Add fixtures for current bundled workflows/personas and round-trip tests,
  including multiline prompts and all existing graph fields.

Exit: current definitions can be represented without losing behavior, with clear
errors for unsupported input. Publish the schema and a documented real example.

### 2. Implement persona dependency resolution and runtime delivery

- Extend Ravn-owned persona ports/adapters for exact revisions and portable
  definitions; add a scoped installation path for imported dependencies.
- Add dependency inspection, digest comparison, mapping, and compatibility checks.
- Wire resolution through ports at composition roots.
- Extend launch snapshots and runtime delivery so pinned personas reach the actual
  Ravn session in both supported local and remote execution paths.
- Block unresolved dependencies before creating a session or dispatching work.

Exit: a launched workflow runs with the selected persona content even if the
catalog's current persona changes afterward.

### 3. Implement file-backed workflow storage

- Add the filesystem adapter with atomic CRUD, metadata, ownership preservation,
  consistent reads, and concurrent-edit protection.
- Split the seven current bundled workflows into one file each, preserving their
  existing deterministic UUIDs, graph content, and default workflow references.
- Add settings and wiring in Ting; remove startup seeding when the file catalog
  is selected. Retain the database adapter for explicit migration/rollback only.
- Update package/build assets and local/container/Helm storage configuration.
- Make catalog reload behavior explicit: validated file changes affect subsequent
  reads and launches, while running snapshots remain unchanged. Surface invalid
  edits instead of silently serving stale content.

Exit: list, view, create, edit, delete, assignment, and launch work against files
through the existing authorization boundary, including across restarts/workers.

### 4. Implement import/export services and APIs

- Add workflow-only and bundled exports using the canonical serializers.
- Add preview/apply endpoints with dependency resolution, explicit collision
  choices, local binding requirements, and concurrent-change checks.
- Bound archive size, expanded size, and entry counts through configuration;
  reject traversal paths, symlinks, duplicate entries, unsafe YAML, and mismatched
  digests. Never extract unchecked paths into the live catalog.
- Implement staged publication and interrupted-import recovery.

Exit: export from one instance and import into another preserves workflow/persona
content, assigns correct local ownership, and reports every unresolved requirement.

### 5. Integrate the workflow builder

- Replace the incomplete YAML serializer with the canonical document contract.
- Keep graph/pipeline editing and preserve layout on save/import/export.
- Add export choices and import preview with persona conflicts and binding mapping.
- Show persona revisions, unresolved dependencies, and explicit dependency updates.
- Explain read-only bundled definitions and provide an edit-as-copy action.
- Handle concurrent saves with a visible conflict instead of last-write-wins loss.

Exit: users can complete the entire sharing workflow from the UI and edit the
result without losing fields. Existing launch and permission behavior still works.

### 6. Migrate existing installations and cut over

- Provide an operator migration command with dry-run and apply modes. Inventory
  database definitions, ownership, IDs, saga references, and persona dependencies.
- Export user and administrator-created definitions to files, preserving IDs and
  ownership. Compare system rows with packaged definitions; preserve divergent
  copies rather than silently discarding edits.
- Resolve and pin personas from the installation's authoritative registry. Report
  missing personas and binding requirements rather than guessing replacements.
- Make reruns idempotent; refuse conflicting destination content. Verify counts,
  identities, ownership, canonical content, and references before cutover.
- Freeze definition writes during final migration, then explicitly select the
  file adapter. Do not run ongoing bidirectional database/file synchronization.
- Keep old tables intact for the transition. Document that switching back after
  new file edits requires reconciling those edits; stale database rows are not a
  complete rollback. Do not drop workflow tables in the initial rollout.
- Preserve historical run snapshots as recorded; do not retroactively invent
  persona pins for old runs. Document the historical reproducibility limitation.

Exit: an existing installation retains its definitions and references, no longer
seeds the catalog into PostgreSQL, and has a verified recovery procedure.

## Verification and completion criteria

- Round-trip every bundled workflow and persona without semantic field loss.
- Exercise missing dependencies, exact reuse, conflicting content, alias rewrites,
  incompatible mappings, unsupported schema versions, and unresolved bindings.
- Verify tenant/owner isolation for CRUD, export, import, and persona installation.
- Test atomic publication, interrupted imports, concurrent workers/edits, external
  file changes, unavailable storage, and read-only mounts.
- Verify imports never export secrets or execute bundled capabilities implicitly.
- Prove launch and resume use pinned persona content after file/catalog edits.
- Test migration reruns, divergent system rows, missing personas, and preservation
  of saga references and historical records.
- Run affected backend and web suites and existing coverage gates. Verify package
  inclusion and deployment rendering, then exercise a real persisted catalog and
  export/import/launch flow in two clean instances.

Deliver in the sequence above, with file-backed storage remaining an explicit
configuration choice until migration, deployment persistence, and runtime
dependency delivery are verified. The completed rollout makes file-backed
definitions the default without silently abandoning existing database catalogs.
