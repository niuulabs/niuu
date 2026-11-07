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

### Mini mode: hosts next to this machine's sessions

On one machine running `niuu platform up`, set this in `~/.niuu/config.yaml`:

```yaml
hosts:
  enabled: true
  execution_catalog: ~/.niuu/hosts/execution-catalog
  join_url: http://192.168.1.10:8080
  allow_plaintext_join: true
```

This machine keeps running its own sessions. The CLI wraps the configured pod
manager in `PlacementPodManager` as `local` and adds a `VmPodManager` on the
registered-host pool as `pool`. It also renders the `compute:` section above with
these defaults: pool `local-hosts`, profile `host`, `max_hosts: 16`,
`reuse_policy: reuse`, one `direct-ssh` connector (`allowed_sources: []`),
`reserved_cpu: 2`, `reserved_memory_mib: 4096`, `min_free_disk_mib: 20480`, and the
pairing key file under `~/.niuu`. Each of these is a `hosts.*` key. If `compute:` is
also set, or the mode is not mini, the CLI refuses the config.

A session runs on a host only when it asks for one, with
`workload_config.host_selector` or an `execution_profile` of the hosts catalog. Every
other session runs on this machine. The session keeps that placement for life,
through the execution pin. A host session that cannot be placed waits or fails, and
never runs locally. The reverse also holds.

Until Niuu ships a default host-mode execution, `hosts.execution_catalog` is required.
It is a catalog whose execution's machine names profile `host` and whose runtime is
the rootless host runtime.

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

To read the script before it runs, choose **Download and read first**. The UI then
shows the script's SHA-256, a button that downloads the same file, and these commands
for the host. They fetch the script, check the digest, show it, and run it only once
you have read it:

```bash
curl -fsSL -o niuu-join.sh -H 'Authorization: Bearer <code>' https://forge-hosts…/api/v1/forge/compute/hosts/join.sh
echo '<sha256>  niuu-join.sh' | sha256sum -c -
less niuu-join.sh
sudo sh niuu-join.sh
```

Forge renders the script for each code, so the digest is the one for this code. The
mint response (`POST …/pairing-codes`) carries it as `script.sha256`, with the script
itself and these commands.

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

### From your own machine: `niuu remote join`

With the `niuu` CLI you can join a host from your own terminal over your own SSH.
Nothing is pasted on the host:

```bash
niuu remote join admin@gpu-box --execution host-podman --tag gpu --slots suggested
```

The CLI mints a code as you, runs the same command the UI shows on the host
(`sh -c '…'` over `ssh`, so `sudo` can ask for your password), then follows the host
until it is ready:

```text
✓ minted pairing code for host-podman on Ymir (expires 2026-11-07T10:42:00+00:00)
→ admin@gpu-box: running join.sh
  ✓ Linux 6.8.0-49-generic, systemd, cgroup v2, OpenSSH, python3
  ✓ registered as gpu-box with the Forge's pool hosts
✓ joined       gpu-box
… connected    key pinned
✓ connected    key pinned
… preparing    recipe host-podman
✓ preparing    recipe host-podman
✓ audit
… capacity
  set 6 slots (Forge's suggestion)
✓ capacity     6 slots
✓ ready        tags: gpu
Next: `niuu remote hosts gpu-box` · leave with `niuu remote leave gpu-box`
```

- **Which host:** any `ssh` destination: `user@host`, a `~/.ssh/config` alias, or
  `ssh://user@host:port`. Use `-p` and `-J` for the port and a jump host; keys
  come from your SSH config and agent.
- **Which Forge:** the active context's Niuu host (`local`, this machine's own
  server, unless you switched). Add a shared one once, then log in:

  ```bash
  niuu context add ymir https://niuu.ymir.example --issuer https://idp.example/realms/niuu --use
  niuu login
  ```

  `--url` replaces the context's URL for one run. Use `--forge` (or `--forge` on
  `niuu context add`) to pick a Forge by id, slug or name when the Niuu host has
  more than one.
- **Login:** run `niuu login` first, unless the Forge is a local mini stack without
  identity. A context with an issuer refuses a login from another issuer.
- **Flags:** `--execution`, `--connector`, `--tag` (repeat it), `--slots N|suggested`
  and `--isolation measured|systemd|cgroupfs|none`, as in the Add host form. You can
  leave out `--execution` and `--connector` when the Forge offers only one of each.
