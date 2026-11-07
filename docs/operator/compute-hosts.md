# Registered hosts: bare-metal machines as Forge compute

A Forge with a compute pool (see [vm-compute.md](vm-compute.md)) can run sessions on
machines you already own instead of VMs it creates. Each machine joins once, with
one command, and becomes capacity of that Forge. It runs no Forge itself. Design:
[docs/design/bm-compute-hosts.md](../design/bm-compute-hosts.md).

A registered host is never created or deleted. Forge **claims** a free host for an
allocation and **audits** it when the allocation ends. A clean host is ready again;
a host with anything a session left behind is quarantined and shown with what was
found.

## Configure the Forge

A registered-host Forge uses the registered-host provider, an execution catalog
(each host runs one catalog execution), and a `compute.hosts` section:

```yaml
compute:
  pool_id: valhalla-hosts
  max_machines: 32                # at least the number of hosts you will register
  reuse_policy: reuse             # slots are cleaned and reused in place
  provider_binding: valhalla-hosts
  provider:
    adapter: volundr.adapters.outbound.registered_hosts.RegisteredHostMachineProvider
    kwargs:
      profiles:
        bm-host: {label: "Bare metal"}
  execution_catalog:
    adapter: volundr.adapters.outbound.file_execution_catalog.FileExecutionCatalog
    kwargs: {root: /etc/niuu/execution-catalog}
  guest_identity:                 # only for MetadataCredentialDelivery on 1-slot hosts
    adapter: volundr.adapters.outbound.guest_identity_host_key.HostKeyGuestIdentity
    kwargs: {key_source: registered}
  hosts:
    join_url: https://forge-hosts.valhalla.asgard.niuu.world
    connectors:
      - binding_id: direct-ssh
        adapter: volundr.adapters.outbound.host_connectors.DirectSshConnector
        kwargs:
          allowed_sources: ["192.168.90.240"]   # Forge's egress; [] accepts any source
    reserved_cpu: 4
    reserved_memory_mib: 8192
    min_free_disk_mib: 20480
```

In the catalog, the execution's machine names the provider profile (`bm-host`), and its
access binding is `SshGuestAccess` with `bootstrap_delivery: registered` and trust
mode `registered_host_key`. The host's own key is pinned, and nothing is trusted on
first use.

Startup fails if any of these hold:

- `compute.hosts` is set without the registered-host provider, or the reverse.
- There is no execution catalog.
- A configured runtime needs a root daemon on the machine. `SshContainerVmRuntime`
  and the OpenShell runtimes run under root Docker, so registered hosts use the host
  runtime, which uses rootless Podman per slot.

Pairing codes are workload JWTs, so `workload_identity.enabled: true` is required.

## Add a host

In the UI, open **Forge → Compute → Hosts → Add host**. Choose the runtime, tags, how
Forge reaches the host, and optionally the number of slots, then copy the command:

```bash
curl -fsSL -H 'Authorization: Bearer <code>' https://forge-hosts…/api/v1/forge/compute/hosts/join.sh | sudo sh
```

The host needs systemd, cgroup v2, OpenSSH server, `sudo`, `python3`, `iproute2` and
`curl` or `wget`. A stock Ubuntu server has all of these. The script checks them
first and changes nothing if any are missing. Then it:

1. Registers the host's **own** sshd Ed25519 key, linked as
   `/etc/niuu/ssh_host_ed25519_key`. sshd and everyone's `known_hosts` are left
   unchanged.
2. Sends the host's name, address, key and facts with the code, which is single use.
3. Installs Forge's key for the access principal, with `restrict,port-forwarding`
   and the connector's options (for example `from=`).
4. Writes `/etc/niuu/compute-host.env` and `/usr/local/sbin/niuu-host-leave`.

Forge then reaches the host with the pinned key, reads its facts and audits it. The
execution's host recipe (Nix, rootless Podman, the slot supervisor) runs when the pool
first claims the host, through the same versioned preparation a VM gets. If
you left the slot count open, the host waits as ready but unclaimable. The UI
suggests a count from the facts and the runtime's per-slot limits, and shows the
arithmetic. A slot count that does not fit is refused, or quarantines the host if
the facts change later.

