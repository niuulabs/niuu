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
    join_url: https://forge-hosts.valhalla.asgard.niuu.world   # unset: see Local mode
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

Pairing codes are short-lived scoped JWTs. Which key signs them:

- **Workload identity with a configured key** (`signing_key_pem` or
  `signing_key_env`). Clusters use this, because Envoy validates the code.
- **Forge's own key file** (`compute.hosts.pairing_signing_key_file`, generated
  once with mode 0600). Local mini mode uses this, with the allow-all identity
  adapter. Nothing else is needed: one machine running `niuu platform up` can add
  hosts without any identity setup.

Any other combination fails at startup and names the fix.

## Where hosts join

Joining sends a pairing code that leads to root on the host, so the join never
crosses the network in clear unless you ask for that.

- **`join_url` set, no `join_listener`** (clusters): Forge's API serves the two join
  routes, and `join_url` is its https address behind an ingress with a real
  certificate. `http://` is refused unless `allow_plaintext_join: true`.
- **`join_listener`**: Forge serves the two join routes on its own TLS listener, and
  only there; the API stops serving them. The certificate is self-signed and the join
  command pins its key, so no CA is needed. Hosts reach it at `join_url` when set
  (for example a port forward to the listener), else at
  `https://<server_public_host>:<port>`.
- **Neither**: Forge runs `join_listener` with its defaults. This is local mode.

## Local mode

A Forge run by `niuu platform up` serves plain http on :8080 and has no CA. Leave
`join_url` unset and Forge serves the join itself, pinned:

```yaml
compute:
  hosts:
    connectors: […]          # as above
    reserved_cpu: 2
    reserved_memory_mib: 4096
    min_free_disk_mib: 20480
    # join_listener:          # all optional; these are the defaults
    #   bind_host: 0.0.0.0
    #   port: 8443
    #   cert_file: ~/.niuu/forge-host-join-cert.pem
    #   key_file: ~/.niuu/forge-host-join-key.pem
```

- The URL host is `server_public_host`: what `server.external_host` in the niuu
  config sets for `niuu platform up` (or `NIUU_SERVER_PUBLIC_HOST`). Set it to the
  LAN address or name your other machines reach this one at. A loopback or
  unspecified address (`127.0.0.1`, `localhost`, `0.0.0.0`) cannot be reached from
  another machine, so startup fails and names both fixes. An IPv6 address is
  refused too; use a name.
- On first start Forge generates an RSA key and a self-signed certificate (both
  mode 0600, the directory 0700). It never overwrites them and reuses them on every
  restart, so the pin stays the same. Delete only the certificate and it is made
  again from the key with the same pin. Delete the key and a new pin is made: mint
  new commands. A certificate that does not belong to the key, or a file other users
  can read, stops startup.
- A listener that cannot load its key or bind its port stops startup. If it ends
  while Forge runs, that is an error log and a count on
  `volundr.compute.hosts.join_listener_stopped`, and hosts cannot join until Forge
  restarts.
- The command becomes:

  ```bash
  curl -fsSL -k --pinnedpubkey sha256//<pin> -H 'Authorization: Bearer <code>' https://192.168.1.20:8443/api/v1/forge/compute/hosts/join.sh | sudo sh
  ```

  `-k` skips the CA check, which a self-signed certificate cannot pass, and
  `--pinnedpubkey` is the trust: curl refuses any server whose key is not the
  pinned one before it sends the code. `join.sh` posts the join with the same pin.
  wget cannot check a pin, so a pinned join offers no wget line, and the script
  stops and changes nothing if the host has no curl. The Add host screen shows the
  join URL and the pin under the command.
- Open the listener's port (8443 by default) to your hosts in the Forge machine's
  firewall. The listener serves only `join.sh` and `join`: no UI, no API docs, no
  admin route.

## Add a host

In the UI, open **Forge → Compute → Hosts → Add host**. Choose the runtime, tags, how
Forge reaches the host, and optionally the number of slots, then copy the command:

```bash
curl -fsSL -H 'Authorization: Bearer <code>' https://forge-hosts…/api/v1/forge/compute/hosts/join.sh | sudo sh
```

The host needs systemd, cgroup v2, OpenSSH server, `sudo`, `python3`, `iproute2` and
`curl` or `wget` (`curl` for a pinned join, see Local mode). A stock Ubuntu server has all of these. The script checks them
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
| `compute.hosts.pairing_signing_key_file` | `~/.niuu/forge-host-pairing-key.pem` | Used only without a workload-identity key, with the allow-all identity adapter | Unreadable or unwritable: startup fails, naming the knob |
| `compute.hosts.join_listener.bind_host` / `port` | `0.0.0.0` / `8443` | Where the pinned join listener listens | Cannot bind: startup fails, naming the knob |
| `compute.hosts.join_listener.cert_file` / `key_file` | `~/.niuu/forge-host-join-{cert,key}.pem` | The listener's certificate and the key the pin comes from | Unreadable, readable by others or not matching: startup fails |
| `compute.hosts.join_listener.key_bits` | 2048 | Size of a generated key | — |

## Not built yet

- The reverse SSH connector for hosts behind NAT (design: "Connection"). Today a
  host must be reachable from Forge.
- **Re-prepare.** After a recipe change, drain the host and resume it; the next claim
  runs the new recipe.
- **Sessions on a Mac.** A Mac runs the same join line and is checked and audited
  (design: "macOS hosts"), always with one slot, but no runtime runs on macOS yet,
  so the join refuses it: "… does not run sessions on macOS hosts".
