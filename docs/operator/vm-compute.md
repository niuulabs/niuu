# VM compute allocations

The generic VM backend supports Forge/Skuld sessions, provider-neutral warm
pools, and an infrastructure-only operator CLI. Warm capacity is bounded by the
configured pool policy and retired when its provider profile revision changes.
Root disks are disposable: infrastructure release deletes the VM, disk and
provider bootstrap data. Forge stop preserves session workspace/home to the
configured controller-local archive before release.

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
provider, endpoint, namespace or installation identity while it has live claims.
Legacy allocations without a resolved execution plan also require their original
profile mapping. Create a new pool for a new infrastructure mapping.

Admission uses a short PostgreSQL transaction. Provider operations hold one
connection-level advisory lock, without holding a database transaction. Process
or connection loss releases that lock. This is not fencing against a late remote
API request after connection loss. A continuously running inventory reconciler
and stronger crash-window handling remain required before unattended rollout.
This CLI performs explicit reconciliation only; it does not run a background
controller or automatically delete unknown inventory.

## Versioned execution catalog

Forge can resolve machine selection, host preparation, guest access and session
runtime from an operator-owned file catalog. The catalog is read-only application
configuration: there is no database catalog, CRUD API or web editor. Configure one
provider binding per pool. Every execution in that catalog must name the configured
pool and provider binding; runtime provider switching and failover are not supported.

Add these fields to the root `compute` settings:

| Field | Meaning |
| --- | --- |
| `provider_binding` | Stable, provider-neutral identity for the one `MachineProvider` installation serving this pool. |
| `execution_catalog.adapter` | `volundr.adapters.outbound.file_execution_catalog.FileExecutionCatalog`. |
| `execution_catalog.kwargs.root` | Absolute path to the catalog directory. |
| `execution_catalog.secret_kwargs_env` | Optional constructor-argument to environment-variable references. The file adapter itself needs none. |
| `runtime` | Legacy runtime binding. Keep it while any live lease predates resolved execution plans; it may be removed after those leases drain. |

The root is a versioned package with this layout:

| Path | Required fields and purpose |
| --- | --- |
| `catalog.yaml` | Exact `default_execution` reference (`id`, `revision`), optional `legacy_compute_profiles` mapping from old `compute_profile` names to exact execution references, and optional `launch_executions`: the exact executions Launch offers as a "Runs as" choice, in order. |
| `machines/*.yaml` | `id`, `revision`, provider-owned `provider_profile` name, and declared `host_requirements`. Provider-specific image, network, placement and sizing remain in `compute.provider.kwargs.profiles`. |
| `host-recipes/*.yaml` | `id`, `revision`, `preparation` adapter binding, pinned `artifacts`, and ordered `stages`. |
| `access/*.yaml` | `id`, `revision`, `transport` adapter binding, guest `principal`, and `trust` contract. |
| `runtimes/*.yaml` | `id`, `revision`, `runtime` adapter binding, `contributor_backend`, independent `storage_mode`, `capabilities`, and `host_requirements`. |
| `executions/*.yaml` | `id`, `revision`, `pool_id`, `provider_binding`, and exact machine, host-recipe, access and runtime references. |
| Any contained artifact path | Executable recipe content referenced by a host recipe with its lowercase SHA-256 digest. Paths must be relative to the catalog root and cannot traverse or resolve outside it. |

Each adapter binding has a stable `binding_id`, a fully qualified `adapter`, plain
`kwargs`, and optional `secret_kwargs_env`. The latter maps constructor argument
names to environment-variable names; secret values do not belong in catalog YAML.
Supported built-in bindings are
`volundr.adapters.outbound.ssh_guest_access.SshGuestAccess`,
`volundr.adapters.outbound.ssh_host_preparation.SshHostPreparation`, and the VM
runtime classes already documented below. Runtime image kwargs must use an immutable
image digest, such as `registry/repository@sha256:<digest>`, rather than a mutable tag.
An image the deployment bumps in place (each platform release) can come from the
environment through `secret_kwargs_env`, which the plan digest does not cover: edited
in the YAML, it would change a revision under the sessions pinned to it, which then
can no longer be recovered or stopped. A running session keeps the image it started
with, as with `compute.runtime`.

The loader rejects duplicate YAML keys, duplicate `id`/`revision` pairs, missing
references, path escapes, missing or non-regular artifacts, and digest mismatches.
An execution request uses exact `id@revision` syntax in
`workload_config.execution_profile`. Omission uses the exact default in
`catalog.yaml`. The legacy `workload_config.compute_profile` field works only when
`legacy_compute_profiles` explicitly maps it; specifying old and new fields with
different results is an error.

### Launch runtime choice

`launch_executions` lists the executions a person may choose between when they
launch a session on this Forge ("Runs as" in Quick and Advanced launch):

```yaml
default_execution: {id: vm-container, revision: "1"}
launch_executions:
  - {id: vm-container, revision: "1"}
  - {id: vm-openshell, revision: "1"}
```

What each one runs as comes from its runtime adapter, not from the YAML:
`SshContainerVmRuntime` is **Container**, `SshOpenShellVmRuntime` and
`OpenShellVmRuntime` are **OpenShell**. Forge refuses to start when an offered
execution's runtime cannot be offered (it declares no kind) or when two offered
executions run as the same kind. Omit the list, or leave it empty, and the host
offers no choice: Launch shows none and sessions run `default_execution`. The list
is not part of any plan digest, so changing it never blocks a pinned lease.

`GET /api/v1/forge/session-runtimes` (and the Guild facade's
`?instance_id=` form) reports each offer: its `runtime`, the `execution_profile`
to send, whether it is the `default`, the `grants` it can give, and its
`readiness`: `warm` with the number of free prepared machines whose host matches
the execution, `boot` when a new machine must boot first, or `full` with the
remedy. Launch sends the choice as `workload_config.execution_profile`.

Grants (`sudo`, `docker`) are offered only by a runtime adapter that declares them
and are sent as `workload_config.runtime_grants`. No shipped runtime declares any
yet, so a session that asks for a grant is refused with 422 before it starts.

Revisions are immutable. Changing any entry or repinning an artifact under the same
`id`/`revision` changes the resolved plan digest and blocks recovery of a lease pinned
to the old content. Add a new revision instead, and retain every referenced YAML file
and artifact until its last lease has been released. Restart Forge after catalog
updates so the composition root imports and injects the new adapter set before new
sessions select it. A restart revalidates existing pinned revisions and artifacts;
it never substitutes the current default.

Host recipe stages have stable IDs and one timeout shared by check, optional apply,
and verify. A check exit code of 0 means the requirement is already satisfied; 1
requests apply; every other code fails. Apply and verify must return 0. Verify stdout
is either empty or a JSON object mapping safe fact names to string values within the
configured output limit. Facts from all stages are merged; conflicting values fail
preparation. The executor discards stage stderr and reports only safe stage/detail
codes. Timeout kills the stage process group.

