# VM session slots and runtimes

Status: proposal, for review. Nothing here is built yet.

Today a VM Forge runs one session per VM, and only one runtime: Skuld in Docker
with host networking (`SshContainerVmRuntime`). This proposal lets one VM serve
several sessions ("slots"), lets each session choose how it runs (Docker,
OpenShell, or directly on the VM), and keeps sessions on one VM apart from each
other.

## Goals

- Several sessions per VM, configurable per pool, default one.
- Three runtimes in a VM, chosen per session among those the operator enabled:
  **Docker**, **OpenShell**, and **host** (no image; the VM's own system).
- Sessions on one VM are separated: their files, credentials, processes,
  network ports and resources.
- Sessions can still install software, and share large toolchains (a Go
  compiler, Node) instead of each slot downloading its own.
- Warm capacity stays: sessions should start in seconds, and the pool boots new
  VMs ahead of demand when free slots run low.
- Host mode is opt-in, because it gives a session the VM's own system.

## Model

### Machines and slots

A pool's policy gains `slots_per_machine` (default 1, which is today's
behaviour). A machine (an allocation) has that many slots. A session holds one
slot; the lease ledger records `(allocation, slot)` per session instead of one
session per allocation.

A machine is created for one **machine profile** (image, size, cloud-init) and
can serve every runtime that profile prepares. Placement puts a session in a
free slot of a ready machine whose profile serves the session's runtime, and
only boots a new machine when none has one.

### Warm capacity

`warm_min` counts **free slots**, not idle machines. The pool keeps at least
`warm_min` free, ready slots: when sessions take slots and the free count drops
below it, the pool boots another machine ahead of demand, within
`max_machines` and `max_provisioning`. Examples with `slots_per_machine: 4`:

| warm_min | Pool behaviour |
|---|---|
| 0 | No spare capacity: the first session boots a VM (minutes). |
| 1 | Always one free slot. When the last free slot is taken, another VM boots. |
| 4 | Always a whole VM's worth free; a burst of four starts in seconds. |

A machine counts towards warm capacity only once it is ready (booted, prepared,
images pulled), as today. A pool that hits `max_machines` stops adding machines
and reports it on the pool's health surface, rather than quietly running
without spares.

### Recycling

Stopping a session cleans **its slot**: archive the slot's workspace and home,
stop its process tree / container / sandbox, remove its user, network namespace,
cgroup and credential files. The VM keeps serving its other slots.

Machine lifecycle, per policy:

- `replace` (default): a machine accepts new sessions until it has served
  `max_sessions_per_machine` sessions or reached `max_machine_age_seconds`
  (both optional; unset means no limit), then **drains**: it takes no new
  sessions and is deleted once its last slot is free. A drained machine's
  capacity is replaced by the warm-capacity rule above. This keeps long-lived VMs
  from drifting (left-over packages, disk fill) without interrupting anyone.
- `reuse`: freed slots return to the pool; the machine lives until idle expiry.
- An empty machine above the warm target expires after `idle_timeout_seconds`,
  as today. Lowering capacity or draining never stops an active session.

A slot whose cleanup fails is quarantined (not reused) and keeps its archive and
stop intent for retry, as allocations are today; the machine's other slots are
unaffected, and the machine is not offered for new sessions until the slot is
resolved or the machine is drained.

## Separation per slot

Every runtime gets the same slot boundary:

- **User:** each slot runs as its own Linux user (`niuu-slot-<n>`, a fixed uid
  range), with its own home and workspace directories (mode 0700) and credential
  files only that user can read.
- **Network:** each slot has its own network namespace. Two sessions can both run
  Vite on `:5173`. Skuld's broker, the preview forwarder and the port scanner
  run inside the slot's namespace and see only its ports; Forge's SSH tunnel
  reaches each slot's broker through a per-slot forward. Outbound traffic leaves
  through the VM's interface (per-slot NAT).
- **Processes and resources:** each slot has its own cgroup (v2) with CPU,
  memory and PID limits from the profile, and its own PID namespace, so one
  session cannot see or signal another's processes or starve the VM.

Per runtime:

- **Docker:** a container per slot, on its own network instead of
  `--network host`, running as the slot's uid. Inside its container a session can
  install anything; the container is the boundary.
- **OpenShell:** a sandbox per slot, which OpenShell already does; its policy
  stays the boundary.
- **Host:** no image. Skuld's slot supervisor (run by the runtime over SSH, not
  a systemd unit) starts the session directly in the slot's user, network and
  PID namespaces and cgroup, against the VM's own filesystem and toolchain.

## Installing and sharing software

In Docker and OpenShell a session installs into its own container or sandbox,
as today. Host mode needs more, because a slot is not root:

1. **Shared baseline, read-only.** The machine profile's preparation installs
   toolchains every slot can use (Go, Node, Python, build tools) system-wide.
   Slots use them but cannot change them, so one session cannot break another's
   compiler.
2. **Per-slot installs, no root.** Language tooling installs into the slot's home:
   `go install` → `~/go/bin`, npm/pnpm prefixes, `uv tool`, `pip --user`,
   `cargo install`, mise/asdf for other versions.
3. **Shared, deduplicated installs without root.** A multi-user Nix store on the
   VM: any slot can install a package (`nix profile install nixpkgs#go_1_24`),
   the daemon builds it into the shared, content-addressed `/nix/store`, and the
   next slot asking for the same package gets it instantly. Slots cannot modify
   each other's packages. Shared download caches (Go module cache, npm, uv,
   pip) are per-user directories by default and can be made a shared read-mostly
   cache through the same mechanism.

System packages (`apt install`) need root and change the VM for every slot, so
they are not available to a slot in a shared VM; see the opt-in below.

## Host mode is opt-in

- The operator enables host mode on a machine profile; a session can only choose
  a runtime its profile enables.
- A slot can be granted **sudo** or **Docker access**. Either is root-equivalent:
  it can read every other slot. The scheduler therefore gives a session with such
  a grant **a machine to itself** (its machine takes no other sessions while it
  runs), whatever `slots_per_machine` says. That is enforced in placement, not
  left to convention.
- Without those grants, the per-slot separation above is what keeps host-mode
  sessions apart, the same as in Docker.

## Choosing a runtime

A session's `workload_config` names its runtime (`docker`, `openshell`, `host`)
and optional grants. Launch shows a runtime choice limited to what the host's
profiles enable (a mockup comes before that UI is built). The existing versioned
execution catalog (`docs/operator/vm-compute.md`) carries the per-runtime
adapter bindings; `ProfiledVmRuntime` stays for single-runtime pools.

## Live preview and progress

Preview already addresses a session, not a machine; with slots the router
reaches the session's own slot broker, whose scanner sees only that slot's
namespace, so ports never collide. Startup progress (niuulabs/niuu#1254) gains
stages for "placed in slot n of machine m", or "booting a new machine" when no
free slot exists.

## Migration

- `slots_per_machine: 1` and no runtime choice reproduce today exactly, so the
  ledger change ships first with no behaviour change.
- Existing leases become slot 0 of their allocation.
- The Docker runtime moves off `--network host` per slot; preview's scanner
  config already excludes the VM's own services by user.

## Open questions

- Per-slot NAT and outbound policy: should slots get the same egress as the VM,
  or a narrower per-session policy (OpenShell has one already)?
- Disk: a per-slot quota on workspace/home (XFS project quotas or a per-slot
  loop volume), or rely on the VM's disk size and the drain rule?
- GPU VMs: one GPU per machine means GPU sessions are always single-slot.
