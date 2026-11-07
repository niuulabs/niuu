# Registered hosts: bare-metal machines as Forge compute

A Forge with a compute pool (see [vm-compute.md](vm-compute.md)) can run sessions on
machines you already own instead of VMs it creates. Each machine joins once, with
one command, and becomes capacity of that Forge. It runs no Forge itself. Design:
[docs/design/bm-compute-hosts.md](../design/bm-compute-hosts.md).

A registered host is never created or deleted. Forge **claims** a free host for an
allocation and **audits** it when the allocation ends. A clean host is ready again;
a host with anything a session left behind is quarantined and shown with what was
found.

## Two commands

Bringing your own machines needs no configuration of its own:

1. **Bring the stack up.** `niuu platform up` on one machine (mini mode), or a Forge
   from the volundr chart with `computeHosts.enabled: true` and its `joinUrl`.
2. **Join a machine.** `niuu remote join <ssh destination>` from your terminal, or
   **Forge → Compute → Hosts → Add host** and the command it shows. Linux machines and
   Macs join the same pool; each runs sessions the way its platform needs.

Forge generates the SSH key it reaches hosts with (below), runs Niuu's **built-in host
execution** and pins the release's own session images. There is no secret store to
fill and no execution catalog to write.

### The built-in host execution

`volundr.adapters.outbound.builtin_host_catalog.BuiltinHostCatalog` is the execution
catalog for a pool of registered hosts. It holds one execution, `host`:

- **Machine:** the registered-host provider's profile.
- **Access:** `SshGuestAccess` as `root`, `bootstrap_delivery: registered`, trust
  `registered_host_key`: the host's own sshd key, registered at join, is pinned, and
  nothing is trusted on first use. Forge's key is its own generated one.
- **Host recipe:** Nix (the pinned release of `deploy/nix/install-nix.sh`) on every
  platform, and on Linux the slot tools and rootless Podman (`iproute2`, `iptables`,
  `podman`, `uidmap`, `passt`, installed with apt or dnf). The scripts ship with Niuu.
- **Runtime:** `PlatformHostRuntime`, which holds `SshHostVmRuntime` for Linux and
  `SshDarwinHostRuntime` for macOS and hands each session to the runtime of its
  host's platform (recorded at join). A Linux host runs several sessions at once, each
  in its own slot (user, network namespace, cgroup), with **no container daemon**; a
  Mac runs one, as a fresh macOS user. Both reuse a slot in place once it is proven
  clean (see Slots and reuse).

Its settings are the deployment's own: pool and provider binding, the provider
profile, `data_dir` (Forge's durable directory for pins, its key and archives),
`platform_port` (where Skuld calls back through its SSH tunnel), which `platforms`
may join, the per-slot limits (`slot_cpu` 2, `slot_memory_mib` 8192, `slot_pids`
16384; `null` for none), and optionally `default_service_account`,
`preview_router_auth` and `native_images`.

**Session images** are not catalog content. Forge hands the host runtimes the
release's own (`compute.session_images.linux`, the OpenShell image, whose Python is
relocatable, and `.darwin`, the macOS session bundle) and pins each tag to its digest
when it starts. A release bump therefore moves them without changing the plan running
sessions are pinned to. A tag the registry does not have, or a registry Forge cannot
reach, stops startup with the reference and the reason. Images must be public.

### On a cluster: `computeHosts`

```yaml
computeHosts:
  enabled: true
  joinUrl: https://forge-hosts.valhalla.asgard.niuu.world   # this Forge's ingress
  allowedSources: ["192.168.90.0/24"]                      # Forge's egress; [] for any
guestMetadata:
  enabled: true              # session credentials (OpenBao, ServiceAccount tokens)
```

That is the whole hosts configuration. The chart renders `podManager` (a `VmPodManager`
on the pool) and `compute` (the registered-host provider, the built-in catalog, the
direct-SSH connector, and with `guestMetadata` the metadata credential delivery and
host-key guest identity), and refuses `compute` beside it. The session images default
to `<image.registry>/openshell:<image.tag>` and `<image.registry>/skuld-darwin:<image.tag>`,
the ones published with this release; `computeHosts.sessionImages` replaces them.
`platformPort` is the pod's Envoy port when Envoy is on. Forge's data lives under
`computeHosts.dataDir` on the sessions volume, so its key and pins survive restarts.

