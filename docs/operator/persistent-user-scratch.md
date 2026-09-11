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

Open a running session's Files view, select **Home**, and open **tmp**:

- `sessions/<session-id>/<container-name>` contains retained temporary files.
- `cache` contains reusable tool caches shared by the user's sessions.

The existing file manager supports browsing, downloading and deleting these files
and directories. Stop the sessions using a directory before deleting it; stop all
of your sessions that use shared caches before clearing `cache`. A separate
management session can browse Home while other sessions are stopped. Do not
remove the `tmp` mount directories of that management session. No temp data is
removed automatically on session deletion. The next session start recreates any
missing scratch/cache directories.

The Home file API uses the configured persistent home mount. Its existing path
containment checks also apply to scratch, including symlinks that point outside
that user's home. Account storage deprovisioning removes the home claim, including
retained scratch and caches.
