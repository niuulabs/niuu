# VM compute allocations

The generic VM backend supports Forge/Skuld sessions and an infrastructure-only
operator CLI. There is no warm pool. Root disks are disposable: infrastructure
release deletes the VM, disk and cloud-init Secret. Forge stop preserves session
workspace/home to the configured controller-local archive before release.

## Provider and authentication configuration

The CLI reads an explicit YAML file through `volundr.compute.config.ComputeConfig`.
Keep this operator configuration and its credentials access restricted.

| Field | Meaning |
| --- | --- |
| `pool_id` | Stable pool identity, unique within the database. |
| `max_machines` | Positive limit; provisioning, failed and draining claims count. |
| `database` | Existing PostgreSQL settings: `host`, `port`, `name`, `user`, `password`. Apply migration 000066 using the normal migration mechanism first. |
| `provider.adapter` | `volundr.adapters.outbound.harvester.HarvesterMachineProvider`, or an installed class implementing `MachineProvider`. |
| `provider.kwargs` | Provider-specific mapping described below. |
| `auth.adapter` | Existing `HttpAuthPort` implementation. |
| `auth.kwargs` | Non-secret authentication parameters, such as a token-file path. |
| `auth.secret_kwargs_env` | Existing mapping of constructor argument names to secret environment-variable names. |
| `bootstrap` | Optional portable `files`, `commands` (argument arrays), and `ssh_authorized_keys`. File fields are `path`, `content`, and octal `permissions`. |
| `provisioning_timeout_seconds` | Whole allocation/readiness deadline, default 600. |
| `cleanup_timeout_seconds` | Deletion confirmation deadline, default 300. |
| `poll_interval_seconds` | Provider polling interval, default 2. |

Harvester provider kwargs:

| Field | Meaning |
| --- | --- |
| `base_url` | Actual HTTPS cluster API origin, or trusted Rancher proxy prefix. |
| `namespace` | Dedicated namespace for this installation's VMs and supporting resources. |
| `installation_id` | Stable DNS label identifying resources owned by this installation. |
| `ca_file` | Optional trusted CA PEM file. Omit for the system trust store. Certificate verification cannot be disabled. |
| `profiles` | Mapping of operator-chosen profile names to infrastructure settings. |
| `cloud_init` | Optional JSON-compatible cloud-init mapping applied to every profile. |
| `timeout_seconds` | Timeout for each API request, default 30. |
| `page_size` | Inventory page size, default 100. |

Each profile requires `image` and `network` in `namespace/name` form, positive
`cpu`, `memory_mib`, and `disk_gib`. Optional `architecture` is `amd64` or `arm64`
(default `amd64`); `access_mode` is `ReadWriteMany` or `ReadWriteOnce`
(default `ReadWriteMany`). The image must already be imported and have an image
storage class. Disk size must accommodate it. The adapter does not import images
or create networks. Use a cloud-init-capable image with a running QEMU guest agent
and network configuration appropriate for that network.

The Vanaheim installation resolves the `niu-10gb-1` cluster network to the
`asgard/asgard-10gb-1` network attachment (VLAN 90). Its provider configuration is:

```yaml
provider:
  adapter: volundr.adapters.outbound.harvester.HarvesterMachineProvider
  kwargs:
    base_url: https://harvester.vanaheim.niuu.world
    namespace: asgard
    installation_id: niuu-compute-live-proof
    profiles:
      ubuntu-proof:
        image: ginnungagap/ubuntu-24.04-server-cloudimg-amd64
        network: asgard/asgard-10gb-1
        cpu: 2
        memory_mib: 2048
        disk_gib: 8
```

Use the API origin as `base_url`, without the management API's `/v3` suffix:
this adapter calls `/api/v1` and `/apis/...` on that origin. Authentication remains
in the separate `auth` configuration. The proof's installation identity should
remain distinct from a production pool's identity.

For a mounted service-account token, select
`niuu.adapters.outbound.http_auth.FileBearerTokenAuthAdapter` and set
`auth.kwargs.token_file` to its actual path. The file is reread for every request,
including one credential refresh after HTTP 401. Empty tokens fail. The existing
static bearer and OAuth client-credentials adapters are also available. OAuth
requires an API endpoint that actually accepts that issuer and audience; choosing
an OAuth adapter does not configure Harvester trust.

Provider credentials are separate from future guest workload identity. They are
never added to VM bootstrap. TLS failures, denied requests and redirects fail;
the adapter does not switch credentials, providers or endpoints on failure.

## API permissions

