# Bare-metal hosts as Forge compute

Status: proposal, for review. Nothing here is built. Mockup:
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

- A host is registered once with one command on the host and one click in the UI,
  through the existing pairing mechanism.
- Forge places sessions into free slots on registered hosts, as it does on VMs.
- A host is never deleted, so recycling is **verified cleanup**: every slot
  is cleaned when its session stops, Forge checks that nothing is left, and a
  host with leftovers is taken out of service loudly.
- Many slots per host, each with its own user, network namespace, PID namespace
  and cgroup limits. No sudo, no Docker group and no root Docker for slots.
- One runtime per host, set on the host's record.
- No new authentication mechanism and no new host-signed route.

## Not in scope

- Hosts behind NAT that Forge cannot reach over SSH (see open questions).
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
| `name` | Unique per pool, from `niuu join --name` (default: hostname) |
| `pool_id` | The pool the host serves |
| `address` | Where Forge reaches the host's sshd, reported at join (required; no guess from the request's source address) |
| `host_public_key` | The host's Ed25519 public key (OpenSSH format), globally unique |
| `execution` | The catalog execution the host runs (`id@revision`), which is its one runtime |
| `slots` | Sessions the host runs at once; 1 means an exclusive machine |
| `facts` | What the last check measured: CPUs, memory, disk, kernel, cgroup v2, arch, GPUs |
| `state` | See below |
| `allocation_id` | The allocation that currently holds the host, or null |
| `prepared_digest` | `host_compatibility_digest` of the recipe that last prepared the host |
| `findings` | What the last check or audit found wrong, shown as is in the UI |
| `registered_by`, `registered_at`, `last_checked_at` | Audit trail |

`execution` and `slots` are **chosen by the admin**, never by the host. The
execution is fixed when the pairing code is minted. `slots` can be fixed then too,
or left open until the first check has measured the host, so the UI can suggest
a number; a host with no `slots` is `ready` but cannot be claimed. A joining host
cannot give itself more slots or a different runtime, just as a node cannot give
itself plaintext transport. An admin can change either later, but only while the
host is out of service.

### Host states

```
pending ──probe ok──▶ preparing ──recipe ok, audit clean──▶ ready ◀──resume── out_of_service
   │                      │                                  │  ▲                  ▲
   │ probe fails          │ recipe fails                     │  │ cleaned          │ drained
   ▼                      ▼                                  ▼  │                  │
unreachable           quarantined ◀──leftovers / check failed── in_service ──drain──▶ draining
```

- `pending`: registered, not yet reached. Forge probes it over SSH with the pinned
  key.
- `preparing`: Forge is running the execution's host recipe (below).
- `ready`: prepared, audit clean, not held by an allocation.
- `in_service`: held by the pool's allocation; slots are placed on it.
- `draining`: the operator asked to take it out; it takes no new sessions, and
  running sessions are never stopped.
- `out_of_service`: empty and released. Nothing runs, and nothing is placed on it.
- `quarantined`: a check, recipe or audit failed. Forge offers it to no one until
  an operator acts. Each move into it is an error log and a count on
  `volundr.compute.host.quarantined`.
- `unreachable`: Forge cannot reach sshd, or the pinned key does not match. This is
  counted the same way. A host that becomes unreachable while sessions run is
  reported per session; Forge does not move the sessions anywhere.

### The `RegisteredHostMachineProvider`

A new adapter, `volundr.adapters.outbound.registered_hosts.RegisteredHostMachineProvider`,
implements `MachineProvider` over that inventory. It is selected the dynamic way
(`compute.provider.adapter` + kwargs). It **claims instead of creating** and
**cleans instead of deleting**:

