# Resident runtimes

A **resident** is a long-lived, named agent that Forge keeps running for its owner
and that has its own chat sessions ("channels"). Residents are **not Forge
sessions**: Forge persists each one in `resident_runtimes` with a global UUID,
owner, tenant, deployment profile, backend, engine, desired and observed state,
backend reference, endpoints, capabilities and conditions, and drives it through
`/api/v1/forge/resident-runtimes`.

A resident has an **engine** (the agent it runs) and a **backend** (the
infrastructure that hosts it):

| Engine | Meaning |
|---|---|
| `openclaw` | An OpenClaw gateway (NemoClaw image). Sessions are driven through the gateway protocol. |
| `hermes` | A Hermes API server (NemoHermes image). |

The `ravn` engine was removed. A profile configured with `engine: ravn` makes
Forge refuse to start with `the ravn resident engine was removed; set this
profile's engine to openclaw or hermes, or delete the profile`. Resident records
that still carried the engine were deleted by migration `000111`; their workloads,
volumes and credentials are not touched by it and have to be removed in their
backend.

| Backend | Hosted by |
|---|---|
| `local` | `LocalContainerResidentRuntimeController`: the local Docker Engine. Docker must be running; when it is not, Forge refuses to start the controller with the remedy in the error instead of running without residents. |
| `openshell` | `OpenShellGatewayPodManager`: one native OpenShell sandbox per resident. |

`helmrelease` and `discovered` remain valid values of the backend enum, but no
controller implements either now, so a profile naming one is not deployable.

## Profiles

Available deployment profiles are operator configuration, not request input.
Configure `residentRuntimeProfiles` in the Volundr chart; disabled profiles and
the backend-only `deployment` data are not returned by
`GET /api/v1/forge/resident-profiles`. No profiles are enabled by default. A
create request names a `profileId`; one that is not configured and supported on
this target is refused with a 422.

A profile for `openclaw` or `hermes` declares the engine's own process in
`deployment.values` (`processMode: replace` and at least one entry in `processes`),
the service port, and the image where the backend needs one. The chart's
`values.yaml` carries a commented example.

On OpenShell a resident is created without a Forge Session row or a parallel
OpenShell client: Forge creates one sandbox, attaches owner-bound dynamic
credential providers, starts the declared processes and exposes the service
through the gateway. OpenShell profiles support chat, restart, gateway logs and
usage reporting; they must not advertise suspend or metrics.

### Local runtime

`LocalContainerResidentRuntimeController` uses the Docker API to run a resident
image. Each resident gets a private loopback-only port and durable directories
under `<residents_dir>/<resident-id>/sandbox/`: `workspace`, `config` (the
container's `.volundr`), and `home` with `.codex` and `.claude`. Existing Codex
and Claude credential files are copied with mode `0600` only when the resident's
durable destination does not exist. Restart and suspend preserve these
directories; deleting the resident removes its container, durable directory and
engine machine credential unless retention is configured.

## Upgrades follow the platform

A resident runs the version its Forge deploys now, not the one it was created
with. When Forge is deployed with a newer sandbox image, reconciliation moves each
running OpenShell resident to it once and logs `Upgrading resident <name> from
<old> to <new>`. The running image is in the resident's `backendRef`.

A sandbox's image is fixed at creation, so the sandbox is recreated on the new
image and the resident keeps its record and credentials. This happens only when
its sandbox keeps the workspace on a claim: its own (below), or the profile's
`persistence.existingClaim` with a `mountPath` at or above the sandbox workspace.
Without one (the profile sets `persistence.enabled: false`, or Forge manages no
claims), nothing ever recreates the sandbox: the upgrade is held with a
`PlatformUpgrade` / `UpgradeHeld` condition, shown in the resident's conditions,
and Restart restarts its processes in place.

- A failed upgrade marks the resident failed with a `PlatformUpgrade` /
  `UpgradeFailed` condition and is not retried until the version changes again,
  the resident is restarted, or Forge restarts.
- An old sandbox that is slow to go is not a failed upgrade. Every removal of a
  resident sandbox waits `resident_sandbox_delete_timeout_seconds` (180s;
  sessions keep `resource_delete_timeout`, 120s). If the sandbox is still being
  deleted after that, the resident reports `PlatformUpgrade` / `Upgrading` and
  its redeployment waits for the sandbox to go, then recreates it on its
  workspace claim, retrying on later passes. If the gateway refuses the delete,
  the resident keeps running on its old sandbox (`PlatformUpgrade` /
  `UpgradeRetrying`) and the next pass tries again.

## OpenShell residents' workspaces

Every OpenShell resident gets its own PVC, `resident-<id>-workspace`, mounted at
`/sandbox/workspace`. Forge creates it on deploy and deletes it with the resident,
in the namespace where the OpenShell gateway runs sandboxes
(`podManager.kwargs.resident_workspace_namespace`, which the chart defaults to
`storageAdapter.kwargs.namespace`; the chart grants Forge the PVC rights there
with the `<release>-resident-workspaces` Role). Size, storage class and access
mode come from `resident_workspace_size`, `resident_storage_class` and
`resident_access_mode`, and a profile overrides them in
`deployment.volumes.workspace`. The claim holds the engine's state; the rest of
`/sandbox` (rendered config, logs, the image's tools, the Codex CLI home) is
rebuilt with each sandbox.

The claim is ReadWriteOnce and carries no node affinity of its own: a sandbox
mounting it is scheduled wherever Kubernetes places it, and the volume attaches to
that node. A recreated sandbox is created only once the old one is gone, so the
volume is free to move. A storage class whose volumes are node-local binds the
resident to that node.

A resident whose sandbox predates its claim is moved onto it once, by its next
deployment (reconcile hands it over with a `WorkspaceMigration` / `Migrating`
condition) or its next Restart. Forge logs `Migrating resident <name>'s workspace
to a persistent volume` and:

1. measures the workspace; more than `resident_workspace_migration_max_size`
   (default 1Gi) or the claim's size is refused before anything changes;
2. creates the claim and takes the migration's lease on it;
3. stops the resident's processes and archives its state out through the sandbox
   exec channel;
4. creates a new sandbox on the current image with the claim mounted, restores the
   archive into it before any resident process starts, and reads it back: every
   path, size and SHA-256 must match;
5. only then deletes the old sandbox and starts the resident on its claim.

Until step 5 the old sandbox is never touched beyond stopping its processes. If
anything fails, the new sandbox is removed, the old one's processes are started
again, and the resident reports `WorkspaceMigration` with the reason
(`WorkspaceTooLarge`, `VerificationFailed`, `ClaimInUse`, `MigrationFailed`); it
is retried on Restart or when Forge restarts. Progress is kept on the claim's
annotations (`volundr.niuu.io/workspace-migration`: `copying`, `verified`,
`complete`, with the holding Forge and its lease), so a Forge that restarts
mid-copy, or a second replica, resumes or waits rather than starting over. A claim
that already holds a workspace is never overwritten.

## Ownership

A resident is owned by the user that created it and belongs to that user's tenant.
Only its owner or an admin sees and controls it through the resident-runtime API,
and creating or changing one needs the `volundr:developer` role or admin.