### Your own catalog

A pool can run executions you write instead. It then uses the registered-host provider,
an execution catalog (each host runs one catalog execution), and a `compute.hosts`
section:

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

The access binding's `ssh_private_key_file` and `ssh_public_key_file` are optional. Set
both to use a key you manage; set only one and startup fails. Set neither and Forge
makes its own: on first start it generates an Ed25519 key at
`<data_dir>/host-access/id_ed25519` (and `.pub`), where `data_dir` is the binding's
own kwarg. The private key is mode 0600 in a 0700 directory and is never overwritten;
every restart reuses it. Keep `data_dir` on a persistent volume (on a cluster, the
Forge's sessions volume). Delete the files and Forge makes a new key, so every host must
join again. Forge logs which key it uses and the public key's `SHA256:` fingerprint,
never the private key. A pair that is incomplete, mismatched, not Ed25519, or a private
key readable by others stops startup, naming the path and the fix.

Startup fails if any of these hold:

- `compute.hosts` is set without the registered-host provider, or the reverse.
- There is no execution catalog.
- A configured runtime needs a root daemon on the machine. `SshContainerVmRuntime`
  and the OpenShell runtimes run under root Docker, so registered hosts use the host
  runtime, which runs no daemon.
- A host runtime has no session image: neither its own `skuld_image` (or
  `skuld_bundle`) nor `compute.session_images`.

Pairing codes are short-lived scoped JWTs. Which key signs them:

- **Workload identity with a configured key** (`signing_key_pem` or
  `signing_key_env`). Clusters use this, because Envoy validates the code.
- **Forge's own key file** (`compute.hosts.pairing_signing_key_file`, generated
  once with mode 0600). Local mini mode uses this, with the allow-all identity
  adapter. Nothing else is needed: one machine running `niuu platform up` can add
  hosts without any identity setup.

Any other combination fails at startup and names the fix.

### Mini mode: hosts next to this machine's sessions

`niuu platform up` serves a pool of registered hosts by default, with nothing in
`~/.niuu/config.yaml`. This machine keeps running its own sessions. The CLI wraps the configured pod
manager in `PlacementPodManager` as `local` and adds a `VmPodManager` on the
registered-host pool as `pool`. It also renders the `compute:` section above with
these defaults: pool `local-hosts`, profile `host`, `max_hosts: 16`,
`reuse_policy: reuse`, one `direct-ssh` connector (`allowed_sources: []`),
`reserved_cpu: 2`, `reserved_memory_mib: 4096`, `min_free_disk_mib: 20480`, and the
pairing key file under `~/.niuu`, and Forge's host data (its key, the hosts' pins,
archives) in `hosts.data_dir` (`~/.niuu/hosts`). Each of these is a `hosts.*` key.

- **Where hosts join:** the pinned join listener at `hosts.join_host`, else
  `server.external_host`, else the address of this machine's default route. A loopback
  address is refused, and so is a machine with no default route; both name the fix.
  `hosts.join_url` replaces the listener.
- **What hosts run:** the built-in host execution, with `hosts.session_images`
  (`ghcr.io/niuulabs/openshell:dev` and `ghcr.io/niuulabs/skuld-darwin:dev` by default,
  pinned when Forge starts, so starting needs the registry).
- **Off:** `hosts.enabled: false`. With `compute:` set, or in another mode, the pool is
  off unless `hosts.enabled: true`, which the CLI then refuses.

A session runs on a host only when it asks for one, with
`workload_config.host_selector` or an `execution_profile` of the hosts catalog. Every
other session runs on this machine. The session keeps that placement for life,
through the execution pin. A host session that cannot be placed waits or fails, and
never runs locally. The reverse also holds.


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

## Linux hosts