## States

| State | Meaning |
|---|---|
| pending | Registered; Forge has not reached it yet (tried every `pending_check_interval_seconds`) |
| ready | Reached, audited clean, not held |
| in_service | Held by the pool; sessions are placed in its free slots |
| draining | Takes no new sessions; leaves service once its allocation is empty and audited |
| out_of_service | Nothing runs and nothing is placed; **Resume** audits it and makes it ready |
| quarantined | A check or audit found something; offered to no one until a check is clean |
| unreachable | Forge cannot reach it, or its key no longer matches; retried, never placed on |
| removing | Drain and remove: removed once empty |

Sessions on a host that becomes unreachable, quarantined or draining keep running.
Only new placement stops.

## Drain, remove, leave

- **Drain** stops new sessions. When the last session ends, the host is audited and
  goes out of service.
- **Drain and remove** does the same, then runs `niuu-host-leave` on the host over SSH
  and forgets the host.
- **Remove** works on a host that runs no session. If Forge cannot reach the host,
  the host is still removed from Forge. The response then says to run
  `sudo niuu-host-leave` on the host.

`niuu-host-leave` removes Forge's key, the key link and the config. Slot users and
their data are the audit's business.

## Tags and targeting

Hosts carry tags (`gpu`, `arm64`, `site:hnoss`). Sessions and workflows target them
with the same `target_tags` + `target_match` (`all` or `any`) they use to target a
Forge. The Guild picks a Forge whose own tags, together with **one** of its hosts' tag
sets, satisfy the selector. It forwards the part the Forge's tags do not cover as
`workload_config.host_selector`, and the Forge places the session only on a matching
host. If no host of the pool carries the tags, session create fails with the tag sets
the pool has. `GET /api/v1/forge/compute/hosts/tag-sets` lists them.

## Checks and the audit

When a host registers, every `check_interval_seconds` and when an allocation ends,
Forge runs two POSIX `sh` programs on the host as root (`sudo -n sh -s`). Only a
host whose last check was clean is claimed:

- **facts**: CPUs, memory, free disk, kernel, architecture, cgroup v2, GPUs and OS.
- **audit**: for every slot that runs no session, no `niuu-slot-*` user, process,
  file under `/tmp`, `/var/tmp`, `/dev/shm`, `/run/user` or the slot directories,
  network namespace, link, slice, IPC object, crontab or linger may remain. It also
  checks that no slot user is in `sudo`, `wheel`, `docker` or `adm`, that Nix
  `trusted-users` is root only, and that free space on `/` is at least
  `min_free_disk_mib`. When an allocation ends, the audit first clears that
  allocation's bootstrap markers.

A finding is an error log, a count on `volundr.compute.host.quarantined` (or
`volundr.compute.host.unreachable`), and a red row with the finding. There is no
"ignore": the host comes back when a check no longer finds it. If the audit cannot
reach the host when an allocation ends, the allocation stays draining and the delete
is retried. Capacity is never freed on a host nobody checked.

## Knobs

| Knob | Default | Bounds | On a hit |
|---|---|---|---|
| `compute.hosts.pairing_code_ttl_seconds` | 600 | How long a minted code can be used. Also bounded by the workload token's own lifetime | The join is refused (401); mint a new code |
| `compute.hosts.check_interval_seconds` | 300 | How often every host is audited | — |
| `compute.hosts.pending_check_interval_seconds` | 5 | How often a pending or unreachable host is retried; also the maintenance tick | — |
| `compute.hosts.command_timeout_seconds` | 120 | One facts read or audit on a host | The check fails: the host is unreachable, with the reason |
| `compute.hosts.reserved_cpu` / `reserved_memory_mib` | required | What slot sizing leaves for the host itself | Too many slots is refused (422) or quarantines the host, with the arithmetic |
| `compute.hosts.min_free_disk_mib` | required | Free space on `/` | Quarantined until space is freed |

## Not built yet

- The reverse SSH connector for hosts behind NAT (design: "Connection"). Today a
  host must be reachable from Forge.
- `warm_targets` per tag selector. `warm_min` counts free slots across the pool.
- macOS hosts.