This repository does not ship a production host recipe or artifact package. Before a
recipe can run, the controller needs an OpenSSH client and network access to the guest
SSH port. The guest needs a reachable SSH server, the configured login principal and
controller public key, Python 3.12 or newer, and passwordless noninteractive `sudo -n`;
the provider profile also needs a running QEMU guest agent for address discovery. A
recipe stage may run as `root` or as the configured access principal and must verify
the combined machine and runtime `host_requirements` as string facts.

For `SshContainerVmRuntime`, the recipe must prepare and verify a working Docker CLI
and daemon, `tar`, and Git when Git session sources are allowed. The guest must be able
to pull the digest-pinned Skuld image and have enough local disk for that image,
workspace, home and archive staging. For `SshOpenShellVmRuntime`, the recipe must also
prepare OpenShell and its gateway, Podman 5 or newer with a live rootless user socket,
and a systemd user manager with lingering and delegated CPU cgroup control. The
catalog path marks the host as recipe-prepared, so the container runtime does not
bootstrap host dependencies and the OpenShell runtime verifies its prepared software
instead of running its legacy installer. Build and validate a pinned recipe against
the exact guest image before admitting sessions.

The OpenShell release is pinned: `openshell_version` (default `v0.1.6`) selects the
release, and `openshell_install_sha256` the SHA-256 of that release's `install.sh`,
which the runtime verifies before running it. Pin a tagged release: `install.sh`
fetches prerelease packages only through an authenticated GitHub CLI, which a guest
does not have. A guest without that release gets it installed; a recipe-prepared host
running another release is refused, naming the version to set. The runtime's own
installer writes a version 2 Podman gateway configuration with
`allow_driver_config = true`, `enable_bind_mounts = true`,
`userns = "keep-id:uid=<sandbox_uid>,gid=<sandbox_gid>"` and
`[openshell.drivers.podman.resource_admission] enabled = false`, generates the
gateway's certificates for `host.containers.internal`, and registers the CLI with
`openshell gateway add https://127.0.0.1:17670 --local --name openshell`. A prepared
host must do the same; the runtime refuses one whose gateway lacks any of these
settings, or whose CLI cannot list sandboxes (`openshell status` succeeds even with
no gateway registered).

`keep-id` maps the access principal to the sandbox user (`sandbox_uid`/`sandbox_gid`,
998 in `ghcr.io/niuulabs/openshell`, the image to run). The session's workspace and
home are then owned by that principal on the host, and secrets its group may read
(OpenBao renders them `0640` for group 1000) are readable in the sandbox. Podman
copies an image's layers for that mapping the first time it runs it (about a minute
for the OpenShell image) and the sandbox create gives up before then, so `prepare`
and `warm` pull the image and run it once under the same mapping.

On Ubuntu 24.04 the distribution's Podman is 4.9. A static Podman 5 (for example
`mgoltzsche/podman-static`, pinned by digest) works, with `uidmap` and
`dbus-user-session` installed and an AppArmor profile granting `userns,` to
`/usr/local/bin/podman` and `/usr/local/bin/crun`: noble denies unprivileged user
namespaces to binaries without one (`kernel.apparmor_restrict_unprivileged_userns`),
and rootless Podman then fails with `failed to reexec: Permission denied`.

