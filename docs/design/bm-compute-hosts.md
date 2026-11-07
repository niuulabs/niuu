# Bare-metal hosts as Forge compute

Status: accepted (decisions settled 2026-11-07); being built (see "What is built"). Mockup:
[`docs/mockups/bm-compute-hosts/index.html`](../mockups/bm-compute-hosts/index.html).

Builds on [VM session slots](vm-session-slots.md) (the slot ledger, placement,
per-slot separation and host mode with Nix and rootless Podman).

## The problem

A bare-metal machine can take part in Niuu today in one way: `niuu join`
(`docs/operator/joining-machines.md`) registers it with the Guild as a **node**,
and that node runs **its own Forge**: its own database, API, archives, settings
and upgrades. Ten machines are ten Forges.

A VM Forge works the other way. One Forge (for example "Valhalla VMs") owns a
compute pool, and machines are capacity behind the `MachineProvider` port
(`src/volundr/domain/compute.py`). Its only provider today is
`HarvesterMachineProvider`, which creates and deletes VMs.

This proposal lets a bare-metal host join an existing Forge as capacity, the way
VMs do: one Forge, many hosts, each host running several sessions in separated
slots, with no Forge installed on the host.

## Goals

- **Adding a host is one step.** Copy one command from the UI and run it on a stock
  Linux host. No Python, no CLI and no Forge on the host. The host appears in the
  list, Forge prepares it with the server's recipe, and it shows ready. Draining
  and removing are one click each.
- **Hosts carry tags**, and sessions and workflows target them with the same
  `tags` + `match` selector they already use to target a Forge.
- Forge places sessions into free slots on registered hosts, as it does on VMs.
- A host is never deleted, so recycling is **verified cleanup**: every slot is
  cleaned when its session stops, Forge checks that nothing is left, and a host
  with leftovers is taken out of service loudly.
- Many slots per host, each with its own user, network namespace, PID namespace
  and cgroup limits. No sudo, no Docker group and no root Docker for slots;
  rootless Podman per slot, with Nix as the shared software layer.
- One runtime per host, set on the host's record. An exclusive host is a host set
  to one slot.
- **How Forge reaches a host is a pluggable adapter.** It is SSH first, and later
  a connection the host opens outward, for hosts behind NAT, with no change to
  the provider or the runtimes.

## Not in scope

- macOS hosts. Nix works on macOS, but there are no network namespaces or
  cgroups, so the slot boundary does not exist there. A Mac could later be an
  exclusive one-slot host; it is not part of this design.
