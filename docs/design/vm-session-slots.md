# VM session slots and runtimes

Status: accepted; being built. PR 1 (slot ledger) is in progress.

Today a VM Forge runs one session per VM, and only one runtime: Skuld in Docker
with host networking (`SshContainerVmRuntime`). This proposal lets one VM serve
several sessions ("slots"), adds runtimes besides Docker (OpenShell, and
directly on the VM), one per Forge, and keeps sessions on one VM apart from each
other.

## Goals

- Several sessions per VM, configurable per pool, default one.
- Three runtimes, one per VM Forge: **Docker**, **OpenShell**, and **host** (no
  image; the VM's own system).
- Sessions on one VM are separated: their files, credentials, processes,
  network ports and resources.
- Sessions can still install software, and share large toolchains (a Go
  compiler, Node) instead of each slot downloading its own.
- Warm capacity stays: sessions should start in seconds, and the pool boots new
  VMs ahead of demand when free slots run low.
- Host mode is opt-in, because it gives a session the VM's own system; even
  there, no slot is root, and containers run rootless.

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

A machine's free slots count towards warm capacity from the moment it is
provisioning, as spares do today, so the pool never boots twice for the same gap;
a session is only placed on it once it is ready (booted, prepared, images pulled). A pool that hits `max_machines` stops adding machines
and reports it on the pool's health surface, rather than quietly running
without spares.

### Recycling

Stopping a session cleans **its slot**: archive the slot's workspace and home,
stop its process tree / container / sandbox, remove its user, network namespace,
cgroup and credential files. The VM keeps serving its other slots.

Machine lifecycle, per policy:

- `replace` (default): a machine accepts new sessions until it has served its
  session limit or reached `max_machine_age_seconds` (optional; unset means no
  age limit), then **drains**: it takes no new sessions and is deleted once its
  last slot is free. The session limit is `max_sessions_per_machine` when set,
  otherwise `slots_per_machine`: by default each slot serves one session, which
  at one slot is exactly today's "replace after every session". A limit above
  `slots_per_machine` reuses cleaned slots in place and so needs a runtime that
  supports reuse. A drained machine's capacity is replaced by the warm-capacity
  rule above. This keeps long-lived VMs from drifting (left-over packages, disk
  fill) without interrupting anyone.
- `reuse`: freed slots return to the pool; the machine lives until idle expiry.
  The drain limits apply to `replace` only and are rejected under `reuse`.
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
they are not available to a slot; a slot builds and runs containers rootless
instead (below).

**Decision: Nix is the shared software layer.** It is the one mechanism that lets
users who are not root share installs safely: content-addressed paths, side-by-side
versions, and no slot able to change another's packages. It also installs on any
Linux host or Mac without Forge, so the same package set and versions work in a VM
slot, on a plain host and on a developer's Mac. Homebrew is not used between slots:
its prefix is one mutable tree, so a shared prefix lets one slot upgrade or remove
another's tools, and a private prefix per slot loses its prebuilt bottles and
compiles from source. Homebrew stays for a developer's own host, where one user
owns the machine.

## Host mode is opt-in

- Host mode is a server of its own: a VM Forge (and its Guild host) runs exactly
  one runtime, configured on the server, so an operator enables host mode by
  running a host-mode Forge. Sessions never choose a runtime and pools never mix
  them.
- **No slot is ever root on the machine.** There are no grants: no sudo, no
  membership of the guest's `docker` group, never the guest's Docker socket.
  Either would be root-equivalent and read every other slot, and there is no
  "machine to itself" path that lifts this. A session that needs root runs on a
  container runtime, where root is its container's.
- **Every slot runs containers rootless** (Podman), inside its own boundary:
  - subordinate uids and gids per slot that overlap no other slot and no VM
    account;
  - rootless networking (pasta) in the slot's own network namespace, so a
    published port is the slot's alone and opens as a preview;
  - a cgroup delegated from the slot's own, so every container sits under the
    slot's limits and per-container limits work;
  - storage in the slot's home (the machine's, never archived) and a runtime
    directory of its own;
  - the session's PID namespace: containers end with the session.
- The VM's own credentials (its identity token, Forge's SSH host key) are hidden
  from a host session's mount namespace, and so from its containers, whatever
  their modes: the session runs on the VM's filesystem.
- With these, the per-slot separation above is what keeps host-mode sessions
  apart, the same as in Docker.

## Choosing a runtime

**Decision: one runtime per server.** A VM Forge runs one runtime, named in its
configuration (its execution catalog's default execution); each runtime is its
own Forge and Guild host (for example "Valhalla VMs" for Docker and "Valhalla VMs
(host)" for host mode). A session picks the server, not a runtime, and carries
no grants. The existing versioned execution catalog
(`docs/operator/vm-compute.md`) carries the runtime's adapter bindings and host
recipe; `ProfiledVmRuntime` stays for single-runtime pools. Launch shows the
host's runtime read-only under the host ("Runs as: Container"), from
`GET /api/v1/forge/session-runtimes`, which answers that one runtime; there is no
per-session choice and no `launch_executions`. A pool runs more than one slot per
machine only when its runtime declares slot support (`VmRuntime.supports_slots`);
otherwise Forge refuses the policy.

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