| Port method | VMs (Harvester) | Registered hosts |
|---|---|---|
| `profiles()` | VM sizes from kwargs | Host classes from kwargs (for example `bm-x86`), with the count of ready hosts and free slots in `details` |
| `create(request)` | Create a VM for the allocation | **Claim** one `ready` host of the profile whose `prepared_digest` matches, with one atomic `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED)` setting `allocation_id`. Repeating with the same allocation id returns the same host. No ready host is `ComputePoolFullError` naming the pool and how many hosts are ready, quarantined and unreachable |
| `get(id)` | VM + VMI state | The held host: `running` when the last probe passed, `failed` when it is unreachable or quarantined, with the reason in `status_detail` and the host name in `phase` |
| `list()` | VMs labelled for this installation | Hosts held by this pool's allocations |
| `delete(id)` | Delete the VM; `False` while it is going | **Clean**: run the whole-host audit (below). `False` while it runs. Clean: the host returns to `ready` (or `out_of_service` if the operator drained it) and the call returns `True`. Leftovers: the host goes to `quarantined` with the findings, the allocation's hold ends, and the call returns `True`, because the allocation is gone even though the host is not usable |
| `close()` | Close the HTTP client | Close the SSH control connections |

`Machine` gains `slots: int | None`, the capacity the provider declares for the
machine. VM providers leave it `None` and the pool's `slots_per_machine` applies, as
today. The ledger currently fixes `ComputeLease.slots` at reserve, before the provider
has been asked. For a fixed inventory it is instead set once, from `Machine.slots`,
when the claimed machine first reports, and a provisioning lease with unknown slots
counts as zero free slots. Changing a host's slot count needs it drained and
claimed again.

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
  cleanup is verified (next section).
- Idle expiry ends the hold on an empty host. It costs nothing, and it runs the
  whole-host audit before the host is claimed again.
- `warm_min` keeps its meaning (free ready slots). For a fixed inventory, nothing
  boots: "warm" counts free slots on held hosts and on `ready` hosts that can
  be claimed in seconds. When the inventory runs out, the pool reports it on its
  health surface and at launch (`readiness: full` with the remedy "register or
  resume a host"). It never quietly runs short.

## Registration

Registration reuses the pairing mechanism of `niuu join`: a short-lived,
single-use pairing code minted by an admin, exchanged by the machine together with
its public key, with consent recorded at mint time and never granted by the
machine. What changes is where the code points: **at the Forge that will own the
host, not at the Guild**. A compute host is inventory of one Forge, not a Guild
node. It registers no instance, sends no heartbeat, and does not appear in
`GET /api/v1/niuu/guild/nodes`.

### Sequence

1. **The admin mints a host pairing code** in the UI (Forge → Compute → Hosts →
   *Add host*) or with `niuu forge hosts pair --forge <instance>`. It goes through the
   Guild facade (`?instance_id=`) to
   `POST /api/v1/forge/compute/hosts/pairing-codes` (`volundr:admin`), with the
   host's runtime (`execution`) and, optionally, `slots`. The code is a scoped workload JWT,
   `token_use: valkyrie_build`, `scopes: ["compute_host_join"]`, empty roles, single
   use (its hash is in the pairing-code table, with a new `purpose` column and the
   chosen `execution`, `slots` and `pool_id`). The UI shows one command to copy:

   ```bash
   sudo niuu join https://forge-hosts.valhalla.asgard.niuu.world --code <code> --as-compute --address 192.168.90.31
   ```

2. **On the host, the operator runs it once, as root.** This is the operator's own
   one-time action; nothing a session does ever runs as root. The CLI:
   - checks the prerequisites before it registers anything and prints every
     missing one with its fix: Linux, cgroup v2 unified, `sshd`, `nftables`,
     `newuidmap`/`newgidmap`, user namespaces allowed (on Ubuntu 24.04, the
     AppArmor `userns` profile for Podman), free subuid/subgid ranges for the slot
     uids, and no conflicting `niuu-slot-*` users;
   - generates the host's Ed25519 key at `/etc/niuu/ssh_host_ed25519_key`
     (`O_CREAT | O_EXCL`, `0600`, root), or loads the existing one, and adds it as
     an sshd `HostKey` (`/etc/ssh/sshd_config.d/10-niuu-host-key.conf`, the same
     paths VMs use, so `SshGuestAccess` pins it unchanged);
   - calls `POST /api/v1/forge/compute/hosts/join` with the code as bearer, and
     sends `name`, `address` and the public key. The route carries
     `require_scope("compute_host_join")`;
   - installs **Forge's SSH public key** from the response into the management
     account's `authorized_keys`, with the options from decision 1 (at least
     `from="<Forge egress>"` and `restrict,port-forwarding`);
   - writes `/etc/niuu/compute-host.yaml` (Forge URL, host id, pool) so that
     `niuu leave --compute` can remove what join installed.