Use the deployment's standard identity mechanism and a dedicated namespace.
Grant these permissions to the provider principal:

| API group / resource | Verbs | Scope |
| --- | --- | --- |
| `kubevirt.io` / `virtualmachines` | `get`, `list`, `create`, `delete` | Managed namespace |
| `kubevirt.io` / `virtualmachineinstances` | `get` | Managed namespace |
| core / `persistentvolumeclaims`, `secrets` | `get`, `list`, `create`, `delete` | Managed namespace |
| `harvesterhci.io` / `virtualmachineimages` | `get` | Configured image namespace/name |
| `k8s.cni.cncf.io` / `network-attachment-definitions` | `get` | Configured network namespace/name |

Use resource-name restrictions for image and network reads where supported.
Owned-resource labels prevent accidental adoption/deletion, but labels are not
an authorization boundary: namespace RBAC is. Secret-list permission exposes
namespace Secret contents to that principal, so do not mix unrelated credentials
into the managed namespace. Configure namespace quotas and network isolation in
the cluster as well as the application's claim limit.

The adapter uses the documented [Harvester HTTP APIs](https://docs.harvesterhci.io/v1.8/api/harvester-apis/),
including [VM creation](https://docs.harvesterhci.io/v1.8/api/create-namespaced-virtual-machine/)
and [PVC creation](https://docs.harvesterhci.io/v1.8/api/create-namespaced-persistent-volume-claim/).
The deployed Harvester version must still be checked with a real allocation.

## Run and interpret a lifecycle proof

Set the shell variables below to your actual approved configuration file,
profile, owner and tenant. The command creates one allocation and always attempts
to delete it, including after a failed or timed-out create.

```sh
uv run python -m volundr.compute.main --config "$COMPUTE_CONFIG" inventory
uv run python -m volundr.compute.main --config "$COMPUTE_CONFIG" prove \
  --profile "$COMPUTE_PROFILE" --owner-id "$COMPUTE_OWNER" --tenant-id "$COMPUTE_TENANT"
```

The proof generates a new session UUID and prints it before provisioning. JSON
events include `allocation_created`, `vm_ready`, `cleanup_started`,
`cleanup_verified`, and finally `proof_passed`. Readiness requires an observed
running VM and guest IP; it does not check network reachability, SSH, cloud-init
completion, Skuld authentication, agent execution, or retained session storage.
Only a complete successful sequence is VM lifecycle proof. Fake-transport tests
are not live proof.

If the process exits or deletion times out, inspect the durable record and resume
the same allocation. Set `COMPUTE_LEASE_ID` to the reported allocation UUID:

```sh
uv run python -m volundr.compute.main --config "$COMPUTE_CONFIG" leases
uv run python -m volundr.compute.main --config "$COMPUTE_CONFIG" reconcile --lease-id "$COMPUTE_LEASE_ID"
uv run python -m volundr.compute.main --config "$COMPUTE_CONFIG" release --lease-id "$COMPUTE_LEASE_ID"
```

`release` requests deletion; repeat `reconcile` until state is `released`.
A failed record still consumes capacity. A busy-operation error means another
controller owns that allocation; retry after it finishes. Never change a pool's
provider, endpoint, namespace, installation identity or profile mapping while
it has live claims. Create a new pool for a new infrastructure mapping.

Admission uses a short PostgreSQL transaction. Provider operations hold one
connection-level advisory lock, without holding a database transaction. Process
or connection loss releases that lock. This is not fencing against a late remote
API request after connection loss. A continuously running inventory reconciler
and stronger crash-window handling remain required before unattended rollout.
This CLI performs explicit reconciliation only; it does not run a background
controller or automatically delete unknown inventory.

## Forge sessions on local disk

Configure the root Settings `compute` section with the provider/auth fields above,
plus `runtime` below. Forge uses its existing database; omit `compute.database`.
Use the existing credential-store adapter to persist per-allocation bootstrap
material. It includes private guest host keys and session environment values:
protect this store as credentials, not ordinary configuration.

```yaml
pod_manager:
  adapter: volundr.adapters.outbound.vm_pod_manager.VmPodManager
  runtime_backend: vm
  kwargs:
    pool_id: asgard-forge
    max_machines: 1
    profile: ubuntu-skuld
    server_port: 8088
    provisioning_timeout_seconds: 900
compute:
  pool_id: asgard-forge
  max_machines: 1
  # Include provider and auth mappings from above.
  runtime:
    adapter: volundr.adapters.outbound.ssh_vm_runtime.SshContainerVmRuntime
    kwargs:
      ssh_private_key_file: /secure/niuu/ssh_key
      ssh_public_key_file: /secure/niuu/ssh_key.pub
      data_dir: /var/lib/niuu/vm-sessions
      skuld_image: ghcr.io/niuulabs/skuld:dev
      platform_port: 8088
      guest_platform_port: 18088
      command_timeout_seconds: 900
```

Generate a real SSH client keypair and configure its paths. The guest must have
OpenSSH, passwordless sudo for `ssh_user` (default `ubuntu`), Python 3.12+, Git,
Docker and the QEMU guest agent. The controller needs OpenSSH and network access
to the guest SSH port. Each start pulls the configured Skuld image; an existing
container keeps its original image until a fresh allocation is started.

Both provider kwargs and individual profiles accept a `cloud_init` mapping.
Profile keys override provider defaults, except `packages`, `write_files`,
`runcmd`, `bootcmd`, and `ssh_authorized_keys`, which concatenate in that order,
followed by portable runtime bootstrap entries. Duplicate `write_files` paths
are rejected. For example, provider defaults can include:

```yaml
cloud_init:
  package_update: true
  packages: [qemu-guest-agent, docker.io, git, python3]
  runcmd:
    - [systemctl, enable, --now, docker]
    - [systemctl, enable, --now, qemu-guest-agent]
```

The VM profile supplies `image`, `network`, `cpu`, `memory_mib` and `disk_gib`.
For the tested dev image, the live proof used 2 CPUs, 4096 MiB and 16 GiB disk.
Keep enough disk space for the image, workspace and Docker extraction.

The runtime accepts an empty workspace or Git source and literal session
environment settings. It also carries read-only hostPath **files** from the
existing session secret injector into guest Docker binds at the same paths.
Skuld consumes `/run/secrets/env.sh` and credential files normally. Credential
files live outside the archived workspace/home and are deleted with the VM.
Directories, writable mounts, Kubernetes volumes, projected service-account
identity, sidecars and workspace host-path sources are rejected. Configure compatible session
contributors explicitly; the development proof disables projected workload
identity and uses the existing development identity adapter. Production sessions
must use the deployment's existing identity and OpenBao configuration; the local
development proof does not establish production authentication.

While running, workspace/home live on guest local disk. Forge stop archives both
to `data_dir/<session-id>/session.tar` on the controller, then deletes the VM.
The next start restores them into a fresh guest. Archive failure prevents
release; cancelled startup before workspace initialization preserves the prior
archive. Back up the controller data directory. This is stop-time persistence,
not continuous replication or protection against unexpected guest disk loss.
Do not use the standalone infrastructure `release` command on a Forge allocation:
it does not invoke runtime archiving.

Graceful controller shutdown closes tunnels without deleting live VMs. A tunnel
supervisor also closes SSH when the controller dies abruptly: controller pipe EOF
causes bounded SSH termination, releasing the guest reverse listener. A restarted
controller reconstructs connections from durable leases and credential storage.
If runtime startup was interrupted, reconciliation replays the saved bootstrap
under the allocation lock in a background task. Initialized workspace contents
are not overwritten. Startup failures remain visible on the lease and retain
capacity; stopping a session cancels its local recovery task before archiving.

Live tests killed the controller both during provisioning and while Skuld was
running, then recovered the same allocation and HTTP/WebSocket connectivity.
Run one controller for this local-disk configuration. Multi-controller routing,
continuous inventory recovery and durable provisioning retry deadlines remain
unverified or unimplemented; this is not yet an unattended warm-pool deployment.

### Reuse existing session credentials

VM Skuld uses the same `BrokeredCredentialPodManager` helper as Docker and
Kubernetes. The default Codex adapter is
`skuld.codex_auth.VolundrCodexAuthProvider`: it requests access-only tokens from
the existing Völundr credential broker, which delegates renewal to the configured
OpenBao credential store. The selected integration's credential name/field are
preserved. No provider refresh-token implementation is added to the VM backend.
`compute.runtime.kwargs.codex_auth_adapter` and `codex_auth_kwargs` provide the
same explicit overrides as Docker; per-session broker settings take precedence.

Broker-only Codex connections require no mounted secret file or OpenBao agent.
For credentials normally supplied as static files (including Claude setup-token
environment files), select the existing session file injection adapter. The VM
runtime transports its output and binds it read-only. This is not continuous
OpenBao agent projection: managed OAuth file mappings still require a compatible
continuous injector and are rejected when it is absent. Keep using the configured
credential store, integration selection and existing broker authentication flow;
no separate VM login/refresh protocol is required.