- **Exit codes:** 0 when the host is ready, or waits for its slot count (the CLI
  prints the suggestion and the command to set it). 1 when the host is quarantined
  or unreachable (with Forge's findings), when the script fails, or when it is not
  ready within `--timeout` (default `remote.follow_timeout_seconds`).

`niuu remote hosts` lists the pool (`--json` for scripts), and `niuu remote hosts <host>`
shows one host's steps and findings. `niuu remote slots <host> <n>` sets a slot count.
`niuu remote leave <host>`
drains and removes a host, waits until its sessions end and Forge has forgotten
it, then runs `niuu-host-leave` on it over your SSH (`--ssh` when the host's name is
not an SSH destination).

## States

| State | Meaning |
|---|---|
| pending | Registered; Forge has not reached it yet (tried every `pending_check_interval_seconds`) |
| ready | Reached, audited clean, not held |
| in_service | Held by the pool; sessions are placed in its free slots |
| draining | Takes no new sessions; leaves service once its allocation is empty and audited; **Cancel drain** returns it to service |
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

- **Cancel drain** returns a draining host to service at once
  (`POST …/hosts/{id}/resume`). A host set to drain and remove cannot be cancelled.

`niuu-host-leave` removes Forge's key, the key link and the config. Slot users and
their data are the audit's business.

## Re-prepare

A host's recipe runs once per allocation: the allocation that holds the host pins the
execution plan, carries the preparation result and keys the checkpoint on the host.
While an allocation holds the host, later claims only verify the recipe; they do not
run it again.

**Re-prepare** (`POST /api/v1/forge/compute/hosts/{id}/reprepare`) has the next claim
run the execution's current recipe again. It works only on a host that runs no
session; otherwise it answers 409 and says to drain the host first. If an idle
allocation holds the host, Forge disposes of it the way the pool disposes of any idle
allocation whose plan changed: the allocation is deleted, the host is audited and
released, and its service setting is kept. The next claim pins the current plan and
runs every recipe stage against a fresh checkpoint. Re-prepare then checks the host,
so a finding shows at once. A host that no allocation holds has no preparation to
discard; Re-prepare only checks it, and its next claim prepares it anyway.

If Forge cannot reach the host to audit it, Re-prepare answers 409 with the reason.
The allocation stays draining, and the pool retries the release.

A host that should change recipe while it runs sessions: **Drain**, wait until it is
out of service, **Re-prepare**, then **Resume**.

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
| `context` / `contexts` (CLI) | `local` | The Niuu host every CLI command talks to (`niuu context`); `--url` per run on `niuu remote` | An unconfigured active context fails every such command, naming `niuu context use` |
| `remote.follow_timeout_seconds` (CLI) | 900 | How long `niuu remote join` follows a host to ready; `--timeout` per run | Exit 1, naming the host's step; Forge goes on |
| `remote.leave_timeout_seconds` (CLI) | 3600 | How long `niuu remote leave` waits for sessions to end; `--timeout` per run | Exit 1; Forge still removes the host once it is empty |
| `remote.poll_interval_seconds` (CLI) | 2 | How often `niuu remote` re-reads the host | — |
| `compute.hosts.pairing_signing_key_file` | `~/.niuu/forge-host-pairing-key.pem` | Used only without a workload-identity key, with the allow-all identity adapter | Unreadable or unwritable: startup fails, naming the knob |
| `compute.hosts.join_listener.bind_host` / `port` | `0.0.0.0` / `8443` | Where the pinned join listener listens | Cannot bind: startup fails, naming the knob |
| `compute.hosts.join_listener.cert_file` / `key_file` | `~/.niuu/forge-host-join-{cert,key}.pem` | The listener's certificate and the key the pin comes from | Unreadable, readable by others or not matching: startup fails |
| `compute.hosts.join_listener.key_bits` | 2048 | Size of a generated key | — |

## Hosts behind NAT: the reverse SSH connector

A host Forge cannot dial keeps one outbound SSH connection to the Forge instead.
`niuu-host-connect.service` on the host runs stock OpenSSH,
`ssh -N -R 127.0.0.1:<port>:127.0.0.1:22 niuu-connect@<endpoint>`, logging in with the
host's own key and pinning the endpoint's key, and restarts it whenever it drops. The
endpoint is an sshd sidecar in the Forge pod, sharing Forge's loopback, so the forwarded
port is reachable by Forge and nothing else. Forge then reaches the host's sshd through
it with the host's registered key pinned, as for direct SSH.

1. Create the endpoint's host key once, as a Secret:

   ```bash
   ssh-keygen -t ed25519 -N '' -C forge-connect -f forge-connect
   kubectl -n <ns> create secret generic forge-connect-host-key --from-file=ssh_host_ed25519_key=forge-connect
   ```

2. Enable it in the volundr chart. It needs exactly one Forge pod:

   ```yaml
   replicaCount: 1
   strategy:
     type: Recreate        # a rolling update would split the tunnels between two pods
   hostConnect:
     enabled: true
     hostKeySecret:
       name: forge-connect-host-key
     service:
       type: LoadBalancer  # or tcpRoute.enabled with parentRefs to a TCP Gateway listener
   ```

   The sidecar runs as root (sshd separates each connection into the unprivileged
   `niuu-connect` user), so the namespace must admit Pod Security `baseline`.

3. Add the connector:

   ```yaml
   compute:
     hosts:
       connectors:
         - binding_id: reverse-ssh
           adapter: volundr.adapters.outbound.host_connectors.ReverseSshConnector
           kwargs:
             endpoint: hosts-connect.valhalla.asgard.niuu.world:2222  # as hosts dial it
             endpoint_host_key: "ssh-ed25519 AAAA…"  # forge-connect.pub, without the comment
             port_range: "20000-20999"
   ```

Hosts joining with this connector need nothing reachable from outside. On join, a
host gets the next free port of `port_range`; Forge's key on the host is limited to
`from="127.0.0.1,::1"`, where Forge's sessions arrive.

**What the endpoint admits.** On every connection its `AuthorizedKeysCommand` asks
Forge, on a socket in a volume only Forge and the sidecar mount, for the key's line.
Only the key of a registered reverse-SSH host gets one, as `niuu-connect` only:
`restrict,port-forwarding,permitlisten="127.0.0.1:<its port>",command="/usr/sbin/nologin"`.
The sidecar's `sshd_config` also refuses local forwarding for everyone
(`AllowTcpForwarding remote`, `PermitOpen none`). A host can listen on its own
loopback port and do nothing else.

**Health.** Forge reads every tunnel's listener on each maintenance tick. A dropped
tunnel makes the host unreachable at once, is logged at error and counted on
`volundr.compute.host.disconnects`. Its sessions keep running; when the unit
reconnects, the next check makes the host healthy again.

**Removing.** Removing the host stops Forge answering for its key at once, and asks the
sidecar to end its live connection (a failure to do so is an error, counted on
`volundr.compute.host.revoke_failures`). `niuu-host-leave` disables the unit and
removes it and the endpoint's pinned key; it does not stop a running tunnel, since
Forge may run leave through it. Without the pinned key the unit does not start again.
To end it at once on the host: `sudo systemctl stop niuu-host-connect.service`.

| Knob | Default | Bounds | On a hit |
|---|---|---|---|
| `port_range` (connector) | required | Ports on the endpoint's loopback, one per reverse-SSH host of the Forge | The join is refused (422) naming `port_range`, counted on `volundr.compute.host.port_range_full` |
| `server_alive_interval_seconds` (connector) | 15 | How often the host's ssh checks the endpoint is there | — |
| `reconnect_seconds` (connector) | 5 | Wait before the unit reconnects | — |
| `revoke_timeout_seconds` (connector) | 10 | How long removal waits for the endpoint to end a connection | Removal fails with the reason, counted on `volundr.compute.host.revoke_failures` |
| `hostConnect.keyLookupTimeoutSeconds` (chart) | 5 | One key lookup from sshd to Forge | The connection is refused; the host retries |
| `hostConnect.clientAliveIntervalSeconds` × `clientAliveCountMax` (chart) | 15 × 3 | How long a silent host keeps its port | The connection is dropped; the host reconnects |

Startup fails if the connector is configured without the endpoint (`runtime_dir` is
missing), and the chart refuses `hostConnect` with more than one replica, autoscaling,
a rolling update or no host key Secret.

## Not built yet

- macOS hosts.
- **Sessions on a Mac.** A Mac runs the same join line and is checked and audited
  (design: "macOS hosts"), always with one slot, but no runtime runs on macOS yet,
  so the join refuses it: "… does not run sessions on macOS hosts".