3. **Forge registers the host atomically.** Consuming the code and inserting the
   `compute_hosts` row happen in one transaction, as in
   `GuildJoinRepository.consume_and_register`. The host is `pending`.

4. **Forge probes and prepares.** Forge connects with its key, pinned to the
   registered host key (a mismatch is `unreachable` with "host key does not match
   the registered key"). It then runs the execution's host recipe through the
   existing `SshHostPreparation`, the same versioned, digest-pinned recipe a VM
   gets: Nix multi-user daemon, rootless Podman, the slot supervisor and Skuld's
   host artifacts. It records `facts` and `prepared_digest` and runs the
   whole-host audit. Clean means `ready`; any failure means `quarantined` with the
   recipe stage or finding. The UI follows each step live (the mockup shows this).

Running the recipe from Forge, not from the CLI, keeps one recipe, one digest and
one place that knows what a host was prepared with. When the execution's recipe
revision changes, a host that does not match is no longer claimable for the new
execution. The UI shows "re-prepare", which runs only on an empty host: drain,
re-run, audit, resume.

### Removing a host

*Remove* in the UI needs the host to be `out_of_service` or `quarantined` and to
hold no allocation. Forge removes its own key from the host over SSH, then deletes
the row. A host that Forge can no longer reach is removed from the inventory
anyway; the UI says the key is still on the host and gives `niuu leave --compute`
to run there. That command removes the authorized key, the sshd drop-in and
`compute-host.yaml`. It does not delete slot users or data, because that is
Forge's audit's job. It calls no API, so it needs no signed route.

## Recycling without deletion

### Per slot, every stop

The slot supervisor already tears a slot down and checks it (#1261): it removes the
containers, rules, interface, namespace, slice, directories, user and group, then
fails `slot-down` naming anything left, and the stop is retried. For hosts the
same `down` also checks:

- no process with the slot's uid (including rootless Podman's `conmon` and
  `pasta`/`slirp4netns`);
- no files owned by the slot's uid or subuid range on any local filesystem the
  slot could write (`find -xdev -uid`), in particular `/tmp`, `/var/tmp` and
  `/dev/shm`. Slots get a private `/tmp`, `/var/tmp` and `/dev/shm` in their own
  mount namespace, so this should always be empty; the check proves it;
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

The **audit** is the slot `down` checks applied to the whole slot uid range, plus
host checks: the recipe digest still matches, no `niuu-slot-*` users, groups,
netns, veths, nft rules or slices exist except those of live bindings, nobody is in
the `docker`, `sudo`, `wheel` or `adm` groups who should not be, Nix
`trusted-users` is root only, sshd still uses the registered host key, and disk
and memory are above the configured floor. It runs when a host is registered,
before every claim, when idle expiry ends a hold, on demand ("Check now"), and
every `compute.hosts.audit_interval_seconds` while in service. During service it
only reports; it never stops a session. A finding during service is an error log,
a count and a red row; the host takes no new sessions until it is cleared.

### Taking a host out of service

- **Drain** (UI or `niuu forge hosts drain <name>`): the ledger machine goes
  `draining` (no new slots). When the last session ends, the provider's `delete`
  runs the audit and the host becomes `out_of_service`.
- **Resume**: `out_of_service` → audit → `ready`.
- **Clear quarantine**: the operator fixes the finding and presses "Check now".
  A clean audit returns the host to `ready`. There is no "ignore": a finding goes
  away when the audit stops seeing it.

## Capacity

- **Slots are set per host**, at mint time or after the first check. Once the
  host's facts are measured, the UI suggests a number from them and the runtime's
  per-slot limits, and shows the arithmetic ("64 CPUs − 4 reserved = 60 ÷ 4 per
  slot = 15"). The admin confirms it; Forge never picks silently.
- **Per-slot limits** come from the host's execution (`slot_isolation`: `cpu`,
  `memory_mib`, `pids`, each set or `null`), as on VMs. They are cgroup v2 limits
  on the slot's slice, and limit hits are counted on
  `volundr.compute.slot.limit_hits`.
- **System reserve.** The provider has kwargs `reserved_cpu` and
  `reserved_memory_mib` (no defaults; write them per host class). Forge refuses
  `slots × limit > facts − reserve` for any limit that is set, with the
  arithmetic in the error, at mint, at prepare and when the facts change. A
  limit set to `null` is the operator's decision and shows as "no limit".
- **Disk** has no per-slot quota yet (open question in the slot design). The audit
  enforces the host's free-disk floor, and the floor is a provider kwarg.
- **GPUs**: a host with GPUs is facts only for now. GPU sessions need exclusive
  hosts (`slots: 1`) until GPU partitioning is designed.
- **Exclusive machine** = a host minted with `slots: 1`. Sessions that get a
  `sudo`/`docker` grant (when a runtime offers grants) are placed only on such
  hosts, as the slot design requires.

## Runtimes on a host

One runtime per host, from its execution. Only runtimes that need **no root
daemon in a slot** run on registered hosts:

- **Host** (Nix + rootless Podman per slot, being built in session
  vm-host-mode-nix). This is the primary runtime for hosts. Sidecars and
  per-slot containers run in the slot user's rootless Podman inside the slot's
  namespaces.
- **Container via rootless Podman**: a later mode of the container runtime. Today
  `SshContainerVmRuntime` runs root Docker, so it declares a new
  `requires_root_daemon`, and a registered-host pool refuses it at startup.
- **OpenShell**: possible on `slots: 1` hosts (its sandbox is the boundary, and it
  refuses `slot_isolation`). Not needed first.

## Security

### What changes against `.claude/rules/architecture.md`

**No new authentication mechanism, and no new host-originated route.** Forge
reaches the host over SSH; the host calls Forge only on the existing guest
metadata route. Specifically:

1. **Pairing** uses the scoped workload token exception unchanged. It adds one
   scope, `compute_host_join`, to `KNOWN_WORKLOAD_SCOPES` together with its
   `require_scope("compute_host_join")` on the join route, which keeps
   `tests/test_niuu/test_token_scope.py`'s two-way check green. The route is
   JWT-authenticated (the code is a workload JWT), so Envoy needs no bypass.
2. **Node signing exception: not extended.** Hosts do not heartbeat or leave
   through signed calls. Health is pulled by Forge over SSH. The two Guild routes
   stay the only Ed25519-signed node routes.
3. **VM guest metadata exception, host-key identity: amended.** Today the key is
   "the Ed25519 SSH host key Forge generated for the allocation", and Forge
   verifies it against the public key in the allocation's machine bootstrap. For a
   registered host, **the host generates the key, the private key never leaves
   the host, and Forge stores only the public key**, received with the pairing
   code. Proposed amendment (added in the provider PR):

   > For a registered host (`RegisteredHostMachineProvider`), the key is generated
   > on the host at `niuu join --as-compute` and only its public key is stored, on
   > the `compute_hosts` row, received under a single-use `compute_host_join`
   > pairing code. Forge never generates or holds that private key. The key
   > belongs to the host, not to one allocation, so the limits still bind
   > to the allocation: the request body names the allocation (a mismatch with the
   > authenticated allocation is the identical 401, so a captured request of an
   > earlier allocation of the same host cannot be replayed under a later one),
   > the watermark is per allocation, and only the allocation currently holding
   > the host gets an answer. Removing or re-keying the host revokes it.

   `HostKeyGuestIdentity` changes in one place: `machine_bootstrap_for` resolves
   the allocation's public key from the host row instead of from a bootstrap that
   Forge generated. `SshGuestAccess` gets a registered-key mode that pins the
   registered `.pub` and writes no host key into the bootstrap.
4. **Multi-slot credentials use SSH push.** `MetadataCredentialDelivery` serves
   only slot 0 (`serves_slots = False`), so multi-slot hosts use
   `SshPushCredentialDelivery`. Forge writes each slot's credentials over SSH into
   that slot's `0700` directory. The metadata identity serves `slots: 1` hosts,
   and multi-slot hosts once per-slot metadata lands (a follow-up shared with
   VMs). On every host, the host key is root-only and slots are never root, which
   is the condition the rule sets for more than one slot.

### Forge's access to the host

Forge needs root on the host: creating slot users, network namespaces, nft rules
and slices is root's job. That is the same trust it has in a VM, but a host is
not disposable and may sit on a LAN with BMCs and other machines. Decision 1
chooses how narrow it is. In every option:

- key only, never a password; `from="<Forge egress addresses>"`; and
  `restrict,port-forwarding` with `permitopen`/`permitlisten` limited to the
  slot tunnel ports;
- the Forge key is per Forge installation and stored like the VM key
  (`compute.access` secret), never in a session;
- removing the host, or rotating Forge's key, is one action in the UI.

### Slots on bare metal

- No sudo and no Docker group for any slot user. The audit fails a host where a
  slot user is in `sudo`, `wheel`, `docker` or `adm`.
- Rootless Podman per slot user, inside the slot's user, network and PID
  namespaces and slice. Its network is the slot's netns (pasta), not the host's.
- Nix: slots are `allowed-users`, never `trusted-users` (a trusted user can add
  substituters and poison the shared store for everyone).
- `loginctl` linger off, no user cron, private `/tmp`, `/var/tmp` and `/dev/shm`.
- **Egress**: slots NAT out of the host's default route, and nft rules deny the
  host itself, other slots and the container networks, as on VMs. On bare metal,
  the LAN (RFC 1918 ranges, the BMC network) is also denied by default, except
  the addresses the operator lists (`allowed_lan_cidrs`). See decision 4.
- The host should be dedicated to Forge. Another workload on the host shares the
  kernel with slots and is reachable by Forge's root key.

## Operator and UI flow

The mockup shows two screens:

1. **Hosts** (Forge "Valhalla Hosts" → Compute → Hosts): the pool summary (hosts by
   state, free slots out of total, warm target), and one row per host with its
   state, runtime, slots in use, CPU and memory, last check and actions (Drain,
   Resume, Check now, Re-prepare, Remove). Quarantined and unreachable rows show
   their findings inline.
2. **Add host**: choose the runtime (and slots, or leave them for later), copy the
   one-line command, and follow the host through *waiting for join → reached (key
   pinned) → preparing (recipe stages) → audit → set capacity → ready*. The
   capacity step shows the measured facts and the suggested slot count with its
   arithmetic. The code's expiry counts down, and it can be revoked.

The same actions are on the CLI for scripting: `niuu forge hosts pair | list |
drain | resume | check | remove`.

## PR plan

Each PR passes CI on its own and changes no behaviour until the provider is
configured.

1. **docs**: this design and the mockup (this PR).
2. **Host inventory**: migration `0001NN_compute_hosts` (table, state check, unique
   name per pool, unique public key, `purpose`/`execution`/`slots`/`pool_id` on the
   pairing-code table), the domain model and state machine, the
   `RegisteredHostRepository` port and its asyncpg adapter. Tests with mocked
   pools plus an integration migration test.
3. **Pairing and join**: the `compute_host_join` scope and its `require_scope`,
   the mint and join routes on Forge and through the Guild facade, and
   `niuu join --as-compute` (prerequisite check, key, sshd drop-in, authorized key,
   `compute-host.yaml`) with `niuu leave --compute`. Rule text for the scope.
4. **Provider and identity**: `RegisteredHostMachineProvider` (claim, get, list,
   clean, probe), `Machine.slots` and ledger slots-from-machine, fixed-inventory
   readiness, `SshGuestAccess` registered-key mode, `HostKeyGuestIdentity` reading
   the key from the host row, and `requires_root_daemon`. Includes the
   architecture rule amendment above.
5. **Verified recycling**: the slot `down` additions (uid-owned files, IPC, linger,
   private tmp mount namespace), the whole-host `audit` verb in the slot supervisor,
   quarantine, the audit interval knob, the counters, and `supports_reuse` on the
   host runtime when verified cleanup is on. Live proof on a throwaway host: plant
   a leftover and watch it quarantine.
6. **Admin API**: list hosts, drain, resume, check, re-prepare and remove; host
   state on the pool snapshot and health surface; operator doc
   `docs/operator/compute-hosts.md`, which lists every new knob with what bounds
   it and what happens on a hit.
7. **Web**: the Hosts panel and the Add host flow, after this mockup is approved.
8. **Infra** (jve/infrastructure): a "Valhalla Hosts" Forge (one controller pod,
   the volundr chart, provider `RegisteredHostMachineProvider`), registered in the
   Guild. Then the first host joins and runs two host-mode sessions side by side.

Later, separately: multi-pool Forge (decision 2), forced-command host agent
(decision 1, option B), macOS one-slot hosts, hosts behind NAT.

## Decisions for Jozef

1. **How much root Forge gets on a host.**
   A. A root SSH login like VMs, narrowed by `from=`, `restrict` and port-forward
   limits. This reuses all runtime code. *Recommended first, on hosts dedicated to
   Forge.*
   B. A forced command: Forge's key can only run `niuu-host-agent` with fixed
   verbs (prepare, slot up/down, audit, write slot files, tunnel), with no shell.
   Stronger, but the runtime moves from shell snippets to verbs: about two more
   PRs. *Recommended as the hardening step after the first host works.*
2. **Which Forge the hosts join.** A Forge has one pool and one provider today.
   A. A separate light Forge, "Valhalla Hosts" (one pod), next to "Valhalla VMs".
   No core change. *Recommended.*
   B. Multi-pool Forge: one Forge offers VMs and hosts, and Launch chooses where.
   The catalog is already shaped for it (executions name `pool_id` and
   `provider_binding`), but `ComputePoolService` assumes one pool throughout. A
   large change; do it after hosts work.
3. **Where pairing lives.** At the owning Forge, through the Guild facade, with
   hosts not being Guild nodes. *Recommended.* The alternative is to make hosts
   Guild nodes that point at a Forge, which gives two registries for one machine.
4. **Slot egress to the LAN.** Deny RFC 1918 by default with an allowlist
   (*recommended*), or the host's own egress as on VMs.
5. **Slot reuse on hosts.** Reuse slots in place after a verified clean `down`
   (*recommended*, otherwise a host idles while it drains), or drain and audit the
   whole host after every N sessions.

## Open questions

- Hosts that Forge cannot reach over SSH (home NAT): a host-initiated reverse SSH
  connection to a Forge bastion would keep "Forge pulls", but needs a new
  listener.
- Per-slot disk quota (shared with the slot design).
- Whether a periodic re-prepare (for example weekly, to pick up Nix updates) is
  worth a knob, or recipe revisions are enough.
- GPU partitioning across slots.