A Linux host runs sessions in host mode (`SshHostVmRuntime`; the details are in
[vm-compute.md](vm-compute.md#host-mode)): Skuld and the agent CLI run as the slot's
user on the host's own filesystem, toolchain and Nix store. Nothing on the host runs as
a daemon for Forge:

- each slot is a user `niuu-slot-<n>`, a named network namespace with its own veth and
  NAT, and a systemd slice `niuu_slot_<n>.slice` with the slot's limits;
- the session image (the OpenShell image) and the credential agents' images are fetched
  from their registries by digest and unpacked under `/var/lib/niuu/host-images`;
- the credential agents are systemd units `niuu-slot-<n>-sidecar-<name>` in the slot's
  slice, and the session runs in a delegated systemd scope there, with rootless Podman
  for the containers it builds and runs.

The host needs systemd, cgroup v2, `iproute2`, `iptables`, `curl` and a `python3` with
tarfile extraction filters (3.10.12, 3.11.4, 3.12 or later); the host recipe installs
the packages it can with apt or dnf. No Docker is needed, and one that is there is
never given to a slot.

## Slots and reuse

A stopped session's slot is torn down at stop: its processes, units, namespace, rules,
slice, user and directories go, and the stop fails, naming what is left, unless nothing
of the slot remains. With `reuse_policy: reuse` (the built-in pool's), the slot then
takes the next session in place: before that, Forge checks the slot again and runs the
audit (below) for that slot alone. A finding, or a slot Forge cannot check, retires the
slot instead: it takes no other session, the failure is logged at error and counted on
`volundr.compute.slot.recycle_failures`, and when it held the host's last session, the
host's allocation drains and the host is audited as a whole.

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
| access binding `ssh_private_key_file` / `ssh_public_key_file` | unset: `<data_dir>/host-access/id_ed25519` | The key Forge reaches hosts with. Unset, Forge generates it once (0600) and reuses it; set both to use your own | Only one set, an incomplete or mismatched pair, a key that is not Ed25519, or a private key readable by others: startup fails, naming the path |
| `compute.hosts.pairing_signing_key_file` | `~/.niuu/forge-host-pairing-key.pem` | Used only without a workload-identity key, with the allow-all identity adapter | Unreadable or unwritable: startup fails, naming the knob |
| `compute.hosts.join_listener.bind_host` / `port` | `0.0.0.0` / `8443` | Where the pinned join listener listens | Cannot bind: startup fails, naming the knob |
| `compute.hosts.join_listener.cert_file` / `key_file` | `~/.niuu/forge-host-join-{cert,key}.pem` | The listener's certificate and the key the pin comes from | Unreadable, readable by others or not matching: startup fails |
| `compute.hosts.join_listener.key_bits` | 2048 | Size of a generated key | — |
| `compute.session_images.linux` / `.darwin` | chart: the release's tag; mini: `…:dev` | What host runtimes without an image of their own run; a tag is pinned at startup | A tag the registry lacks or a registry Forge cannot reach: startup fails, naming the reference |
| `compute.session_images.resolve_timeout_seconds` | 30 | One registry request while pinning a tag at startup | Startup fails with the reference |
| built-in catalog `slot_cpu` / `slot_memory_mib` / `slot_pids` | 2 / 8192 / 16384 | What one Linux slot may use (`null`: no limit); slot counts are suggested from them | The slot's cgroup holds the session to them; a hit is counted on `volundr.compute.slot.limit_hits` |
| `hosts.join_host` (CLI) | `server.external_host`, else the default route's address | Where other machines reach this one to join | Loopback, or no default route: `niuu platform up` stops, naming the knob |

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

## Macs as hosts

A Mac (Apple silicon; proven on macOS 26) joins with the same line as a Linux host and
always runs **one session at a time**: macOS has no network namespaces or cgroups, so
the only boundary is a user. Design: "macOS hosts" in
[bm-compute-hosts.md](../design/bm-compute-hosts.md).

**Before joining**, on the Mac: turn on Remote Login (System Settings → General →
Sharing), install the Command Line Tools (`xcode-select --install`; the join and Forge
use Apple's `python3` and `git`), and make sure Forge's login is allowed by Remote
Login. The darwin execution logs in as `root` with its key, which macOS's default
`PermitRootLogin prohibit-password` allows.

**The execution.** The built-in host execution runs Macs with no setting of its own.
In a catalog of your own, the runtime is `SshDarwinHostRuntime`, the only runtime that
names `darwin` (alone, or as the `darwin` child of a `PlatformHostRuntime`):

```yaml
runtime:
  binding_id: macos
  adapter: volundr.adapters.outbound.ssh_darwin_host_runtime.SshDarwinHostRuntime
  kwargs:
    platform_port: 8443          # Forge's callbacks, through the SSH reverse tunnel
    guest_platform_port: 18080   # the Mac's loopback end of that tunnel
    nix: true                    # the host recipe installs multi-user Nix
    native_images:               # pod images whose commands the bundle carries
      - quay.io/openbao/openbao@sha256:fdc6da21…   # OpenBao 2.5.3, the agent's bao
  secret_kwargs_env: {skuld_bundle: FORGE_MACOS_SKULD_BUNDLE}
```

Its access binding is `SshGuestAccess` with `ssh_user: root`, and its connector's
`permit_listen` must include `127.0.0.1:18080` and `permit_open` `127.0.0.1:8081`
(Skuld's port on the Mac), because a Mac session listens on the Mac's loopback rather
than a slot address.

**The session bundle.** Instead of the Skuld image, a Mac runs the bundle
`ghcr.io/niuulabs/skuld-darwin@sha256:…` (`compute.session_images.darwin`, or
`skuld_bundle` in a catalog of your own), built on a macOS runner by the Dev workflow
(`.github/workflows/dev.yaml`, from `containers/skuld-darwin/build-bundle.sh`), scanned,
and tagged `dev-<sha>` with an attested digest only once the tests pass: CPython (with
the same stdlib security backports as the Linux images' Python) with niuu, Node and the agent CLIs
(Claude Code, Codex, OpenCode, Grok, pi, Muse) at the versions of the Skuld image,
OpenBao's agent and uv, every download pinned by SHA-256. Forge fetches it onto the Mac
with an anonymous registry pull, checks both digests, and unpacks it once per digest
under `/var/lib/niuu/host-bundles`; it removes older ones when no session runs. The
bundle is about 1 GB.

**A session**, step by step:

1. Forge creates the user and group `niuu-slot-0` (uid `uid_base`, default 61000,
   hidden, no password, no admin or Remote Login groups), its storage in
   `/var/lib/niuu/slots/0` and root's directory for it in `/var/run/niuu/slots/0`.
2. It restores the workspace archive, if there is one, and opens the SSH tunnel.
3. The pod's init containers run once as root, natively, and its sidecars (the guest
   credential agent, OpenBao's agent) become launchd jobs `world.niuu.slot.0.<name>`.
   A pod container whose image is neither the bundle nor in `native_images` is refused
   when the session is created.
4. The session is the launchd job `world.niuu.slot.0` in the system domain. It opens
   Skuld's log as root (`/var/log/niuu/host-sessions/0/`), becomes the slot user and
   runs Skuld from the bundle.

On stop Forge boots out the jobs, archives the workspace, kills whatever the user still
runs, removes its launchd domains, IPC objects, crontab, Nix profile, temporary files
(including `/private/var/folders`) and directories, deletes the user and group, and then
runs the darwin audit for the slot: anything left fails the stop, naming it, and the
Mac is not offered again until it is clean.

**Paths.** A Mac has no mount namespaces, so the container paths a session and its
agents are given (`/workspace`, `/home/skuld`, `/run/secrets`, an agent's home) are
translated to where their sources are on the Mac, in the environment, commands and the
guest agent's configuration. Secret files the session expects in its home become links
there; a session mount under `/var/run` (a workload token) becomes a link of root's at
that path, removed at stop.

**Limits.** There is no CPU or memory limit: a session has the whole Mac. A session can
read its own credential files (its ServiceAccount tokens and the guest agent's
configuration) through its group, which Linux hides behind a mount. Live preview finds
the session's listeners with `lsof`.

| Knob | Default | Bounds | On a hit |
|---|---|---|---|
| `uid_base` (runtime) | 61000 | The slot user's uid; must be free on the Mac | The slot is refused, naming the user that has it |
| `session_start_timeout_seconds` (runtime) | 30 | How long launchd may take to start the session's job | The start fails, naming the log on the Mac |
| `stop_timeout_seconds` (runtime) | 30 | Skuld's time to flush after SIGTERM, and each later wait at stop | The stop fails naming the job or the processes left; it is retried |

## Not built yet

- Macs with Intel processors: the session bundle is built for arm64 only.