- Mixing VMs and hosts in one pool. A pool has one provider (the execution
  catalog's `provider_binding`), and Forge never falls back from one to the
  other (`no-fallbacks.md`). See decision 2.

## Model

### The registered host

Forge keeps a fixed inventory in a new table, `compute_hosts`, owned by the Forge
that the hosts serve:

| Field | Meaning |
|---|---|
| `id` | UUID; also the `Machine.resource_id` |
| `name` | Unique per pool; the host's hostname unless the admin renames it |
| `pool_id` | The pool the host serves |
| `tags` | Admin-set tags (`gpu`, `arm64`, `fast-disk`, `site:hnoss`, …), matched with `niuu.domain.tags.matches_tags` |
| `connection` | The connector binding id the host joined with (`direct-ssh`, `reverse-ssh`, …) and its per-host data (address, or the tunnel port Forge assigned) |
| `host_public_key` | The host's Ed25519 public key (OpenSSH format), globally unique |
| `execution` | The catalog execution the host runs (`id@revision`): its one runtime |
| `slots` | Sessions the host runs at once; 1 means an exclusive host |
| `facts` | What the last check measured: CPUs, memory, disk, kernel, cgroup v2, arch, GPUs |
| `state` | See below |
| `allocation_id` | The allocation that currently holds the host, or null |
| `prepared_digest` | `host_compatibility_digest` of the recipe that last prepared the host |
| `findings` | What the last check or audit found wrong, shown as is in the UI |
| `registered_by`, `registered_at`, `last_checked_at` | Audit trail |

`execution`, `slots`, `tags` and the connection kind are **chosen by the admin**,
never by the host. The execution and the connection kind are fixed when the
pairing code is minted. `slots` can be fixed then too, or left open until the
first check has measured the host, so the UI can suggest a number; a host with no
`slots` is `ready` but cannot be claimed. A joining host cannot give itself more
slots, other tags or a different runtime, just as a node cannot give itself
plaintext transport. The admin can change tags at any time; that affects only
new placement. Changing `slots` or `execution` needs the host out of service.

### Host states

```
pending ──reached──▶ preparing ──recipe ok, audit clean──▶ ready ◀──resume── out_of_service
   │                     │                                  │  ▲                  ▲
   │ never reached       │ recipe fails                     │  │ cleaned          │ drained
   ▼                     ▼                                  ▼  │                  │
unreachable          quarantined ◀──leftovers / check failed── in_service ──drain──▶ draining
```

- `pending`: registered, not reached yet.
- `preparing`: Forge is running the execution's host recipe (below).
- `ready`: prepared, audit clean, not held by an allocation.
- `in_service`: held by the pool's allocation; slots are placed on it.
- `draining`: the operator asked to take it out; it takes no new sessions, and
  running sessions are never stopped.
- `out_of_service`: empty and released. Nothing runs, and nothing is placed on it.
- `quarantined`: a check, recipe or audit failed. It is offered to no one until an
  operator acts. Every move into this state is an error log and a count on
  `volundr.compute.host.quarantined`.
- `unreachable`: the connector has no connection to the host, or the host's SSH
  key does not match the registered key. It is counted the same way. A host that
  drops while sessions run is a host-health signal. Its sessions are marked "host
  disconnected", never stopped, lost or moved, and they recover when the
  connection returns (see Connection).

### The `RegisteredHostMachineProvider`

A new adapter, `volundr.adapters.outbound.registered_hosts.RegisteredHostMachineProvider`,
implements `MachineProvider` over that inventory. It is selected the dynamic way
(`compute.provider.adapter` + kwargs). It **claims instead of creating** and
**cleans instead of deleting**:

| Port method | VMs (Harvester) | Registered hosts |
|---|---|---|
| `profiles()` | VM sizes from kwargs | Host classes from kwargs, with the count of ready hosts and free slots in `details` |
| `create(request)` | Create a VM for the allocation | **Claim** one `ready` host whose `prepared_digest` matches and whose tags satisfy the request's selector, with one atomic `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED)` setting `allocation_id`. Repeating with the same allocation id returns the same host. No such host is `ComputePoolFullError`, naming the selector and how many hosts are ready, quarantined and unreachable |
| `get(id)` | VM + VMI state | The held host: `running` when the connector is connected and the last check passed, otherwise `failed` with the reason in `status_detail`; the host name goes in `phase` |
| `list()` | VMs labelled for this installation | Hosts held by this pool's allocations |
| `delete(id)` | Delete the VM; `False` while it is going | **Clean**: run the whole-host audit (below), returning `False` while it runs. If the host is clean, it returns to `ready` (or `out_of_service` if drained) and the call returns `True`. If something is left, the host goes to `quarantined` with the findings, the allocation's hold ends, and the call returns `True`: the allocation is gone even though the host is not usable |
| `close()` | Close the HTTP client | Close the connector |

`Machine` gains `slots: int | None` (the capacity the provider declares) and
`tags: tuple[str, ...]`. VM providers leave `slots` as `None`, so the pool's
`slots_per_machine` applies as today. They set `tags` from the profile, so a VM
pool can be targeted the same way. The ledger currently fixes `ComputeLease.slots`
at reserve, before the provider has been asked. For a fixed inventory it is set
once instead, from `Machine.slots`, when the claimed machine first reports. A
provisioning lease with unknown slots counts as zero free slots.

### One long-lived allocation per host

A VM is cheap to replace, so the default `replace` policy deletes a VM once its
slots have served. A host cannot be replaced, and draining a 16-slot host so that it
can be recycled would leave it idle until its longest session ends. So a
registered-host pool runs `reuse_policy: reuse`:

- A host is claimed into one allocation when it enters service and stays held
  while it serves. Under `reuse`, the drain knobs `max_sessions_per_machine` and
  `max_machine_age_seconds` are already rejected.
- Each slot is recycled in place when its session stops. That needs the runtime
  to declare `supports_reuse`. The host runtime declares it only when its slot
  cleanup is verified (see Recycling).
- Idle expiry ends the hold on an empty host. It costs nothing, and it runs the
  whole-host audit before the host is claimed again.

## Tags and targeting

### One vocabulary

Niuu already targets a whole Forge with a tag selector: Guild instances carry
`tags`, and session create (`target_tags` / `target_match` on the Guild facade),
Launch and Ting dispatch (`select_adapter_by_tags`) pick an instance with
`niuu.domain.tags.matches_tags(have, required, match)`. Here `match` is `all`
(default: every tag is present) or `any`. Hosts use **the same field names, the
same function and the same semantics**. They have no separate label system:
a host's `tags` are what a selector is matched against.

### How a selector reaches placement

A caller still sends one selector, for example
`{"target_tags": ["valhalla", "gpu"], "target_match": "all"}`. The selector has two
levels:

1. **Guild picks the Forge.** An instance can serve the selector if its own
   tags, together with the tags of at least one of its in-service hosts, satisfy
   it. Each host is checked on its own: `match: all` over a union across hosts
   would wrongly accept a Forge with one `gpu` host and another `arm64` host for
   `[gpu, arm64]`. The Forge reports its **distinct host tag sets** with its
   health, through the instance probe that already exists, so Guild never sees
   individual hosts.
2. **Guild forwards what is left to the Forge.** `_strip_instance_hints` already
   removes `target_tags`/`target_match` before forwarding. Instead, Guild now
   forwards the remaining selector as
   `workload_config.host_selector = {"tags": [...], "match": "all"|"any"}`. For
   `all`, that is the required tags minus the instance's own tags. For `any`, it
   is nothing if an instance tag already matched, and the whole selector
   otherwise. The split is one pure function next to `matches_tags`
   (`residual_selector(instance_tags, required, match)`), used by both the Guild
   facade and Ting dispatch, so they cannot drift apart.
3. **Forge places only on matching hosts.** Placement and claiming filter
   machines with `matches_tags(machine.tags, selector.tags, selector.match)`. A
   session that names a host selector that nothing in the pool can satisfy is
   refused at create with 422, naming the selector and the tag sets the pool
   has. It never lands on a host without the tag.

A session that sends no selector can be placed on any host. The selector is part of
the session's record, so restarts and recovery keep it.

### Warm capacity: per pool, plus optional targets per selector

> **Dropped (2026-11-07).** Jozef decided per-selector `warm_targets` are not needed.
> `warm_min` stays per pool, and a pool on registered hosts reports a shortfall when
> it cannot claim enough hosts. The rest of this section is kept as the record.

`warm_min` stays per pool: the count of free ready slots, whatever their tags. A
pool-wide count alone can be met entirely by hosts that do not match a `gpu`
request, so the pool also gets an optional list:

```yaml
warm_targets:
  - {tags: [gpu], match: all, min: 1}
  - {tags: [arm64], match: all, min: 2}
```

Each target counts only the free slots on machines whose tags satisfy it. For VMs,
a target below its minimum boots a machine of a profile whose tags satisfy it. For
registered hosts, nothing can boot, so a target below its minimum is a health
signal: an error log, a count on `volundr.compute.warm_shortfall` (attribute: the
selector), and a red "1 of 2 free" line in the pool summary, with the remedy
"register or resume a host tagged `arm64`". Launch's readiness
(`GET /api/v1/forge/session-runtimes`) gains an optional selector and reports
`warm` / `full` for that selector, not for the pool.

### In the UI

- Host rows show their tags as chips, and the admin edits them inline.
- The host list filters by a selector, in the same chip input that Launch uses for
  instance targeting.
- The pool summary lists free slots per distinct tag set and the state of each
  warm target.
- Launch and Ting's targeting fields stay as they are. When the chosen Forge has
  tagged hosts, the tag suggestions include host tags, marked "host".

## Registration: one command

### What the admin does

Forge → Compute → Hosts → **Add host**: pick the runtime (one per host), optionally
the slot count, the tags, and how Forge reaches the host (only the connectors the
Forge has configured are offered). Copy one line. The panel then follows the host
live and needs no further action, except setting the slot count if it was left
open.

### What runs on the host

```bash
curl -fsSL -H 'Authorization: Bearer <pairing code>' https://forge-hosts.valhalla.asgard.niuu.world/api/v1/forge/compute/hosts/join.sh | sudo sh
```

That is the whole step. It needs only what a stock systemd Linux has: `sh`,
`curl` (or `wget`, which the UI offers as an alternative line), `ssh-keygen` and
`sshd` or `ssh`, depending on the connector. The script is POSIX `sh`. It
installs nothing itself: everything else (Nix, rootless Podman, `uidmap`,
`nftables`, the slot supervisor) comes from Forge's recipe in the next step.

- **The pairing code authenticates the download too.** `join.sh` is an ordinary
  JWT-gated route with `require_scope("compute_host_join")`. It does not consume
  the code. Forge renders the script with the Forge URL, the connector's setup
  and the code the caller sent, so the code appears once on the command line and
  no unauthenticated route is added. The code is the same single-use, short-lived
  scoped workload JWT that `niuu guild pair` mints (below), so the line is long.
  The UI shows it truncated with a Copy button.
- **TLS is required.** As for `niuu join`, a plaintext Forge URL is refused unless
  the admin minted the code with plaintext allowed. A Forge with no CA (local mode)
  serves the join on its own listener with a self-signed certificate, and the
  command pins its key (`curl -k --pinnedpubkey sha256//…`); see "What is built".
- The UI also offers **"Download and read first"**: the same script as a file with
  its SHA-256, for an operator who will not pipe to `sh`.

The script, in order:

1. Checks only what the join needs: Linux, root, systemd, cgroup v2, the tools
   above, and no existing registration. Each missing item prints its fix, and
   nothing is changed.
2. Generates the host's Ed25519 key at `/etc/niuu/ssh_host_ed25519_key`
   (`umask 077`, refuse to overwrite), the same path VMs use.
3. Sends `name`, `facts` (a few `/proc` reads) and the public key to
   `POST /api/v1/forge/compute/hosts/join`. Consuming the code and inserting the
   `compute_hosts` row happen in one transaction, as in
   `GuildJoinRepository.consume_and_register`.
4. Runs the connector's host setup from the join response. For direct SSH, it
   adds the key as an sshd `HostKey` (`/etc/ssh/sshd_config.d/10-niuu-host-key.conf`)
   and installs Forge's public key with the options from decision 1. For reverse
   SSH, it also writes and starts `niuu-host-connect.service` (see Connection).
   This is a host-level unit. "No systemd units" applies to sessions and slots,
   not to the one connection the host keeps.
5. Installs `/usr/local/sbin/niuu-host-leave`, which undoes steps 2 and 4.
6. Prints "Registered as `glad`. Forge is preparing this host; follow it in the UI."

The `niuu` CLI is not needed. `niuu join … --as-compute` may later wrap the same
script for people who have the CLI, but it is not a second implementation.

### Forge prepares the host

Forge reaches the host through its connector, pinned to the registered host key
(a mismatch is `unreachable` with "host key does not match the registered key").
It runs the execution's host recipe through the existing `SshHostPreparation`,
the same versioned, digest-pinned recipe a VM gets: the Nix multi-user daemon,
rootless Podman, the slot supervisor and Skuld's host artifacts. It records
`facts` and `prepared_digest` and runs the whole-host audit. A clean audit makes
the host `ready`; any failure quarantines it, naming the recipe stage or finding.
Running the recipe from Forge keeps one recipe, one digest and one place that
knows what a host was prepared with. When the execution's recipe revision
changes, a host that does not match is no longer claimable. The UI then offers
**Re-prepare**, which runs only on an empty host: drain, run the recipe again,
audit, resume.

### Pairing codes

Minting reuses the pairing mechanism of `niuu join`: a scoped workload JWT with
`token_use: valkyrie_build`, `scopes: ["compute_host_join"]` and empty roles,
single use (its hash is stored in the pairing-code table). That table gains
`purpose`, `pool_id`, `execution`, `slots`, `tags` and `connector`, all recorded
at mint and never taken from the host. Minting is
`POST /api/v1/forge/compute/hosts/pairing-codes` (`volundr:admin`), reached from
the UI through the Guild facade (`?instance_id=`). The code expires after
`compute.hosts.pairing_code_ttl_seconds` (default 600; a host does not take
longer than that to run one command), and the UI can revoke it.

A compute host is inventory of one Forge, not a Guild node. It registers no
instance and sends no heartbeat. Guild sees the Forge's host tag sets and nothing
about individual hosts.

### Draining and removing

- **Drain** (one click): no new sessions; running ones continue. When the last
  one ends, the host is audited and becomes `out_of_service`.
- **Drain and remove** (one click, one confirmation): drains, then removes when
  empty. Removing deletes Forge's key from the host through the connector, tells
  the connector to revoke the host (which ends any live connection), and deletes
  the row.
- **Remove now** is offered only on an empty host. On a host Forge cannot reach,
  removing still revokes the host on Forge's side immediately. The UI then shows
  the one command to run on the host to finish: `sudo niuu-host-leave`.
- **Resume** returns an `out_of_service` host to `ready` after a clean audit.

## Connection: a pluggable connector

### The port

Every way of reaching a host ends in the same place: **an SSH session from Forge
to the host's own sshd**, authenticated end to end with the pinned host key on one
side and Forge's key on the other. Connectors differ only in how they carry the
bytes. So the port says how to dial, not what to run:

```python
class HostConnector(ABC):
    """How Forge reaches a registered host's sshd. Carries bytes; never authenticates SSH."""

    @abstractmethod
    def join_setup(self, host: HostRegistration) -> HostJoinSetup:
        """What join.sh sets up on the host: sshd drop-ins, a connect unit, nothing."""

    @abstractmethod
    async def admit(self, host: RegisteredHost) -> None:
        """Make the host reachable: assign a tunnel port, allow its key, …; idempotent."""

    @abstractmethod
    async def dial(self, host: RegisteredHost) -> SshDial:
        """Where OpenSSH connects: HostName/Port, or a ProxyCommand argv that yields
        a byte stream to the host's sshd. Raises HostDisconnectedError when there is none."""

    @abstractmethod
    async def status(self, host: RegisteredHost) -> HostConnectionStatus:
        """connected | disconnected (since, last error); feeds host health."""

    @abstractmethod
    async def revoke(self, host: RegisteredHost) -> None:
        """Stop admitting the host and cut any live connection; idempotent."""
```

`SshGuestAccess` changes in one place: `_base_ssh` asks the connector for an
`SshDial` instead of reading `machine.addresses`, and adds `ProxyCommand` when the
dial has one. Everything that runs over SSH is unchanged: commands, file
writes, host preparation, the slot supervisor, the per-slot tunnels to each
slot's broker, and the reverse forward that Skuld's callbacks use. The runtimes
and the provider never see the connector, only the `GuestAccess` they already
use. A connector is configured the dynamic way:

```yaml
compute:
  hosts:
    connectors:
      - binding_id: direct-ssh
        adapter: volundr.adapters.outbound.host_connectors.DirectSshConnector
      - binding_id: reverse-ssh
        adapter: volundr.adapters.outbound.host_connectors.ReverseSshConnector
        endpoint: hosts-connect.valhalla.asgard.niuu.world:2222
        port_range: "20000-20999"
```

Each host records the connector it joined with. A connector is never swapped at
runtime, and a host is never tried over a second one (`no-fallbacks.md`):
changing it is remove and join again.

### Options

1. **Direct SSH** (`DirectSshConnector`). Forge dials the address the admin
   confirmed at join. This is the first to build, and the right one when Forge can
   reach the host (the Valhalla VLANs).
2. **Outbound reverse SSH tunnel** (`ReverseSshConnector`). *Recommended for hosts
   behind NAT, and the first host-initiated connector.* The host keeps one
   connection open, and Forge sends everything down it; nothing polls.
   - The host's `niuu-host-connect.service` runs
     `ssh -N -i /etc/niuu/ssh_host_ed25519_key -o ExitOnForwardFailure=yes
     -o ServerAliveInterval=15 -R 127.0.0.1:<assigned port>:127.0.0.1:22
     niuu-connect@<endpoint>`, with `Restart=always`. Stock OpenSSH is enough. The
     host's sshd listens on loopback only if the operator wants, since nothing
     needs to reach it from outside.
   - **The endpoint** is an OpenSSH server in a sidecar container of the Forge
     pod. It shares the pod's loopback, so the forwarded port is reachable by Forge
     and by nothing else. A Gateway `TCPRoute` (or a LoadBalancer Service) exposes
     its port. Forge is one replica (Recreate) already, so there is exactly one
     endpoint.
   - **It authenticates the host by the key registered at pairing**:
     `AuthorizedKeysCommand` asks Forge, on a socket in the pod, for that key's
     line. Only a registered, not-removed `reverse-ssh` host gets one:
     `restrict,port-forwarding,permitlisten="127.0.0.1:<that host's port>",permitopen="none",command="/usr/sbin/nologin"`.
     A host can only listen on its own port. It cannot open a port to reach
     anything in the Forge pod (`permitopen="none"`) and gets no shell.
     Another host's key cannot bind that port, and if something else ever
     answered on it, Forge's host-key pin would refuse it.
   - **Dial** is `127.0.0.1:<port>`. The port is assigned at `admit` from
     `port_range` and stored on the host, and is unique. When the range is full,
     the join is refused, naming `port_range`.
   - **Health**: a dropped tunnel makes `status` `disconnected` from that moment.
     The host becomes `unreachable` and is offered nothing new. Its sessions are
     marked "host disconnected" in the session view and keep running on the
     host, since Skuld does not need Forge to keep working. Messages to them fail
     with that reason until the tunnel returns. `Restart=always` reconnects; Forge
     then re-opens the per-slot tunnels (the existing reconnect path after a Forge
     restart) and the host returns to `in_service`. Each disconnect is counted on
     `volundr.compute.host.disconnects`.
   - **Revoke** stops the key's line from being returned and kills the host's
     connection on the endpoint.
3. **Agent with one multiplexed stream** (WebSocket or HTTP/2, in the manner of
   Teleport nodes, konnectivity or Cloudflare Tunnel). This is the later upgrade
   where outbound SSH is blocked but HTTPS is not. A small agent keeps one TLS
   stream to Forge, and `dial` returns `ProxyCommand niuu-host-relay <host id>`,
   which opens a sub-stream to the host's `127.0.0.1:22`. SSH still runs inside it,
   so nothing above the connector changes. It needs an agent binary on the host,
   delivered by `join.sh` as a pinned, digest-checked download.
4. **HTTPS long-poll** (the GitHub Actions runner model). Only as a fallback for
   egress through an HTTPS-only proxy that breaks long-lived streams. Each poll
   returns queued byte chunks for the relay. It works, but adds latency to every
   SSH round trip, so it is not a default.
5. **Overlay network** (WireGuard, Tailscale or Headscale). This is a deployment
   choice, not a connector: the host and Forge share a private network, and
   `DirectSshConnector` dials the overlay address. No new entry point.

### Why SSH all the way down

Whatever carries the bytes, it **carries an SSH session it cannot read or
forge**. A compromised carrier (the endpoint sidecar, a relay, an overlay) cannot
pretend to be the host (pinned host key) or Forge (Forge's key only authenticates
to the host's sshd). So the trust that matters stays the same for every
connector, and a new connector adds a reachability path, not a trust path.

## Recycling without deletion

### Per slot, every stop

The slot supervisor already tears a slot down and checks it (#1261): it removes the
containers, rules, interface, namespace, slice, directories, user and group, then
fails `slot-down` naming anything left, and the stop is retried. For hosts the
same `down` also checks:

- no process with the slot's uid (including rootless Podman's `conmon` and
  `pasta`);
- no files owned by the slot's uid or subuid range on any local filesystem the
  slot could write (`find -xdev -uid`). Slots get a private `/tmp`, `/var/tmp` and
  `/dev/shm` in their own mount namespace, so this should always be empty; the
  check proves it;
- no SysV IPC objects, cron or at jobs, `loginctl` linger, or systemd user
  manager for the slot's user;
- the slot's rootless Podman storage is gone (it lives under the slot's home, so
  it goes with it);
- Nix: the slot's profile and GC roots are gone. The shared `/nix/store` stays, and
  the next GC collects what only that slot used.

Only after a clean `down` is the slot index `released` and offered again. A uid
is reused only once nothing owns it anywhere, so a new session cannot inherit
what the previous one left. A `down` that keeps failing quarantines **the slot**:
the host's other slots keep running, the host takes no new sessions until the slot
is resolved, and the stop intent and archive are kept for retry, as the slot design
already says.

### Whole host

The **audit** applies the slot `down` checks to the whole slot uid range and adds
host checks:

- the recipe digest still matches;
- no `niuu-slot-*` users, groups, netns, veths, nft rules or slices exist beyond
  those of live bindings;
- no slot user is in the `docker`, `sudo`, `wheel` or `adm` groups;
- Nix `trusted-users` is root only;
- sshd still uses the registered host key;
- disk and memory are above the configured floors.

The audit runs when a host is registered, before every claim, when idle expiry
ends a hold, on demand ("Check now") and every
`compute.hosts.audit_interval_seconds` while in service. During service it only
reports; it never stops a session. A finding during service is an error log, a
count and a red row, and the host takes no new sessions until it is cleared.
There is no "ignore": a finding clears when the audit stops seeing it.

## Capacity

- **Slots are set per host**, at mint or after the first check. Once the host's
  facts are measured, the UI suggests a number from them and the runtime's
  per-slot limits, and shows the arithmetic ("48 CPUs − 4 reserved = 44 ÷ 4 per
  slot = 11"). The admin confirms it; Forge never picks silently.
- **Per-slot limits** come from the host's execution (`slot_isolation`: `cpu`,
  `memory_mib`, `pids`, each set or `null`), as on VMs. They are cgroup v2 limits
  on the slot's slice. Limit hits are counted on `volundr.compute.slot.limit_hits`.
- **System reserve.** The provider has kwargs `reserved_cpu` and
  `reserved_memory_mib` (no defaults; write them per host class). Forge refuses
  `slots × limit > facts − reserve` for any limit that is set, with the arithmetic
  in the error, at mint, at prepare and when the facts change. A limit set to
  `null` is the operator's decision and shows as "no limit".
- **Disk** has no per-slot quota yet (an open question in the slot design). The
  audit enforces the host's free-disk floor, and that floor is a provider kwarg.
- **GPUs**: GPU hosts are tagged `gpu` and set to one slot until GPU partitioning
  is designed.
- **Exclusive host** = a host set to one slot. Sessions with a `sudo`/`docker` grant
  (when a runtime offers grants) are placed only on such hosts, as the slot design
  requires.

## Runtimes on a host

One runtime per host, from its execution. Only runtimes that need **no root
daemon in a slot** run on registered hosts:

- **Host** (Nix + rootless Podman per slot, being built in session
  vm-host-mode-nix). This is the primary runtime for hosts. Sidecars and per-slot
  containers run in the slot user's rootless Podman, inside the slot's
  namespaces.
- **Container via rootless Podman**: a later mode of the container runtime. Today
  `SshContainerVmRuntime` runs root Docker, so it declares a new
  `requires_root_daemon`, and a registered-host pool refuses it at startup.
- **OpenShell**: its runtimes run the guest agents under host Docker today, so they
  declare `requires_root_daemon` and are refused on hosts as well. A rootless OpenShell
  mode would make it possible on one-slot hosts (its sandbox is the boundary).

## Security

### Against `.claude/rules/architecture.md`

1. **Pairing** uses the scoped workload token exception unchanged. It adds one
   scope, `compute_host_join`, to `KNOWN_WORKLOAD_SCOPES`, with
   `require_scope("compute_host_join")` on both `join.sh` and `join`. That keeps
   `tests/test_niuu/test_token_scope.py`'s two-way check green. Both routes are
   JWT-authenticated, so Envoy needs no bypass and no route is unauthenticated.
2. **Node signing exception: not extended** by direct SSH or reverse SSH. Hosts
   send no heartbeats and call no signed HTTP route; Forge reads health through
   the connector. An agent connector (option 3) or long-poll (option 4) **would**
   be a host-signed HTTP entry point. It must then be added to that exception
   with the same limits (Ed25519 over method, path, millisecond timestamp and body
   hash; per-host atomic watermark; bounded skew; identical 401s; exact
   `safe_regex` Envoy bypass and middleware exemption), in the PR that builds it.
3. **New exception: the host connect endpoint** (for the reverse SSH connector).
   It is an authenticated entry point that is not HTTP: SSH public-key
   authentication of a *machine* by its registered Ed25519 key. It is sanctioned
   for the same reason as node signing: there is no OIDC principal for a machine,
   and the key never leaves the host. Proposed text for its limits, enforced
   together:
   - only keys of registered, not-removed hosts whose connector is `reverse-ssh`
     are admitted, looked up live on every connection (`AuthorizedKeysCommand`),
     so removing a host takes effect at once, and revoke also kills a live
     connection;
   - each key may only listen on its own assigned loopback port
     (`permitlisten`), may open nothing (`permitopen="none"`), gets no shell, PTY,
     agent or X11 forwarding (`restrict`), and the endpoint listens in the Forge
     pod's network namespace only for this;
   - what crosses it is an SSH session from Forge to the host's sshd, pinned to
     the same registered key, so the endpoint never carries trust of its own.
4. **VM guest metadata exception, host-key identity: amended.** Today the key is
   "the Ed25519 SSH host key Forge generated for the allocation", verified against
   the public key in the allocation's machine bootstrap. For a registered host,
   **the host generates the key, the private key never leaves the host, and Forge
   stores only the public key**, received with the pairing code. Proposed
   amendment:

   > For a registered host (`RegisteredHostMachineProvider`), the key is generated
   > on the host by `join.sh` and only its public key is stored, on the
   > `compute_hosts` row, received under a single-use `compute_host_join` pairing
   > code. Forge never generates or holds that private key. The key belongs to the
   > host, not to one allocation, so the limits still bind to the allocation: the
   > request body names the allocation (a mismatch with the authenticated
   > allocation is the identical 401, so a captured request of an earlier
   > allocation of the same host cannot be replayed under a later one), the
   > watermark is per allocation, and only the allocation currently holding the
   > host gets an answer. Removing or re-keying the host revokes it.

   `HostKeyGuestIdentity` changes in one place: `machine_bootstrap_for` resolves
   the allocation's public key from the host row instead of from a bootstrap that
   Forge generated. `SshGuestAccess` gets a registered-key mode that pins the
   registered `.pub` and writes no host key into the bootstrap.
5. **One key, three uses.** The host key is the sshd host key, the reverse-tunnel
   client key and the metadata signing key. Each signs a different, structured
   message (the SSH exchange hash, the SSH user-auth blob, the `niuu join`
   request message), so a signature for one cannot be presented as another.
   Fewer keys means fewer things to revoke. A separate connect key is a small
   change if review prefers it.
6. **Multi-slot credentials use SSH push.** `MetadataCredentialDelivery` serves
   only slot 0 (`serves_slots = False`), so multi-slot hosts use
   `SshPushCredentialDelivery`. Forge writes each slot's credentials over SSH into
   that slot's `0700` directory. The metadata identity serves one-slot hosts, and
   multi-slot hosts once per-slot metadata lands (a follow-up shared with VMs). On
   every host the host key is root-only and slots are never root, which is the
   condition the rule sets for more than one slot.

### Forge's access to the host

Forge needs root on the host: creating slot users, network namespaces, nft rules
and slices is root's job. That is the same trust it has in a VM, but a host is
not disposable and may sit on a LAN with BMCs and other machines. Decision 1
settles it: a narrowed root SSH login now, a forced-command agent later. In both:

- key only, never a password; `restrict,port-forwarding` with
  `permitopen`/`permitlisten` limited to the slot tunnel ports. Direct SSH adds
  `from="<Forge egress addresses>"`. Reverse SSH adds `from="127.0.0.1,::1"`,
  because Forge's connection arrives through the tunnel on the host's loopback;
- Forge's key is per Forge installation and stored like the VM key
  (`compute.access` secret), never in a session;
- removing the host, or rotating Forge's key, is one action in the UI.

### Slots on bare metal

- No sudo and no Docker group for any slot user; the audit fails a host where
  one is in `sudo`, `wheel`, `docker` or `adm`.
- Rootless Podman per slot user, inside the slot's user, network and PID
  namespaces and slice. Its network is the slot's netns (pasta), not the host's.
- Nix: slots are `allowed-users`, never `trusted-users` (a trusted user can add
  substituters and poison the shared store for everyone).
- `loginctl` linger off, no user cron, private `/tmp`, `/var/tmp` and `/dev/shm`.
- **Egress**: slots NAT out of the host's default route, and nft rules deny the
  host itself, other slots and the container networks, as on VMs. On bare metal
  the LAN (RFC 1918 ranges, the BMC network) is also denied by default, except
  what the operator lists (`allowed_lan_cidrs`). See decision 4.
- The host should be dedicated to Forge: another workload on it shares the kernel
  with slots and is reachable by Forge's root key.

## Operator and UI flow

The mockup shows four screens:

1. **Hosts**: a pool summary (hosts by state, free slots, free slots per tag set,
   warm targets), a selector filter, and one row per host. Each row shows the
   host's tags, connection, state, slots in use, size and last check, with the
   actions Drain, Resume, Check now, Re-prepare and Drain and remove. Quarantined,
   disconnected and unreachable rows show their findings inline.
2. **Add host**: the runtime, tags, connection and (optional) slots, then the one
   command to copy, with a countdown and Revoke.
3. **Add host, live**: the host appears and Forge follows it through *joined →
   connected (key pinned) → preparing (recipe stages) → audit → set capacity →
   ready*.
4. **Set capacity**: measured facts, the suggested slot count with its arithmetic,
   and *Put in service*.

## PR plan

Each PR passes CI on its own and changes no behaviour until the provider is
configured.

1. **docs**: this design and the mockup (this PR).
2. **Host inventory**: migration `0001NN_compute_hosts` (the table, a state check,
   a unique name per pool, a unique public key, and the pairing-code columns),
   the domain model and state machine, the `RegisteredHostRepository` port and its
   asyncpg adapter. Tests with mocked pools, plus an integration migration test.
3. **Connector port and direct SSH**: `HostConnector`, `SshDial`,
   `DirectSshConnector`, and the `SshGuestAccess` change to dial through a
   connector (VMs get a trivial connector over `machine.addresses`, so nothing
   changes for them).
4. **Pairing and join**: the `compute_host_join` scope, the mint, `join.sh` and
   `join` routes on Forge and through the Guild facade, the POSIX `join.sh` (tested
   with `sh -n`, shellcheck, and a test that runs it against a fake Forge in a
   throwaway root), and `niuu-host-leave`.
5. **Provider and identity**: `RegisteredHostMachineProvider` (claim, get, list,
   clean, check), `Machine.slots`/`Machine.tags`, ledger slots-from-machine,
   fixed-inventory readiness, `HostKeyGuestIdentity` reading the key from the host
   row, `requires_root_daemon`, and the architecture rule amendment.
6. **Tags and targeting**: `residual_selector`, Guild forwarding
   `workload_config.host_selector` (facade and Ting dispatch), the Forge's host tag
   sets in its health, placement filtering, and the 422 for an unsatisfiable
   selector.
7. **Verified recycling**: the slot `down` additions, the whole-host `audit` verb,
   quarantine, the audit interval knob, the counters, and `supports_reuse` on the
   host runtime when verified cleanup is on. Live proof on a throwaway host: plant
   a leftover and watch it quarantine.
8. **Admin API and docs**: list hosts, drain, resume, check, re-prepare, remove,
   and edit tags; host state on the pool snapshot and health surface; operator
   doc `docs/operator/compute-hosts.md` with every new knob, what bounds it and
   what happens on a hit.
9. **Web**: the Hosts panel and Add host flow, after this mockup is approved.
10. **Infra** (jve/infrastructure): a "Valhalla Hosts" Forge (one controller pod,
    the volundr chart, the registered-hosts provider), registered in the Guild.
    Then the first host joins with the one command and runs two host-mode sessions
    side by side.
11. **Reverse SSH connector**: the endpoint sidecar and chart values, the Gateway
    `TCPRoute`, `ReverseSshConnector`, `niuu-host-connect.service`, the connect
    endpoint exception in the architecture rules, and a live proof from a host
    behind NAT (Forge restart and host network drop both recover).

Later, separately: the agent connector (option 3), multi-pool Forge (decision 2),
the forced-command host agent (decision 1, option B), and macOS one-slot hosts.

## What is built (2026-11-07)

On branch `docs/bm-compute-hosts`. Operator guide: [docs/operator/compute-hosts.md](../operator/compute-hosts.md).

- Inventory, pairing, join, the registered-host provider, direct SSH, checks and audit,
  admin routes, tags through the Guild facade and Ting dispatch, the web Hosts view.
- The Postgres pairing table is Forge's own (`compute_host_pairing_codes`), not the
  Guild's `niuu_pairing_codes`. That table belongs to Guild nodes and has a key into
  `niuu_nodes`.
- The host key is **sshd's own Ed25519 key**, linked as
  `/etc/niuu/ssh_host_ed25519_key`. An extra `HostKey` line would make sshd drop the
  host's other keys and break every operator's `known_hosts`.
- The recipe runs when the pool first claims a host, through the existing
  `_ensure_host_prepared` path. It does not run at registration. Registration only
  reaches the host and audits it.
- The audit checks free disk only; there is no memory floor.
- Pairing codes need no identity infrastructure on a local mini-mode Forge. Where
  workload identity has a configured key, it signs them, as Envoy needs. Otherwise,
  only with the allow-all identity adapter of mini mode, Forge signs them with its own
  key file (`compute.hosts.pairing_signing_key_file`), as it already does for session
  credentials. Anything else is a startup error, because a code the identity layer
  cannot verify would be refused before it reached Forge.
- The edge (`credential_allows_route`) admits a `compute_host_join` code on exactly
  `GET …/hosts/join.sh` and `POST …/hosts/join`.
- **Pinned TLS join for a Forge with no CA.** Plain http on the LAN was rejected: a
  hijacked join gives someone root on the host. With `compute.hosts.join_listener`
  (the default when `join_url` is unset), Forge serves only those two routes on a TLS
  listener of its own (`volundr.adapters.inbound.host_join_listener`, started and
  stopped in Forge's lifespan), and the API no longer serves them. Its RSA key and
  self-signed certificate are generated once (0600), never overwritten, and reused,
  so the pin is stable; a certificate without expiry (RFC 5280's 99991231235959Z),
  since `curl -k` checks only the pin. The mint command is
  `curl -fsSL -k --pinnedpubkey sha256//<SPKI SHA-256> …`, `join.sh` posts with the
  same pin, there is no wget line (wget cannot pin), and the mint response carries
  `join_url` and `pin` for the Add host screen. The URL is `join_url` when set,
  else `https://<server_public_host>:<port>`; a loopback, unspecified or IPv6
  public host is a startup error, never a silent plaintext or unreachable URL. A key
  or port the listener cannot use is fatal at startup.
- `warm_targets` per selector were dropped (decision 6).
- **Re-prepare** (`POST …/hosts/{id}/reprepare`, also through the Guild facade) does
  not run the recipe itself. The preparation record is the allocation that holds the
  host: it pins the plan, carries the result and keys the checkpoint on the host. So
  Re-prepare disposes of the idle allocation through `ComputePoolService.dispose`, the
  same path that retires an idle allocation whose plan changed, and then checks the
  host. The next claim pins the current plan and runs the recipe from a fresh
  checkpoint. It refuses with 409 while sessions run, naming drain.
- **Cancel drain** is `resume` on a draining host. **Download and read first** shows
  the script's SHA-256, the file, and the commands to fetch, verify, read and run it.
  The mint response carries the script and digest, rendered exactly as `join.sh`
  serves it for that code, so no extra route was needed.
- Not built yet: the reverse SSH connector, macOS hosts.

## Decisions (settled 2026-11-07)

Jozef accepted the recommendations:

1. **Forge's root on a host:** a root SSH login like on VMs, narrowed by `restrict`,
   port-forward limits and, for direct SSH, `from=`, on hosts dedicated to Forge.
   The forced-command host agent (`niuu-host-agent`, fixed verbs, no shell) is the
   hardening step once the first host works.
2. **Which Forge:** a separate light "Valhalla Hosts" Forge (one pod) next to
   "Valhalla VMs". A multi-pool Forge comes later, after hosts work.
3. **Hosts are Forge inventory, not Guild nodes.** Guild sees each Forge's host tag
   sets, never individual hosts.
4. **Slot egress to the LAN** is denied for RFC 1918 by default, with
   `allowed_lan_cidrs` as the allowlist.
5. **Slots are reused in place** after a verified clean `down`; a host is not
   drained to recycle.
6. **Warm capacity** is per pool. Optional `warm_targets` per selector were
   accepted at first, then dropped on 2026-11-07 as not needed.
7. **The first host-initiated connector** is reverse SSH to an endpoint sidecar in
   the Forge pod. The multiplexed agent comes later.
8. **Registration** is `curl … | sudo sh` with the download authenticated by the
   pairing code, plus a "download and read first" variant.

The mockup still needs Jozef's reaction before the web PR.

## Open questions

- Per-slot disk quota (shared with the slot design).
- Whether a periodic re-prepare (for example weekly, to pick up Nix updates) is
  worth a knob, or recipe revisions are enough.
- GPU partitioning across slots.
- SSH over 443 (TLS-wrapped) on the connect endpoint, for networks that allow only
  HTTPS out, before the agent connector exists.
