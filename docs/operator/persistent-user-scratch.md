# Persistent user scratch on Kubernetes

With persistent homes enabled, the storage contributor enables PVC-backed `/tmp`
for Kubernetes session runtimes. The home PVC belongs to the session owner; it is
retained when a session stops or is deleted. OpenShell does not yet mount user
home PVCs and is not covered by this feature.

Each runtime container mounts `tmp/sessions/<session-id>/<container-name>` from
the user's home at `/tmp`. This catches explicit `/tmp` writes as well as tools
using `TMPDIR`. Concurrent sessions and agents get separate temporary directories;
restarting the same session preserves its temporary files. Do not use retained
`/tmp` files as evidence that a previous process is still running.

All of that user's sessions mount `tmp/cache` at `/var/cache/niuu`. Environment
variables point Go build/module, npm, pip, uv and XDG caches there. These tools
manage their own cache concurrency. A command that explicitly overrides these
variables still writes to its isolated, persistent `/tmp` if it names `/tmp`.
Files in a session's temp directory are not automatically promoted into the
shared cache. Users on different claims cannot share these caches.

## Capacity and scheduling

Configure the Kubernetes storage adapter through its existing kwargs:

```yaml
storageAdapter:
  adapter: volundr.adapters.outbound.k8s_storage_adapter.K8sStorageAdapter
  kwargs:
    namespace: skuld
    home_storage_class: harvester-single-replica
    home_access_mode: ReadWriteOnce
    home_size_gb: 64
    workspace_storage_class: harvester-single-replica
    workspace_size_gb: 20
```

These class names are specific to the Valhalla installation. Other installations
must select their own data-backed classes. `home_size_gb` is the minimum requested
home capacity; onboarding quotas can request more. Provisioning expands an
existing smaller home claim and never shrinks it. The class must support expansion,
and the backend needs PVC patch permission. Expansion failures stop provisioning.
Existing workspaces keep their current capacity.

The home, scratch and caches share the home PVC's capacity. A full home claim
requires deleting unneeded data or increasing `home_size_gb`. This does not remove
all node disk usage: images, logs and unmounted container paths still need node
capacity. RWO homes require a user's simultaneous session pods to run on the same
node; use a suitable RWX class if they must run across nodes.

Direct chart installations opt in with `homeVolume.persistentTmp: true`, an
existing sufficiently sized home claim and a valid `session.id`. The Volundr
storage contributor enables it by default with homes; set its `persistent_tmp`
kwarg to false to explicitly disable it. New mounts take effect when session
runtime deployments are recreated, not by merely restarting a container.

## User management

Open **Settings → Storage → Home & temporary files** and select a cluster.
Home browses the user's home volume, Temporary files opens `tmp/sessions`, and
Caches opens `tmp/cache`. The page shows filesystem capacity and free space,
folder navigation and confirmed deletion. No coding session is required.
Files stay on their selected cluster; this is not cross-cluster synchronization.

Enable Kubernetes file access with storage adapter kwargs `file_browser_image`
(a pinned Skuld image with Python), `file_browser_lifetime_seconds` (default 600)
and `file_browser_timeout_seconds` (default 20). The chart grants pod lifecycle
and exec access to the storage adapter. The backend mounts only the authenticated
user's existing home in a non-root helper pod, without a service-account token.
It expands smaller existing homes to `home_size_gb` when accessed. Unprovisioned
homes and unsupported storage adapters are reported explicitly.

The helper exits after its configured lifetime. Its next use replaces the expired
pod. Deprovisioning also removes the helper. File contents are not sent to pod
logs. Paths cannot leave the home, and symlinks cannot be followed. DELETE rejects
requests while other Pending or Running pods use the home; stop those sessions
first. Deleting a folder permanently removes its contents. Retained temp data is
not automatically removed when sessions are deleted; new sessions recreate
missing scratch/cache directories.

The shared gateway routes requests to the explicitly selected, visible cluster,
preserving the user's authentication. Local filesystem storage implements the
same operations. OpenShell home mounts remain unsupported and are reported as
such; they do not silently use another cluster's home.