Only Skuld runs in the sandbox. The session's pod contract runs as for
`SshContainerVmRuntime`: its init containers and sidecars (the guest credential agent,
OpenBao's agent) run in the guest's Docker from `host_image`, the digest-pinned Skuld
image, and the sandbox bind-mounts the directories they keep fresh at the targets the
launch names, adding those to the policy's `filesystem_policy` where it does not
already cover them. Use `contributor_backend: vm` for this contract and set
`native_credential_providers: false`: its credentials arrive as rendered files, while
`true` (the default) turns `openshell.credentialMappings` into OpenShell providers for
the `openshell` contributor contract. A `policy` is required; a sandbox without one
never leaves Provisioning. The sandbox reaches the guest's loopback, where the Forge
tunnel listens, as `host.openshell.internal` (the driver pins it to the supervisor's
loopback), so set `sandbox_platform_url: http://host.openshell.internal:<guest port>`
and allow that endpoint in the policy. `host.containers.internal` is the guest's LAN
address in Podman's `/etc/hosts` and does not reach the tunnel. Policy binaries are
matched by resolved path: in the OpenShell image Python is
`/sandbox/.uv/python/**` and Claude Code
`/opt/skuld-tools/node_modules/@anthropic-ai/**`.

OpenShell shutdown requests sandbox deletion once, then polls the complete sandbox
inventory until absence is confirmed. Runtime adapter kwargs control this wait:
`sandbox_delete_timeout_seconds` (120), `sandbox_delete_poll_interval_seconds` (2),
and `sandbox_delete_command_timeout_seconds` (30); `sandbox_inventory_page_size`
(1000, the gateway's maximum) sets the `--page-size` of each inventory page,
followed by page token until the last page. Each delete/list command is capped
by both its command timeout and the remaining deletion deadline. Keep the deletion
budget below the controller's overall cleanup and SSH command timeouts. Transient
transport failures are retried within that budget; permission errors and malformed
inventory fail immediately. An unconfirmed deletion prevents archive replacement
and VM release. Diagnostics retain the delete exit status, safe failure categories,
last observed presence, inventory check count, and deadline status; raw CLI stderr
is not exposed. Configure these kwargs in a new runtime/catalog revision when
changing a pinned execution plan.

SSH access supports `provider_identity` for a provider-delivered pinned host key and
explicit `tofu` for one `accept-new` enrollment followed by strict allocation-scoped
pinning. `ssh_ca` is represented in the schema but the built-in SSH adapter rejects it
as unsupported. Do not select it until a configured access adapter implements and
proves CA validation.

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

The runtime holds an SSH reverse tunnel from the guest's
`127.0.0.1:<guest_platform_port>` to `platform_host:platform_port` on the
controller, and Skuld inside the guest reaches Forge only through it. That port
must serve Forge the way the session would see it in a pod. In a Helm deployment
that is the Forge pod's Envoy listener (`envoy.port`, 8443 by default), not the
app port (8080): the app trusts the identity headers that only Envoy sets, so a
tunnel to the app port reaches Forge unauthenticated and the broker's calls
(delivery claims, notifications) are refused with 401. A Forge without Envoy in
front, as in the standalone example above, points it at its own HTTP port.

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
environment settings. Of the session's pod spec it runs the subset a guest can
honour, and refuses the rest by name:

- read-only hostPath **files** (the session file injector's output), copied into
  the bootstrap and bound read-only at the same paths;
- `emptyDir` volumes, created on guest tmpfs (`/run/niuu/session/volumes`, mode 0777);
- projected volumes of `serviceAccountToken` sources only (relative `path`, explicit
  `audience`, optional `expirationSeconds`), delivered as described below;
- init containers and sidecars with `name`, `image`, `command`, `args`, literal `env`,
  `volumeMounts` (`name`, `mountPath`, `readOnly`) and `securityContext`
  `runAsUser`/`runAsGroup`. Init containers run to completion, then sidecars start,
  then Skuld, on every start; all run with host networking and the label
  `compute.niuu.io/allocation=<allocation id>`, and stop with the session.

Credential files live outside the archived workspace/home and are deleted with the
VM. PVCs, ConfigMap/Secret volumes, `valueFrom` env and workspace host-path sources
are rejected. The storage contributor emits no PVC settings for VMs, and session
definitions drop their `homeVolume`/`persistence` pod defaults (the chart's built-in
definitions carry them), so a VM release needs no override for them. A definition
that names an `existingClaim`, and any template or profile that sets pod storage,
is still refused.

## Live preview in VM sessions

A dev server a VM session starts opens at
`https://<port>--<session-id>.<preview domain>` like a pod session's: the VM
Forge's preview router signs a short-lived credential per request, reaches the
guest's Skuld through the same SSH tunnel as chat, and Skuld forwards to the
port on the guest's loopback. Turning it on takes:

- `compute.runtime.kwargs.preview_router_auth` on `SshContainerVmRuntime`: Skuld's
  router-credential check, normally
  `skuld.preview_adapters.WorkloadJwtRouterAuth` with the workload issuer, the
  router's audience and the issuer's JWKS URL. `preview_excluded_ports` adds
  ports to the fixed exclusions (SSH, the resolver, the tunnel, the broker).
  VM sessions offer only listeners owned by the session's own user, so the
  guest's root services (containerd's stream server picks a random port per
  boot) never appear as previews.
- `workloadIdentity` on the VM Forge, with the shared signing key. Enabled, the
  Forge also answers token exchanges, and Skuld in each guest exchanges its
  session ServiceAccount token there for its calls back, so the release needs a
  `mappings` entry admitting its session accounts
  (`system:serviceaccount:<namespace>:openbao-session-` as `subject_prefix`).
  Without it delivery claims are refused and messages stay unconfirmed.
- `sessionGateway` and `sessionPreview` on the release, with a preview domain of
  its own: another Forge's `*.<domain>` routes to that Forge's router, which
  does not know these sessions. The Gateway needs a wildcard DNS record and a
  DNS-01 certificate issuer, and the preview OIDC client needs the release's
  callback host (`auth.<preview domain>`).

## Separated session slots

`compute.runtime.kwargs.slot_isolation` on `SshContainerVmRuntime` runs each
session in a slot of its VM instead of on the whole VM. Before Skuld starts, the
runtime runs the slot supervisor (`vm_slot_supervisor`) in the guest over SSH, as
root; it is not a systemd service. For slot `n` the supervisor creates:

- user and group `niuu-slot-<n>` with uid and gid `uid_base + n`. Its
  directories `/var/lib/niuu/slots/<n>` (home, workspace, launch file, session
  secrets, the Forge session credential) and `/run/niuu/slots/<n>` (emptyDir and
  token volumes) are mode 0700 and owned by that user. The guest agent's
  configuration, which names the slot it fetches credentials for, is in
  `/etc/niuu/slots/<n>`, owned by root, so the slot cannot point the agent at
  another slot. Sidecars that set a group (OpenBao's agent renders secrets 0640)
  run with the slot's group, never a VM group;
- a holder container (`niuu-slot-<n>-holder`, the Skuld image running `sleep` as
  the slot's uid). Its network namespace is attached as `niuu-slot-<n>`, and every
  container of the slot shares its network and PID namespaces. So two sessions
  can both listen on `:5173`, and neither sees the other's processes;
- a veth pair `niuuslot<n>` with the slot's four addresses from `subnet`.
  Outbound traffic is NATed out of the VM's default-route interface. Netfilter
  rules tagged `niuu-slot-<n>` allow the slot nothing else on the VM, no other
  slot and no Docker network, and nothing inbound except replies;
- the systemd slice `niuu_slot_<n>.slice` with the `cpu`, `memory_mib` and `pids`
  limits. Every container of the slot runs in it.

Skuld then runs as the slot's uid (the image's `image_user` account keeps its
name, home and shell, renumbered), without `NET_RAW`. The broker listens on the
slot's own address, and Forge's SSH tunnel for that slot forwards there. The
broker's callbacks go to the slot's gateway on `guest_platform_port`, which is
DNATed to the slot's reverse forward on the VM's loopback
(`platform_forward_port_base + n`). Each slot's preview scanner sees only its own
namespace, so a preview always opens the right session's port. Tunnels, the
storage marker, credential refresh and container names are keyed by allocation
and slot. Slot 0 keeps the allocation's own key.

```yaml
compute:
  runtime:
    kwargs:
      slot_isolation:
        cpu: 2            # cores per slot, or null for no limit
        memory_mib: 4096  # or null
        pids: 4096        # or null
        # Defaults:
        # uid_base: 61000
        # subnet: 10.249.0.0/16     # 4 addresses per slot; must not overlap the VM's networks
        # platform_forward_port_base: 18100
        # image_user: skuld
```

The limits have no defaults: write each one, or `null`, for the machine profile.
When a stopping slot has hit its memory or PID limit, the runtime logs an error
naming the knob and counts the hits on `volundr.compute.slot.limit_hits`
(attribute `limit`: `memory.max`, `memory.oom_kill` or `pids.max`).

Stopping a slot stops its containers and archives its storage. The supervisor
then removes the containers, rules, interface, namespace, slice and its settings,
the directories, the user and the group. It then checks that none of them is
left. If anything is left, the stop fails with stage `slot-down`, naming what
remains (for example a process still running as the slot's user), and the stop
is retried. A retry keeps the archive already taken.

Without `slot_isolation` a VM runs one session, as before, and a lease for any
slot but 0 is refused. With it, `SshContainerVmRuntime` declares slot support
(`supports_slots`) when its credential delivery serves slots:

- `SshPushCredentialDelivery` serves every slot: it writes each slot's own files.
- `MetadataCredentialDelivery` serves slots when its guest identity belongs to the
  machine: `ProviderAttestedGuestIdentity` (Harvester) and `HostKeyGuestIdentity`.
  Each slot runs its own guest agent in the slot's namespaces; its configuration
  names the slot (`"slot": n`), and Forge answers only for the session bound to
  that slot. The identity's token or key is mounted into that agent only (root,
  read-only), never into Skuld or the slot's home, so a slot's user cannot present
  it. `SpiffeGuestIdentity` registers and deletes a SPIRE entry per allocation, so
  one slot's stop would retire the others' identity: it serves one slot.

OpenShell runtimes refuse `slot_isolation`: their sandbox is the boundary, and
they serve one session per machine.

## Host mode

`volundr.adapters.outbound.ssh_host_vm_runtime.SshHostVmRuntime` runs each
session directly on its VM: Skuld and the agent CLI run as the slot's user
against the VM's own filesystem, toolchain and Nix store, with no session
container. A VM Forge runs one runtime, so host mode is a Forge (and a Guild
host) of its own, never a choice beside Docker on the same pool.

```yaml
compute:
  runtime:
    adapter: volundr.adapters.outbound.ssh_host_vm_runtime.SshHostVmRuntime
    kwargs:
      skuld_image: "ghcr.io/niuulabs/openshell@sha256:<digest>"
      slot_isolation:        # required: the slot is the session's boundary
        cpu: 2
        memory_mib: 4096
        pids: 4096
        image_user: sandbox  # the OpenShell image's session account
      nix: true              # the machine profile installs Nix (below)
      containers: {}         # rootless Podman in every slot (below); null for none
      # session_start_timeout_seconds: 30  (until Skuld's PID 1 runs; a hit fails the start)
      # … SSH, platform and preview settings as for SshContainerVmRuntime
```

How a session runs, all over SSH as every other guest step, never as a service:

1. The slot supervisor creates the slot exactly as for Docker sessions (user
   `niuu-slot-<n>`, its directories, the holder container that keeps the slot's
   network namespace and cgroup, the veth, the rules and the slice). The
   credential agents (guest agent, OpenBao's agent) run beside the holder in
   the guest's Docker, as for `SshContainerVmRuntime`.
2. The session image is unpacked once per digest under
   `/var/lib/niuu/host-images/<digest>` (`docker create` and `docker export`; no
   container runs it), with wrappers for the agent CLIs (`claude`, `codex`,
   `opencode`, `grok`, `pi`, `muse`) and fallback tools (`gh`, `glab`, `uv`,
   `node`, `npm`). `warm` does this ahead of sessions.
3. A guest program joins the slot's network namespace and the holder's cgroup
   (so the slot's limits apply), opens its own mount and PID namespaces and
   becomes PID 1 there. In that mount namespace it binds, read-only, the
   unpacked image at `/opt/niuu-skuld` and the image's venv and Python at their
   own paths, then the workspace at `/workspace`, the home at `/home/skuld`, the
   launch's secret and volume mounts at their targets, a private `/tmp`,
   `/var/tmp` and `/dev/shm`, and the VM's upstream resolvers over
   `/etc/resolv.conf` (the slot cannot reach the VM's resolver on loopback).
   An empty read-only tmpfs covers `/etc/niuu`, where the VM keeps its own
   credentials (the guest identity token, Forge's SSH host key): a session
   runs on the VM's filesystem, so it must not depend on their modes.
   It then runs Skuld as the slot's uid. When Skuld ends, PID 1 ends, and with
   it every process the session left.

A host session runs on the VM as the slot's user, who owns the slot's data and
runtime directories and could swap anything in them for a link. So whatever
root or the credential agents write for the slot (the launch file, secrets, the
Forge session credential, the guest agent's configuration, the token and volume
directories) lives in the slot's root-owned `/etc/niuu/slots/<n>`
(`root:<slot group> 0750`, closed to other slots too), and only the session's
home and workspace stay in `/var/lib/niuu/slots/<n>`. The supervisor creates
and owns the slot's directories, and writes its identity files, through
directory descriptors that never follow a link; the workspace marker and the
restore do the same, the start clones the repository as the slot's user, and
it touches the slot's storage only once a replaced session has exited.

`PATH` is the image's agent CLIs first (the pinned versions), then the
`docker` shim (rootless Podman), then Nix's profiles (`~/.nix-profile/bin`, the default profile), then the VM's own
directories, then the image's fallback tools. The agent's system prompt says it
runs on the VM as an unprivileged user and how to install with Nix.

**The image needs a relocatable Python.** Skuld runs from the image's venv on
the VM's kernel and libc, so its interpreter must not be the image
distribution's: `ghcr.io/niuulabs/openshell` is built on uv's
python-build-standalone and declares `VIRTUAL_ENV` and `UV_PYTHON_INSTALL_DIR`.
The Skuld image's Python is Ubuntu's and runs only inside its own root; the
unpack refuses such an image, naming the remedy. One digest serves the
holder, the credential agents and Skuld.

Stopping a session sends Skuld `SIGTERM` (then `SIGKILL` after
`stop_timeout_seconds`), which ends its containers with it, removes its
subordinate ids, and then stops, archives and
removes the slot as for Docker sessions. A host session's links to absolute
paths (Nix makes `~/.nix-profile` and its caches that way) are restored as
written; they are never followed, and a member whose own path resolves outside
the session still fails the restore. Docker sessions keep refusing them.

### Nix on host-mode VMs

Software is shared through a multi-user Nix: one daemon, one `/nix/store`,
profiles per user. `deploy/nix/install-nix.sh` installs a pinned release
(checked by SHA-256) as root and is idempotent; run it from the machine
profile's cloud-init or a host recipe. It:

- enables `nix-command` and `flakes`, lets every user build through the daemon
  and trusts only root, so a slot can add packages but never a substituter or
  an unsigned path, and cannot change another slot's packages;
- puts one copy of a locked nixpkgs (`NIX_NIXPKGS`, a revision with its
  narHash) in the store, roots it (`/nix/var/nix/gcroots/niuu-nixpkgs`) and
  makes it what `nixpkgs` means in the system registry. No slot resolves a
  branch through the GitHub API, which rate-limits a shared egress address, and
  none downloads or unpacks nixpkgs again;
- installs `NIX_BASELINE` into the default profile, when set: for example the
  repository's own flake, `github:niuulabs/niuu/<rev>?dir=deploy/nix#baseline`,
  which builds the same toolchain on Linux and macOS.

Pass garbage collection thresholds in `NIX_EXTRA_CONF`, for example
`min-free = 2147483648` and `max-free = 8589934592`: the daemon then collects
on its own when free space drops, which keeps a long-lived VM from filling.
A stopped slot's profile goes with its home, so its packages become garbage.
With `replace` (the default) machines drain after their sessions as well.

A slot installs with `nix profile install nixpkgs#<package>` (kept in its
profile, which is archived with its home) or `nix shell nixpkgs#<package>`; a
package another slot installed is in the store already and needs no download.
The runtime refuses to start a session with `nix: true` on a VM without the
daemon socket.

Limit: a profile is a store path built on the VM. A session restored onto
another VM keeps `~/.nix-profile`, but that VM lacks the profile's store path
until the session installs its packages again.

### Rootless containers

Every slot can build and run containers with rootless Podman (`containers`,
on by default; `null` turns it off). The machine profile installs `podman`,
`uidmap` and `passt` (Ubuntu 24.04 ships AppArmor profiles that let them use
user namespaces) and writes `/etc/containers/containers.conf`:

```toml
[engine]
cgroup_manager = "cgroupfs"   # no systemd user session; the runtime delegates a cgroup
events_logger = "file"

[network]
default_rootless_network_cmd = "pasta"
```

For each slot the runtime adds:

- **Subordinate ids of its own.** Slot `n` maps container uids and gids to
  `subordinate_id_base + n × subordinate_ids_per_slot` onwards (defaults
  1000000 and 65536) in `/etc/subuid` and `/etc/subgid`, and removes them at
  stop. Forge refuses ranges that run past the id space or overlap the slots'
  own uids; the start refuses one another VM user holds.
- **A delegated cgroup inside the slot's.** The holder's Docker scope in the
  slot's slice becomes a tree: the holder in a leaf, and a `session` subtree
  owned by the slot's user, with the `cpu`, `memory` and `pids` controllers.
  The session runs in its `init` leaf, and Podman (`cgroupfs`) puts each
  container in a cgroup of its own under `session`, so `--memory` and
  `--pids-limit` work and every container stays under the slot's limits.
- **Its own runtime directory** (`XDG_RUNTIME_DIR=/run/niuu/slots/<n>/xdg`) and
  storage in its home (`~/.local/share/containers`). Container storage holds
  files owned by subordinate ids and is the machine's: it is never archived
  (`home/.local/share/containers` and `home/.cache/containers` join the
  archive exclusions) and the start leaves its owners as they are.
- A `docker` command that runs `podman`.

Containers share the slot's network namespace through pasta: a published port
listens in the slot as the slot's user, so two slots can publish the same port
and the preview offers it. Their processes are in the session's PID namespace
and end with it.

What a slot never gets: the guest's Docker daemon (its socket is
`root:docker`, and no slot user is in `docker`), sudo, or any other
root-equivalent access. The guest's Docker runs only the platform's holder and
credential agents. See `.claude/rules/architecture.md` ("Containers in
host-mode slots are rootless").

## VM session credentials

A session pod gets its ServiceAccount's tokens from the kubelet and its Forge
session credential from a projection. A VM session gets the same credentials from
the configured **credential delivery**, so the same contributors serve both:

- the workload-identity contributor declares its projected `volundr-api` token, and
  Skuld exchanges it as in a pod;
- `OpenBaoAgentInjectionAdapter` with `guest_agent_image` set emits an OpenBao agent
  init container (and, with `oauth_mount_path`, a re-rendering sidecar) that logs in
  with the session ServiceAccount token and renders `/run/secrets` exactly as the
  injector would. Every template destination must lie under `/run/secrets`;
- the Forge session credential (`SKULD__FORGE_MCP__TOKEN_FILE`) is a file under
  `/var/run/niuu/forge-session` in the session container. It is minted per launch
  and never stored with the allocation's bootstrap.

All three pieces are dynamic adapters under `compute`; none switches to another at
runtime:

```yaml
compute:
  service_account_tokens:            # who issues ServiceAccount tokens
    adapter: volundr.adapters.outbound.k8s_service_account_tokens.KubernetesServiceAccountTokenIssuer
    kwargs: {namespace: forge-vm-sessions}
  credential_delivery:               # how they reach the guest
    adapter: volundr.adapters.outbound.guest_credential_delivery.MetadataCredentialDelivery
    kwargs: {metadata_url: "https://forge-vm.example.com"}
  guest_identity:                    # how a guest proves itself (metadata only)
    adapter: volundr.adapters.outbound.guest_identity_host_key.HostKeyGuestIdentity
    kwargs: {clock_skew_seconds: 30}
  runtime:
    adapter: volundr.adapters.outbound.ssh_vm_runtime.SshContainerVmRuntime
    kwargs:
      default_service_account: skuld-session   # when the pod names none
      # … SSH and Skuld settings as above
```

The issuer calls the Kubernetes TokenRequest API for the session ServiceAccount, as
a kubelet does: Forge needs `create` on `serviceaccounts/token` in that namespace
(chart: `guestMetadata.enabled` or `guestMetadata.issueServiceAccountTokens`, with
`guestMetadata.serviceAccountNamespace`). Sessions carrying tokens or a Forge session
credential fail to launch, naming the missing setting, when no delivery is configured.

### Choosing a delivery

| Delivery | Guest needs | Refresh |
| --- | --- | --- |
| `SshPushCredentialDelivery` (`refresh_fraction` 0.8, `retry_seconds` 10) | nothing beyond the pinned SSH channel | the controller re-issues each token at `refresh_fraction` of its lifetime and rewrites it over SSH |
| `MetadataCredentialDelivery` | network reach to `metadata_url` (HTTPS) | the guest agent re-fetches at `refresh_fraction`; it survives controller restarts and serves any controller |

Use metadata delivery where guests can reach Forge (Harvester VLANs routed to the
cluster ingress); use SSH push for backends whose guests are isolated from it.

**SSH push.** A failed refresh is logged at error, counted on
`volundr.compute.token_refresh.errors`, and retried every `retry_seconds` until it
succeeds; the session loses its identity only when the last token expires. The
launch credential is held in controller memory: a controller restarted mid-start
refuses that start ("restart the session") rather than starting a broker without it.

**Metadata.** The runtime adds `169.254.169.254/32` to the guest's `lo` and runs the
guest agent from the Skuld image (`python -m volundr.guest_agent`), first as the
init container `niuu-guest-agent-init` (`once`: fetch and write every file, so the
OpenBao agent finds its token) and then as the sidecar `niuu-guest-agent` (`serve`).
The sidecar refreshes the files and serves the last answer on
`http://169.254.169.254/niuu/v1/credentials` to guest processes that send
`Niuu-Metadata: true` (other requests get 403, against request forwarding), plus
`/niuu/v1/health`. Delivery kwargs: `metadata_url` (https, reachable from guests),
`refresh_fraction`, `retry_seconds`, `request_timeout_seconds`, `ca_file` (guest
path of a private CA), `listen_port` (80), `agent_command`.

The agent calls `POST /api/v1/internal/compute/metadata/credentials` with the body
`{"allocation_id": "<uuid>", "slot": <index>}`. Forge answers only for the session
currently bound to that slot of the calling allocation (machine ready or idle, slot
starting or busy, not stopping), minting fresh ServiceAccount tokens and a Forge
session credential for the session's **current** launch. `slot` may be omitted only
when the allocation has one slot (the guest agent of a one-session machine sends
none); on a machine with more slots it is required, and a `slot` that is not an
integer below the allocation's slot count is a 400 after authentication. Every
authentication failure is the same 401; an authenticated allocation without a
running session in that slot is 409 with a reason code
(`allocation_not_running`, `session_stopping`, `no_bound_session`,
`session_not_launched`, `no_service_account`, `forge_session_unavailable`); Forge
logs the detail at warning, and the guest agent logs the code. A warm spare that
holds no session answers `no_bound_session`. The chart's
`guestMetadata.enabled` renders the Envoy bypass for exactly that route (JWT and
Cedar are skipped; the route authenticates the guest itself). Check that Forge's
NetworkPolicy and ingress admit guest traffic.

### Guest identity for the metadata endpoint

Pick one per pool in `compute.guest_identity`:

| Identity | Use it for | Needs in the VM image |
|---|---|---|
| `ProviderAttestedGuestIdentity` | Harvester (KubeVirt): the default there | nothing |
| `SpiffeGuestIdentity` | VMware and other providers with SPIRE | `spire-agent` |
| `HostKeyGuestIdentity` | any provider that delivers private bootstrap | nothing |

`ProviderAttestedGuestIdentity` (Harvester): the hypervisor vouches for the guest,
as an AWS instance identity document does. Set `guest_service_account` on
`HarvesterMachineProvider`; every VM's virt-launcher pod then runs as that
ServiceAccount, and KubeVirt shares the pod's token into the guest over virtiofs
(`EnableVirtioFsConfigVolumes`). A `bootcmd` mounts it read-only at
`/etc/niuu/guest-identity` on every boot, and the kubelet keeps it fresh. The agent
sends it as `Authorization: Bearer <token>`. Forge sends a TokenReview to Harvester
through the provider's own credential and requires:

- the token is for `system:serviceaccount:<namespace>:<guest_service_account>` and
  bound to one pod;
- the pod exists with that UID, and its controller owner is a VMI;
- the VMI carries this installation's and an allocation's labels, is named for that
  allocation, and lists the pod in `status.activePods` (only virt-controller writes
  it, so a pod that merely names the VMI as owner does not pass).

On Harvester this needs:

- the ServiceAccount in the provider namespace with **no** RoleBindings. Its token is
  addressed to Harvester's API server (KubeVirt cannot set another audience), so it
  must grant nothing there;
- Forge's Harvester credential allowed to `create` `tokenreviews`
  (`system:auth-delegator`) and to `get` `pods` and `virtualmachineinstances` in the
  provider namespace.

A Harvester outage or a denied TokenReview is a 500 naming the RBAC, never a 401.

`HostKeyGuestIdentity` (works on every provider that delivers private bootstrap):
the agent signs `METHOD\nPATH\nTIMESTAMP_MS\nSHA256(body)` with the guest's Ed25519
SSH host key, which the controller generates and the provider delivers, and Forge
verifies it against the allocation's pinned public key. Timestamps must strictly
increase per allocation (an atomic watermark, migration 000104) within
`clock_skew_seconds`. See the "VM guest metadata identities" exception in
`.claude/rules/architecture.md`.

`SpiffeGuestIdentity` (providers without a hypervisor attestor, such as VMware, where
SPIRE is available): the agent presents a JWT-SVID for
the configured `audience`, fetched from the guest's SPIRE agent
(`spire-agent api fetch jwt`), and Forge verifies it against SPIRE's JWKS
(`issuer`, `jwks_uri`, `ca_cert_path`). The subject must match `spiffe_id_template`
(default `spiffe://{trust_domain}/forge-vm/{pool_id}/{allocation_id}`). When a
session starts Forge registers a spire-controller-manager `ClusterStaticEntry`
for the allocation with `selector_templates` (default
`docker:label:compute.niuu.io/allocation:{allocation_id}`), and deletes it when the
session stops (chart: `guestMetadata.spiffeRegistration`). Node attestation is
provider specific:

- `node_attestation: x509pop` (default): Forge generates a node key and a certificate
  signed by the operator CA (`node_ca_cert_file`, `node_ca_key_file`,
  `node_certificate_ttl_seconds`) with a random node id as CN, and installs a
  `spire-agent` config and systemd unit through the bootstrap. The image must ship
  `spire-agent` at `spire_agent_binary`. The SPIRE server needs the `x509pop` node
  attestor in `external_pki` mode with that CA bundle and
  `agent_path_template = "/{{ .PluginName }}/{{ .Subject.CommonName }}"` (or set
  `x509pop_agent_id: fingerprint`), and its agent port reachable from guests
  (`server_address`, `server_port`); the guest trusts the server through
  `trust_bundle_file` or `trust_bundle_url`.
- `node_attestation: provider`: the image or provider runs an already attested agent
  (a cloud attestor such as AWS IID); set `parent_id_template` to its agent id.

Providers that cannot deliver cloud-init or user-data may set
`runtime.kwargs.bootstrap_delivery: ssh`. The provider must arrange for the
configured controller public key to be accepted by the guest before the first
connection. That first connection uses OpenSSH `accept-new` under a dedicated
allocation alias and delivers only the machine bootstrap. The accepted guest
host key is then reused with strict checking before any session launch data or
credential file is sent. This is explicit trust on first use and is weaker than
provider-delivered host identity. The built-in access adapter does not yet implement
SSH CA validation. After strict identity is verified, the controller persists an
allocation-specific marker and will never fall back to first-contact trust for
that allocation. Keep `data_dir` durable and protected.

Portable bootstrap commands must be safe to retry. The guest checkpoints each
completed command and resumes at the next command after an ordinary failure, but
a guest or controller crash can still occur between a command's side effect and
its progress checkpoint.

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
covered by the warm-pool lifecycle below. Production credentials and capacity
acceptance still require the intended deployment configuration.

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

## Warm pools and admin controls

Settings → Forge → Compute pool manages the configured provider's pool. The
same controls work for every `MachineProvider`/`VmRuntime` pair: profile name,
maximum machines, sessions per machine, free warm slots, concurrent provisioning,
spare lifetime, provisioning timeout, pause and drain. Provider-specific image, network, disk,
CPU, memory and cloud-init remain in the provider adapter's profile kwargs.
The profile selector is populated by the provider's `profiles()` catalog. The
Machine profiles section shows adapter-selected public details; raw cloud-init
and credentials are never returned. Definitions remain in adapter configuration
(`compute.provider.kwargs.profiles` for Harvester). Unknown profile names are
rejected before saving pool policy. Every provider implements this catalog, so
the shared UI does not interpret provider-specific configuration.
HTTP authentication is optional at the composition boundary: adapters using a
native credential chain can omit `compute.auth` entirely.

Initial policy comes from `compute.max_machines`, `compute.slots_per_machine`
(default one), `compute.max_sessions_per_machine` and
`compute.max_machine_age_seconds` (both unset), `compute.warm_min` (default
zero), `compute.max_provisioning` (one), `compute.idle_timeout_seconds` (3600),
and `compute.provisioning_timeout_seconds` (600). The profile comes from the VM
pod manager. After initialization, policy is stored in PostgreSQL and admin
changes survive restarts. Editing those bootstrap defaults does not overwrite
an already-administered pool. All controllers must share its database,
credential store and workspace archive directory. SSH runtime controllers need
access to the same controller key and local workspace disk.

A spare has its own pinned SSH identity but no session binding, launch payload,
model credentials, session container or workspace. With `SshContainerVmRuntime`
it is booted, prepared over SSH and has the Skuld image pulled, so a session that
claims it starts in seconds rather than the minutes a cold VM takes (boot,
cloud-init, image pull). A transaction assigns it to one session at a time. The default `replace` policy archives the workspace/home,
deletes the guest and root disk, then replenishes with a clean allocation. The
`reuse` policy requires explicit runtime support: after archival and sandbox/data
cleanup, the same guest returns to the pool with its previous binding cleared.
OpenShell on Docker implements this path; the direct Docker runtime retains the
replacement policy. Failed or incompletely started guests are disposed after
preserving any session data.

Pause rejects new assignments and provisioning without interrupting sessions.
Drain additionally removes unbound spares. Lowering capacity retires excess idle
guests; it never terminates active sessions. With replacement policy, reducing
the free warm slots also retires excess empty machines immediately. Reuse policy
retains excess spares until idle expiry, within maximum capacity. Failed,
quarantined and deleting allocations continue consuming capacity. The database
admission lock applies policy across concurrent controllers, not just one process.

### Session slots, free warm slots and draining

A machine has `slots_per_machine` slots (**Sessions per machine**, default 1), and
each slot holds one session. One slot is the behaviour described above: a machine
serves one session. More than one slot per machine needs a runtime that keeps the
slots apart (`VmRuntime.supports_slots`; see "Separated session slots"). A VM Forge
runs one runtime, so every configured runtime must declare it; otherwise Forge
refuses a policy above one slot at startup and in the admin form, naming the
runtime and the remedy. The slot count is fixed when a machine is created, so
changing it applies to machines booted afterwards.

**Placement.** A new session takes a free slot of a ready machine whose profile and
host digest match its execution plan, before Forge boots a machine. Among the
candidates it picks the machine with the most occupied slots, then the oldest, so
sessions pack onto few machines and empty ones stay free to expire. The choice is
checked again under the machine's lock; a machine with a stop pending on any slot,
past its limits, or no longer idle is not offered. Only when no slot is free does
the session boot a machine (with `slots_per_machine` slots, itself in slot 0).
Every status check of a session already on a machine holds that lock for a moment,
so placement waits for a busy machine up to `podManager.kwargs.placement_lock_wait_seconds`
(default 15) before trying the next one; a machine skipped that way is logged and
counted on `volundr.compute.placement.busy_skips`.

**Free warm slots.** `warm_min` (**Free warm slots**) counts free, usable slots on
compatible machines that are provisioning, ready or idle, not machines. The pool
keeps at least that many; when sessions take slots and the count drops below it,
the pool boots `ceil((warm_min - free) / slots_per_machine)` machines, within
**Maximum machines** and **Concurrent provisioning**. With four slots, `warm_min: 1`
keeps one free slot and boots another machine when the last is taken, and
`warm_min: 4` keeps a whole machine's worth free. `warm_min` cannot exceed
`max_machines * slots_per_machine`. At one slot the count is the number of ready
spare machines, exactly as before.

Only an empty machine is ever disposed for being surplus or idle, and only while
the free slots left stay at `warm_min`; a machine with a session is never touched.
Idle expiry (`idle_timeout_seconds`) applies to empty machines above that count.

**Draining.** Under **Replace VM**, a machine takes new sessions until it has served
**Sessions before replacement** (`max_sessions_per_machine`; unset means one per
slot) or reached **Machine lifetime (seconds)** (`max_machine_age_seconds`; unset
means no limit). It then takes no new session, keeps serving the ones it has, and
is deleted once its last session stops; the warm rule above boots its replacement
as soon as its free slots stop counting. A limit above the slot count reuses
cleaned slots in place and needs a runtime that supports reuse. A machine past its
lifetime is not cleaned for another session. Both drain limits are rejected under
**Reuse cleaned VM**, and a limit below the slot count is rejected.

**Capacity shortfall.** When the pool needs machines to reach its free warm slots
but is at **Maximum machines**, it keeps running short rather than quietly: it
logs once at error when the shortfall starts, counts every hit on
`volundr.compute.warm.capacity_hits`, and shows the reason (for example
`3 of 4 free warm slots: the pool is at its maximum of 2 machines; raise the maximum
or lower the free warm slots`) as **Warm capacity** in Compute status and as
`capacity_shortfall` in the compute snapshot. Raise **Maximum machines** or lower
**Free warm slots**; the shortfall clears on the tick the target is met.

Compute status shows pool counts, reconciliation health, warm capacity, and one
row per machine with its state, profile, sessions in use out of its slots, and
each session's slot, state and id. Dispose unused machine lists only machines
without sessions; stop
session-owned machines through Forge to preserve their files. An owned machine
missing from the ledger is quarantined, never silently adopted or deleted.
Inspect its data before explicitly disposing it. A failed archive leaves the
machine intact, and the persisted stop intent is retried after controller restart.

Provisioning deadlines and provider retry times are durable. Retries use
`compute.retry_interval_seconds` (10) with exponential delay bounded by
`compute.retry_max_seconds` (300). Maintenance runs every
`compute.maintenance_interval_seconds` (10). It resumes partial provisioning,
cleanup and stop operations under allocation locks. Pool maintenance errors are
visible in admin settings; provider exception bodies are not exposed as they
may contain credentials.

With the existing Niuu observability exporter enabled, monitor
`volundr.compute.machines` by pool/state (a machine in service shows as `busy`),
`volundr.compute.slots` by pool/state (`starting`, `busy`, `failed`, `draining`,
`retired`, and `free`: the usable free slots on compatible machines, the number
`warm_min` is compared with), `volundr.compute.assignments` by
warm/cold source, `volundr.compute.warm.duration`,
`volundr.compute.warm.capacity_hits` (the pool is at its maximum and short of free
warm slots), `volundr.compute.reconcile.duration` and
`volundr.compute.reconcile.errors`.
A growing failed/draining/quarantined count explains unavailable capacity.
Admin operations require the existing `volundr:admin` role. Runtime session
routes keep the existing ownership, tenant and workload-identity authorization.

Before rollback, pause admission, stop sessions, drain spares and verify zero
unreleased allocations. Migration 000067 refuses downgrade with live claims.
Back up the PostgreSQL ledger, configured credential store and controller-local
workspace archives together. Guest disk loss before a successful stop/archive
is still outside the durability guarantee of controller-local storage.

Migration 000107 (`compute_slots`) moves each allocation's session into a slot
binding (slot 0) and stores machines as `idle` while in service. Stop older
controllers before applying it: they cannot read the new records and fail loudly,
as for 000068. Its down migration refuses to run while any allocation is
unreleased (pause admission, stop sessions, drain spares and verify zero
unreleased allocations first), then restores the old shape and drops
`compute_slots`.

## Local controller installation

The verified macOS installation keeps its checkout, configuration, PostgreSQL,
credential store, pinned SSH key and workspace archives together under
`~/.niuu/compute-controller`. User LaunchAgents
`world.niuu.compute-postgres` and `world.niuu.compute-controller` start at login
and restart failed processes. The controller checks database connectivity before
starting the normal Niuu root application and mounted Forge/Guild services.

The local UI is available at `http://127.0.0.1:8088/settings/volundr/compute`.
This installation uses the existing development identity on loopback. Production
exposure requires the normal configured identity and gateway deployment.
Configuration and credential files are private to the user; the Harvester token
stays on the controller and must be replaced before its expiry. No model refresh
credential is copied from the existing Spark credential service.

Restart the controller with
`launchctl kickstart -k gui/$(id -u)/world.niuu.compute-controller`.
Inspect logs under `~/.niuu/compute-controller/logs`. For an upgrade, pause
admission, finish or stop sessions, drain spares, update the installation checkout
and web build, apply required migrations, then restart and inspect Compute status
before reopening admission. Back up the database, credential store and session
archives together. This user-login installation is not a system boot daemon.


## Profile changes and warm-spare compatibility

Each provider catalog entry carries an opaque revision for its machine definition.
The shared pool persists that revision with the allocation and checks it before
assigning a spare. Changing a profile under the same name retires old unbound
guests and prepares replacements; it does not interrupt bound sessions. Older
allocations without a revision are never assigned as warm spares.

The Harvester revision covers image, resources, network, architecture, volume
access mode and provider/profile cloud-init configuration. The shared services
compare opaque values and never inspect Harvester fields. Private providers must
change the revision when their effective machine definition changes. Use immutable
image references or a new profile when changing image contents: an unchanged
reference cannot identify an out-of-band image mutation.

Migration 000068 backfills existing JSONB records with an empty revision. Stop
older controllers before upgrading the shared lease writer. Downgrade requires
pausing admission and releasing all allocations; its down migration removes the
new JSONB field so the older controller can read the ledger.


## Disposable runtime files in session archives

`SshContainerVmRuntime` accepts `archive_excludes`, a list of explicit paths
relative to the archived session root (`home/` and `workspace/`). Configure
`["home/.codex/tmp"]` for Codex sessions: this directory contains disposable
process launch wrappers that can point at absolute paths in the old container.
The same exclusions apply while writing new archives and restoring older ones.
No provider-specific behavior is involved, and credentials, conversation history
and workspace files remain under their existing preservation rules.

Extraction still uses Python's safe data filter; escaping links anywhere outside
configured exclusions fail loudly. Allocation completion markers are never
restored from an archive, so a partial extraction cannot masquerade as a completed
restore. A failed session can be stopped through the normal Forge API to preserve
its data and release its machine before retrying.

## Mounts inside read-only mounts

A container cannot create a mountpoint inside a read-only bind. The workload
identity token (`/var/run/secrets/niuu-workload`) lands inside the OpenBao
secrets volume (`/run/secrets`, read-only on Skuld), because `/var/run` is `/run`
in the Skuld image. `SshContainerVmRuntime` therefore creates each such mountpoint
in the parent volume's guest directory before the containers start, and the
parent stays read-only. It resolves targets through `image_path_links`, a map of
directory symlinks in the session images (default `{"/var/run": "/run"}`); set it
when an image links other directories. A mount inside a secret file is refused.

## OpenShell on managed VMs

Use `VmPodManager` with
`volundr.adapters.outbound.openshell_vm_runtime.OpenShellVmRuntime` as
`compute.runtime.adapter`. The machine provider remains independent of this
runtime. Harvester delivers its generated bootstrap through cloud-init; another
provider implements the same `MachineBootstrap` contract.

The image or provider bootstrap must supply Docker, Python 3.12+, OpenSSH and
OpenSSL. The runtime bootstrap installs the native gateway from `gateway_image`,
creates the guest's callback bridge and signing keys, validates `gateway_config` with
OpenShell's preflight, and starts the systemd service. No Kubernetes installation
is involved. Warm readiness requires an authenticated gateway RPC and a cached
sandbox image, before a session can claim the guest.

Alongside the SSH runtime's key paths, local `data_dir`, `skuld_image`, timeout
and callback settings, configure:

| Runtime kwarg | Purpose |
| --- | --- |
| `gateway_image` | Gateway image of the same OpenShell build as the TOML's supervisor and sandbox runtime images, matching the SDK (the Niuu fork publishes `ghcr.io/jve/openshell/gateway:<commit>`) |
| `gateway_config` | OpenShell version 2 TOML, selecting the Docker driver |
| `network_subnet` | Subnet of the guest's callback bridge; holds the gateway bind address |
| `callback_network_name` | Name of that Docker bridge (default `niuu-callback`); no sandbox joins it |
| `gateway_kwargs` | Existing OpenShell policy, OIDC token URL/client ID, sandbox command and resource limits |
| `gateway_client_secret` | OIDC client secret; supply through `compute.runtime.secret_kwargs_env` |

The TOML must require OIDC authentication and bind its plaintext endpoint to a
host address inside `network_subnet` (the callback bridge address; sandbox
egress cannot reach loopback). Under `[openshell.drivers.docker]` it must:

- set `sandbox_runtime_image` and `supervisor_image` to the same OpenShell build
  as `gateway_image` (since v0.1.6 the workload runtime and the supervisor are
  separate images, e.g. `ghcr.io/jve/openshell/sandbox:<commit>` and
  `ghcr.io/jve/openshell/supervisor:<commit>`);
- set `grpc_endpoint = "http://<bind address>"`: supervisors run on the host
  network and otherwise call back on `127.0.0.1`, where this gateway does not
  listen. Leave `supervisor_network_mode` unset (or `host`);
- enable `enable_bind_mounts` and `allow_driver_config`: the runtime mounts the
  guest's workspace and home into the sandbox as host binds through the driver
  config;
- disable `[openshell.drivers.docker.resource_admission]` (`enabled = false`):
  OpenShell cannot label host paths, so admission would reject those mounts;
- not set `network_name`, which v0.1.6 removed (sandbox workloads run with
  `network=none`).

```toml
[openshell]
version = 2

[openshell.gateway]
compute_driver = "docker"
bind_address   = "172.30.0.1:17670"
disable_tls    = true

[openshell.drivers.docker]
grpc_endpoint         = "http://172.30.0.1:17670"
sandbox_runtime_image = "ghcr.io/jve/openshell/sandbox:<commit>"
supervisor_image      = "ghcr.io/jve/openshell/supervisor:<commit>"
enable_bind_mounts    = true
allow_driver_config   = true

[openshell.drivers.docker.resource_admission]
enabled = false
```

The runtime refuses a configuration that misses any of these, naming the key to
set. Controller traffic travels through pinned SSH. Configure the gateway signing-key paths as
`/var/lib/openshell/jwt/signing.pem`, `public.pem`, and `kid`. The runtime owns the
per-guest endpoint and workspace/home mounts; do not duplicate those in
`gateway_kwargs`. Allow the bridge callback address and required model/broker
endpoints in the sandbox policy.

The guest has one session at a time. Workspace and CLI state live under
`/var/lib/niuu/session` and are mounted under `/sandbox/workspace` and
`/sandbox/home`. Session credentials are delivered only after binding, separately
from machine bootstrap. The existing Skuld credential broker and read-only secret
file injection apply. This VM runtime does not configure SPIFFE infrastructure;
OpenShell dynamic provider grants requiring it must use a deployment that supplies
that infrastructure.

### Stop, reuse and idle deletion

`compute.reuse_policy` initializes the pool policy. Admin settings expose the same
choice as **After session stop**:

- `reuse`: stop the sandbox, archive workspace/home to the controller's local disk,
  delete the OpenShell sandbox, remove guest session files, then clear the binding
  and return the same VM to idle capacity. New sessions receive fresh storage;
  restarting a previous session restores its own archive.
- `replace` (the backward-compatible default): archive the session and delete its
  VM. The warm minimum determines whether to provision a replacement.

Reuse requires a runtime that explicitly implements cleanup. A failed archive or
reset retains the binding and stop request for retry; the VM is never offered as
idle after incomplete cleanup. The provider and allocation record remain the
same across successful reuse. Retained surplus VMs expire after
`idle_timeout_seconds`; lowering maximum capacity or draining the pool can remove
them sooner. Free slots within the warm minimum never expire: their machines stay
ready until a session claims a slot, so an idle pool does not churn machines.
Closing a browser tab does not stop a session. Explicit stop, archive, and delete
operations use the session lifecycle's cleanup path.
