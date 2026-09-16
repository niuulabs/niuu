# VM execution through Völundr: verification and implementation plan

Status: the selected local Harvester deployment and shared VM backend are implemented. The disposable VM allocation layer, Harvester
API adapter, PostgreSQL repository, and operator lifecycle proof command are
implemented. Live Harvester VM lifecycle proof passed on 2026-09-16. Forge
runtime integration now includes a generic VmPodManager and a pinned-SSH Docker
runtime, with local-disk archive/restore. Live Codex execution through the
existing Niuu credential broker passed. Production deployment authentication,
permanent provider credentials and deployment-scale load remain operational acceptance items.
Provider-neutral warm pools and their admin settings are now implemented.
Verified on 2026-09-16 against local `dev`, HEAD `302507990`, including the
existing uncommitted working-tree changes. Those changes were left intact.
Source discussion: https://chatgpt.com/share/6aaa7520-9028-83ea-88f2-0e71daaa28c9

## Current acceptance checklist (2026-09-16)

- Complete: generic provider/runtime ports, durable allocation/admission, Harvester
  API integration, local storage archive/restore, credential delivery and renewal,
  warm pools, crash recovery, admin policy/status/disposal, and profile discovery.
- Complete: local controller/PostgreSQL supervision, real Codex execution through
  Guild, Ting target discovery, two-machine admission burst, session isolation,
  controller restart/interrupted-stop recovery, and verified zero-VM cleanup.
- Added after review: adapter-owned profile revisions prevent assignment of a
  stale spare after a profile definition changes. Legacy unversioned spares are
  replaced; already-bound sessions remain on their existing allocation.
- Pending operator input: a durable Harvester credential source and the maximum
  VM count for the next live load/churn run. The pool remains paused and drained.
- Pending deployment selection: production gateway/identity and credential-store
  verification. The chosen loopback installation uses existing development
  identity and the existing Spark credential broker; it cannot certify a different
  production topology.
- Deferred by scope: EC2/private provider implementation, multiple sessions per VM,
  and capacity-aware cross-environment placement.

The ordered work packages below retain the original design discussion. The actual
reset policy is single-use allocation plus complete machine replacement, so there
is no in-place reassign/scrub generation. `IDLE` means a prepared, unbound guest;
`READY` means infrastructure exists but runtime preparation/assignment is pending.
Per-allocation PostgreSQL advisory locks exclude concurrent operations and release
on controller connection loss. The completion/evidence sections describe the
implemented contract where the original proposal differs.

## Implementation progress and proof boundary

The first increment implements `MachineProvider`, `ComputeLeaseService`, a
PostgreSQL repository with atomic pool admission and per-allocation operation
locks, and `HarvesterMachineProvider`. Provider and authentication adapters are
loaded by fully qualified class name in `volundr.compute.main`. Private adapters
can implement the same port without core changes; no EC2/private stub is added.

Harvester operations use the documented HTTP APIs for VirtualMachines, VMIs,
images, networks, PVCs and cloud-init Secrets. Stable allocation IDs, ownership
labels and request fingerprints support replay and partial-create recovery.
Deletion verifies VM/VMI disappearance before disposing of its root disk.
Failed or pending cleanup remains charged to pool capacity.

Authentication uses the existing `HttpAuthPort`, including OAuth adapters or a
mounted rotating bearer-token file. HTTPS certificate verification is mandatory;
private CAs are configurable. Requests do not follow redirects. Provider
credentials never enter bootstrap data. An operator must provision appropriately
scoped API credentials and namespace RBAC; the application does not mint cluster
administrator credentials. Details: [VM compute operations](../operator/vm-compute.md).

The initial service allocates a disposable VM per claim and destroys it on
release. `READY` here means infrastructure readiness, not Skuld authentication
or runtime readiness. The initial increment had no warm-pool controller. Forge composes
`VmPodManager`, the same lease service, and a configurable `VmRuntime` adapter.
The SSH runtime archives workspace/home to controller-local disk before deletion
and restores that archive on the next allocation. The warm-pool controller now binds clean guests once and replaces used machines
instead of scrubbing and reassigning a disk; the standalone CLI is infrastructure-only.

The focused suite passed 53 tests without warnings, with 93% combined line/branch
coverage across the new compute modules. It exercises API translation using
explicitly fake transport and providers. Two additional tests passed against an
isolated real PostgreSQL instance, proving
concurrent admission and operation exclusion with the actual migration. That
evidence does not establish live Harvester compatibility. The `prove` command
creates one real VM, waits for VM readiness plus a reported address, and confirms
cleanup before emitting success; it explicitly excludes agent-runtime proof.

Live Harvester verification passed on Vanaheim (Harvester 1.8.2), using namespace
`asgard`, network `asgard/asgard-10gb-1` (VLAN 90), and the imported Ubuntu 24.04
cloud image. A 2-vCPU, 2-GiB VM with an 8-GiB disk reached running state and
reported `192.168.90.7`. The durable claim reached `released`; a separate API
inventory confirmed no remaining VM, VMI, PVC or Secret belonging to the proof.
See the [recorded evidence](evidence/harvester-live-proof-2026-09-16.json).

The live run exposed two adapter defects, now fixed: image references must
accept DNS subdomains containing dots, and this cluster requires explicit guest
memory in the VM manifest. Regression checks cover both. Rejected attempts also
completed cleanup. The supplied bearer token worked with verified TLS; its
temporary local copy was removed after verification. Production least-privilege
RBAC and long-running token renewal were not established by this short-lived test.

## Forge runtime increment

The user selected a local Völundr server, `ghcr.io/niuulabs/skuld:dev` and local
workspace storage. The server runs on loopback port 8088, using isolated real
PostgreSQL. Harvester profiles configure image, CPU, memory and disk size;
provider/profile `cloud_init` defaults install the guest prerequisites.

Guest control uses OpenSSH with a separately generated, pinned host key for each
allocation. The existing credential-store port retains bootstrap material across
controller restarts; database leases contain only a reference and fingerprint.
Provider credentials remain on the controller. Skuld and its reverse platform
callback bind guest loopback. Existing Forge HTTP/WebSocket proxy routes connect
through the authenticated SSH tunnel.

A graceful stop first stops Skuld and atomically saves an archive on the local
controller disk, then releases the VM and its disposable root disk. Failed
archive transfer retains the VM and pool claim. Running-session writes remain
on guest disk until stop: abrupt guest/disk loss is not protected by this policy.
Use Forge stop for session allocations, not the infrastructure-only CLI release.

Focused validation: 167 tests passed without warnings; combined line/branch
coverage across compute and VM modules was 91.30%. Live checks established real
Skuld health, its WebSocket handshake, platform callbacks, workspace archive and
restore into a replacement VM, and reconnection after graceful controller restart.
See [Forge runtime evidence](evidence/harvester-forge-proof-2026-09-16.json) for
cleanup results and the limits of this proof.

Follow-up crash recovery now supervises SSH lifetime through a controller-owned
pipe and persists runtime-start completion. Reconciliation resumes interrupted
startup in a tracked background task; failures retain capacity and remain visible.
Live SIGKILL tests recovered both provisioning and running sessions on the same
allocation. See [crash-recovery evidence](evidence/harvester-crash-recovery-2026-09-16.json).

Live model execution now passes using Skuld's existing Codex credential adapter
and the selected connection on the Spark's existing Niuu credential broker.
Codex (`gpt-5.6-sol`) created and read a workspace file; a separate pinned SSH
read confirmed its contents. Stop preserved that file in the local workspace
archive and disposed of the allocation. No subscription refresh credential was
copied into the VM, and no new login or renewal protocol was introduced.
See [Codex execution evidence](evidence/harvester-codex-execution-2026-09-16.json).

Unattended inventory recovery, durable retry deadlines, clean standby pools,
interrupted stop recovery and admin settings are implemented. Production
deployment authentication and deployment-scale load remain acceptance work. The local proof uses the existing development identity
adapter and does not establish production end-user authentication.


## Follow-up: correct Ting target concentration

The user requested a bug fix after the initial verification. Ting's new-work
placement now randomly selects among visible eligible targets for issue,
template, event-trigger and workflow launches. Explicit target choices remain
fixed; invalid or tag-ineligible explicit targets fail rather than relocate.
`primary_for_*` retains its stable primary-target meaning for existing consumers.
This corrects the first-match launch behavior described in the historical
verification below. Guild's own default routing is unchanged.

Queue exclusion and owner concurrency accounting now include sessions across
visible targets. Follow-up review, transcript and run-message operations resolve
the owning target before operating on an existing session. Provider errors do
not cause a mutation to be retried against another target.

Random placement spreads launches statistically; it is not least-loaded or
capacity-aware scheduling and does not reserve compute. The lease, quota and
gateway work below remains necessary. Source line references below refer to
the initial verification snapshot and may shift with this follow-up.

Follow-up validation: `uv run --extra dev pytest -q tests/test_ting --no-cov`
passed all 1,771 tests with no warnings. Lint, formatting and diff checks passed
for the changed code. This is test-suite verification, not a live multi-target
deployment or a coverage measurement.

## Decision

Build one generic VM execution backend, with infrastructure-specific provider
adapters. Harvester is a concrete integration; EC2 is a planned future
integration, and a proprietary provider must be implementable privately against
the same port. The backend must not depend on details of that private service.

Deploy Völundr in each execution environment and register the environment with
Guild. Manage VM allocation and reuse below the existing `PodManager` port,
with durable leases from the first implementation. Start with one active Forge
session per VM and one explicitly configured provider per pool. A session may
itself run several agents; session count is not necessarily agent count.

Keep Kubernetes, Docker, local process, and OpenShell adapters working as they
do today. Do not make the VM project depend on migrating them to leases.
Keep lease policy in Völundr while Völundr is its only consumer. Extract shared
infrastructure to `niuu` when another consumer actually needs it. Ravn retains
judgment and agent lifecycle semantics; Skuld retains runtime/session behavior.

```text
Ting / browser
      |
Guild discovery and API routing
      |
environment Völundr + existing session proxy
      |
VmPodManager ------ runtime bootstrap / health / stop
      |
ComputeLeaseService ------ PostgreSQL lease repository
      |
MachineProvider port
      +-- Harvester adapter
      +-- EC2 adapter (future)
      +-- privately supplied proprietary adapter
      |
VM: Skuld + selected runtime + attached session storage
```

The diagram is the target architecture. Today Ting discovers through Guild
but its resolved HTTP adapters call Völundr directly. A deployment that only
allows traffic through the upstream gateway must also route that traffic.

## Generic backend and provider boundary

`VmPodManager` owns Forge-to-runtime adaptation. `ComputeLeaseService` owns
claims, quotas, reconciliation and warm-pool policy. `MachineProvider` owns
translation to the infrastructure API. Provider SDKs, resource schemas and
authentication stay inside adapters. Neither the session service nor the lease
service switches behavior on provider names.

The common contract uses allocation IDs, idempotency keys, desired resource
requirements, an operator-selected image/profile, bootstrap input, normalized
observed state and connection information. Provider resource IDs are opaque.
Provider-specific image IDs, network configuration, volume classes, regions
and machine types are resolved in adapter configuration/profile mappings; do
not expose Kubernetes manifests or EC2 request objects as domain requirements.
Retain durable provider operation references needed for recovery without making
the lease state machine interpret their contents.

Use the repository's dynamic fully qualified `adapter` plus kwargs pattern
for provider loading. Compose ports and services in the composition root. A
private provider can be installed as a separate Python package and selected
through configuration; adding it must not require changes to core dispatch,
lease policy, a provider enum, or a central provider factory switch.

Separate portable runtime bootstrap content from how an adapter delivers it.
Cloud-init/user-data is one possible delivery method, not a requirement of the
domain port. Later session start/stop and health checks need an authenticated
runtime control path independent of first-boot provisioning. Reuse existing
runtime/control facilities where they fit; provider extensions implement a
runtime-control port only if that boundary is needed by the supported control
mechanisms. Do not require arbitrary shell execution in `MachineProvider`.

Model meaningful differences explicitly: reliable reset/reimage, retained
storage and resource-discovery support. Validate pool policy against adapter
capabilities before enabling it. A provider that cannot safely reuse VMs can
serve an explicitly configured disposable pool; it cannot silently ignore a
configured reuse policy. Do not assume identical stop, reset, delete or quota
semantics across providers.

Harvester documents VM creation through Kubernetes `VirtualMachine` objects
and cloud-init startup configuration. Those are Harvester-adapter implementation
details, not reasons to run the VM lifecycle through the existing session-pod
adapter. Verify the deployed Harvester version, images, networking, storage
and reset behavior before enabling the adapter in a deployment. See the official
[Harvester API documentation](https://docs.harvesterhci.io/v1.8/api/harvester-apis/).

Use shared provider contract tests for normalized lifecycle, operation replay,
inventory ownership and errors, plus adapter-specific tests for real API
translation. A second simulated provider in tests should demonstrate that
shared lease behavior does not require Harvester fields. Production adapters
must be complete; do not add EC2 or proprietary stubs as extensibility proof.

## What the discussion got right, and what needs correction

| Claim | Verification and implication |
| --- | --- |
| Niuu picks a registered target randomly. | False. `src/ting/adapters/volundr_factory.py:69` returns the first adapter; sorting at line 122 prefers default, then name and creation time. `src/ting/domain/services/dispatch_service.py:430` returns the first tag match. Registering hundreds of Völundr instances does not create a load balancer. |
| Guild chooses an environment. | Supported for explicit IDs, tags, and default selection. `src/niuu/adapters/inbound/rest_volundr.py:445` chooses the default or first visible match. This is routing, not capacity-aware scheduling. |
| `PodManager` is an existing extension point. | Confirmed in `src/volundr/domain/ports.py:420`: start, stop, status, readiness, optional capacity and initial endpoints. Implementations include Flux, direct Kubernetes, local process, Docker, and OpenShell. No need to rename the port first. |
| The whole launch contract is infrastructure-neutral. | Only partly. `SessionSpec` at `src/volundr/domain/models.py:1618` includes `PodSpecAdditions`, Helm-shaped values, volumes, init containers and service accounts. Storage and workload-identity contributors emit PVC/projected-token configuration. A VM adapter must deliberately support or reject these settings. |
| Existing Docker execution can be used unchanged on remote VMs. | False. `src/volundr/adapters/outbound/docker_container.py` inherits local workspace/process behavior and mounts host paths. Reuse useful runtime conventions, not its local filesystem assumptions. |
| Existing capacity tracks limit/active/available. | Confirmed at `src/volundr/domain/ports.py:403`; available is computed and a remedy is included. `None` means uncapped. It provides no warm, provisioning, quota, or reservation model. |
| Hundreds of concurrent starts can rely on that capacity check. | No. `SessionService.ensure_capacity()` at `src/volundr/domain/services/session.py:761` reads capacity before background provisioning; it does not atomically claim it. Reservation must be authoritative in lease acquisition. |
| Ting already has capacity policy. | It has a per-owner concurrent-run budget based on session/run counts (`dispatch_service.py:547`), not a provider capacity or warm-pool scheduler. Preserve the distinction. |
| Völundr can proxy to a non-local Skuld. | Confirmed. `src/niuu/ports/session_proxy.py` describes external targets; `src/niuu/session_proxy.py:408` registers HTTP, health and WebSocket routes. Standalone Völundr installs this proxy and a target resolver in `src/volundr/main.py:500` and `:788`. |
| Registering partner Völundr automatically funnels all traffic through upstream Guild. | Not established and not true for the ordinary Forge path as currently wired. `rest_volundr.py:58` rebases relative chat URLs to the remote instance origin. Guild has Ravn/resident socket handling (`rest_ravn.py:82`); the host middleware at `src/niuu/app.py:267` dispatches the resident-shaped socket path. These do not establish a general Forge HTTP/WS hop through Guild. |
| A local Völundr eliminates per-VM public ingress. | Yes, provided it can reach workers and callers can reach Völundr, directly or through a configured gateway. Workers still need reachable identity, credentials, model, source-control and other configured services. Guild registration does not create a tunnel. |
| Ravn has a related spawn abstraction. | Confirmed in `src/ravn/ports/spawn.py` and subprocess/Kubernetes adapters under `src/ravn/adapters/spawn/`. It returns discovered peer IDs and has different lifecycle semantics. This is precedent, not a reason to merge it with Forge provisioning now. |
| VMs contain no important durable state. | Not guaranteed. Workspaces, local changes and runtime continuation need a storage contract. `StorageContributor.cleanup()` archives workspaces (`contributors/storage.py:83`), and session startup overlays continuation IDs (`services/session.py:1038`). Scrubbing must not erase the only recoverable session copy. |
| A compute lease/warm-pool subsystem already exists. | No matching VM compute-lease service/provider implementation found in the inspected source. Flokk membership leases and HelmRelease resources are different concepts. |
| The Valkyrie portability document specifies this scheduler. | No. `docs/plans/observatory-valkyrie-architecture.md:11` supports Guild's registry/fan-out role and infrastructure portability for topology/discovery. It does not implement or specify the VM scheduler proposed here. |

## Lease design constraints

Use one durable record for each managed allocation, with its current session
assignment and an incrementing generation. The pool allocation survives
session completion; the session's claim on it does not. Keep session storage
identity separate from machine identity.

Minimum durable data: allocation ID, environment/pool, provider resource ID,
provider operation/idempotency key, state, current session and launch generation,
tenant/owner boundary, compatible image and resource profile, internal endpoint,
operation/deadline timestamps, idle timestamp, last error, and cleanup status.
Persist references to credentials, not credential payloads.

Use these distinct lifecycle meanings:

```text
PROVISIONING -> READY -> CLAIMED -> BUSY -> SCRUBBING -> READY
                  |                                      |
                  +---------- idle expiry ---------------+
                                      |
                                  DRAINING -> RELEASED

Failures -> FAILED (not allocatable; cleanup/reconciliation still required)
```

`READY` covers both fresh and safely reused idle machines. A separate
`WARM_IDLE` state adds no allocation rule; expose idle age and ready counts.
Reserve the session assignment and pool budget before external provisioning;
never leave a newly created machine available to another acquire call.

- Enforce one live claim per session attempt and one claim per machine with
  database constraints and transactional updates. Use a generation to reject
  stale readiness/stop/cleanup results after reassignment.
- Reserve provisioning capacity atomically, including simultaneous Völundr
  processes. Use short database transactions; never hold them across provider
  network calls. Coordinate reconciliation with expiring operation ownership.
- Persist create intent before calling the provider. After an ambiguous timeout,
  find the same resource by idempotency key/tag before retrying. If a provider
  API cannot support safe discovery, unattended retry is a rollout blocker.
- Reconcile inventory and database state on startup and periodically: incomplete
  create, stopped during create, missing machine, expired idle resource, failed
  deletion, and managed orphan. Act only on resources positively owned by this
  installation. Repeated stop/delete must converge without double allocation.
- Report failure honestly. `FAILED` does not mean the VM was deleted; keep its
  resource ID and reserved budget until confirmed disposal. Never silently
  switch provider after a failure.
- End a claim only after runtime shutdown, durable-data preservation and
  credential cleanup are verified. Failed scrubs cannot return to `READY`.
- Stop/archive preserves the existing session data contract. Reset only the
  disposable VM disk and detached session mounts. For untrusted agent workloads,
  use provider reimage/reset to a trusted image before reuse; deleting selected
  directories alone is not proof that a root-capable agent left the VM clean.

## Ordered work packages

### 0. Establish the common contract and verify Harvester integration

Start with public Harvester documentation and a test allocation: auth, create/inspect/list/delete,
idempotency, resource tags, asynchronous operations, quotas/rate limits, image
selection, bootstrap, reset/reimage, network addressing and persistent volumes.
Choose the real bootstrap/control mechanism supported by that deployment.
Define the provider-neutral contract around the lifecycle requirements above,
and check that it does not assume Harvester/Kubernetes data structures. The
private integration does not require disclosure here and does not block this
work; its implementer can later supply the adapter and run the contract suite.

Document who can reach whom: Ting, upstream gateway, environment Völundr, VM, IDP,
credential service, Bifrost/model endpoints, Git, registry and telemetry. Pick
direct access to the environment Völundr origin or upstream-only gateway access.
No individual VM needs public ingress in either design.

Exit: a real create/inspect/delete proof, supported storage/reset contract,
runtime image, authentication design and endpoint map. Harvester live proof
requires deployment access and configuration; generic lease/backend work can
proceed without access to the proprietary API.

### 1. Implement durable allocation and claim ownership first

Add Völundr compute models, a repository port, a narrow machine-provider port,
and `ComputeLeaseService`. Provider operations cover create with stable request
identity, inspect/discover managed inventory, destroy, and the verified reset
capability. Runtime start/stop remains outside this infrastructure port.

Add an asyncpg repository and reversible migrations in both `migrations/` and
`charts/volundr/templates/migrations-configmap.yaml`. Implement claim/release,
budget reservation, generation checks and recoverable operation intent.
Do not enable a production VM backend backed by a test fake or partial adapter.

Exit: concurrent claims never share a VM or exceed budget; replayed operations
converge; stale updates cannot affect a new claim. Unit tests exercise the state
machine; CI PostgreSQL tests prove constraints/transactions and crash windows.

### 2. Implement the Harvester adapter and portable session bootstrap

Build the Harvester adapter against the documented API and the chosen bootstrap
mechanism. Supply Skuld and the selected supported runtime, immutable image
identity, workspace attachment, Git checkout, configured integrations and
short-lived workload credentials through existing identity ports where possible.
Define token renewal and cleanup for sessions longer than token lifetime.

Audit storage, secrets, workload identity, resources and Ravn-flock contributors
against the VM contract. `composition_builders._runtime_backend()` currently
uses adapter-name inference and defaults to Kubernetes; make VM backend identity
explicit through configuration/adapter metadata rather than another name test.
Reject unsupported mounts or features explicitly. Never silently omit them.
Support the selected workload end to end before claiming adapter parity. Keep
provider mapping and bootstrap delivery separate from shared lease policy so
EC2 and the private adapter can use the same backend later.

Exit: a real machine runs an authenticated session, reaches its dependencies,
persists work, shuts down, resets or disposes, and leaves no active session
credentials. Verify supported runtime/integration combinations, not just health.

### 3. Integrate `VmPodManager` with session lifecycle and recovery

Implement start/stop/status/readiness/capacity and deterministic public proxy
endpoints. Resolve internal targets from durable lease data, not a process-local
registry alone. The existing synchronous `session_proxy_target(session)` hook
cannot itself await a lease lookup: extend composition with an async resolver
backed by the repository, or hydrate a durable target projection deliberately.

Wire repository, lease service, provider and adapter at the composition root;
retain dynamic `adapter` plus kwargs configuration. Own reconciler startup,
cancellation and shutdown in application lifespan. Configure pool cap,
provisioning concurrency, API timeouts, operation deadlines and retry limits.

Handle stop/delete during create, failed readiness, restart during `STARTING`,
and a crash between provider success and database update. Existing reconciliation
is useful but insufficient: startup readiness recovery targets `PROVISIONING`,
and `_poll_readiness()` logs exceptions without persisting failure. Make lease
operations recoverable and ensure session status converges visibly.

Keep the preflight capacity check advisory; acquisition is authoritative.
Account for admitted/provisioning claims so a burst cannot overcommit. Preserve
`SessionCapacity` compatibility and expose separate ready, busy, provisioning,
scrubbing, draining and failed counts plus unreserved allocation budget. Clearly
distinguish immediately runnable slots from potential future provisioning.

Exit: ordinary Forge create/start/connect/stop/restart works; every injected
failure yields recoverable state, accurate capacity and no untracked VM.

### 4. Enable safe warm reuse and bounded pool maintenance

Use the lease foundation to acquire compatible `READY` allocations first,
reimage/reset after release, and expire idle allocations. Add configurable warm
minimum, idle maximum/TTL, total machine cap and provisioning concurrency.
Do not expire busy sessions with an idle timer. Background replenishment must
respect the same transactional cap as foreground launches.

Compatibility includes image/version, architecture, resource profile and the
agreed tenant/trust boundary. Preserve/re-attach durable session data independently
of machine reuse. A reset failure leaves a non-allocatable machine tracked for
disposal; a provider outage leaves visible failed/pending operations.

Exit: session A cannot expose files, tokens, processes or mounts to session B;
session A can still resume its retained data on another allocation. TTL and pool
limits work across restarts. This package is required for the intended initial
warm-pool rollout, not an optional later redesign.

### 5. Complete the selected routing topology

For direct environment-origin access, verify published chat/API URLs and service/user
authentication from actual clients. For upstream-only access, add general Forge
HTTP/WS routing through the registered owning Völundr and return upstream-facing
endpoints. Carry an explicit visible instance hint where practical; do not rely
on probing hundreds of targets on every connection. Keep resident and ordinary
Forge paths distinct and preserve tenant/ownership checks at both hops.

Trace Ting's direct target HTTP calls and all required Skuld runtime API calls,
not just browser chat. Include transcript/reconnect, health, tool approval and
any supported editor/terminal path. Do not assume the current `/s/.../api` proxy
is a generic editor reverse proxy.

Exit: the selected deployment works with VM addresses blocked from upstream;
if upstream-only topology is selected, it also works with the environment origin
blocked from browsers. Cross-tenant/session access is rejected.

### 6. Prove the workload and roll out

Expose lease transitions, pool counts, provisioning/reset latency, provider
errors, stuck operations and cleanup backlog through existing telemetry.
Carry session/allocation/provider operation IDs and trace context without secrets.
Document inspection, drain, failed-reset disposal and recovery procedures.

Run an incremental real-provider load test up to the agreed simultaneous-session
target and churn rate. Hundreds of agents in Flokks can have a different capacity
profile from hundreds of independent sessions. Measure startup percentiles,
ready-pool hit rate, provider throttling, database contention, gateway sockets,
credential/model limits and orphan count. Set acceptance thresholds from the
deployment quota and required user experience before the test.

Inject Völundr restarts, provider timeouts, lost workers, duplicate start/stop,
reset failure and gateway disconnects. Completion requires no duplicate claims,
bounded allocation count, no leaked credentials, preserved workspace data and
eventual verified cleanup. Stage with an explicit environment target/tag and a small
cap, then raise the cap after evidence. Rollback stops admission, drains claims
and disposes managed resources before disabling the backend.

## Deferred work

- EC2 implementation is future work; the provider extension boundary is part of
  the initial design. The proprietary adapter can be developed privately using
  that contract. GCE/Azure are outside the currently requested integrations.
- Global cost/capacity-aware placement and automatic cloud bursting. Preserve
  explicit target routing; provider failures do not authorize silent relocation.
- Moving existing Kubernetes pods under a VM-style lease pool.
- Sharing the lease service with Ravn `SpawnPort` or resident runtime controllers.
- Multiple concurrent sessions per VM and arbitrary multi-hop Guild scheduling.
- Provider-specific EC2 and private adapter implementations; pool/admin flows
  are already generic and do not require new provider branches.

## Verification performed

These existing tests passed against the current working tree, with no warnings
reported (224 tests total):

```sh
uv run --extra dev pytest -q tests/test_ting/test_volundr_factory.py tests/test_domain/test_session_capacity.py tests/test_niuu/test_session_proxy_routes.py tests/test_ravn/test_spawn_adapters.py tests/test_adapters/test_docker_container.py
# 69 passed
uv run --extra dev pytest -q tests/test_niuu/test_rest_volundr.py tests/test_niuu/test_rest_ravn.py tests/test_ting/test_services/test_dispatch_service.py
# 155 passed
```

This validates existing routing/lifecycle contracts with their test doubles. It
is not live proof of VM provisioning, federated connectivity, load capacity or
safe reset; those are acceptance gates above. The initial verification changed
only this plan; the target-selection follow-up is documented at the top.


## Warm-pool implementation (2026-09-16)

The shared pool service maintains unbound guests, assigns each once under a
PostgreSQL operation/admission lock, persists provisioning deadlines and retry
backoff, resumes stop/cleanup after restart, and quarantines unrecorded owned
infrastructure. Fresh allocations use separate credential-store namespaces,
so independent allocation writers do not contend on one file-backed owner record.
Warm preparation runs concurrently for independently owned allocations; database
policy bounds total machines, provisioning and spare count across controllers.

Admin Settings → Forge exposes pool policy, status and unused-machine disposal
through the existing mounted-settings schema and existing admin role gate. No
Harvester identifiers or API calls enter those shared flows. Providers using
native authentication can omit the optional HTTP auth adapter. Reset is complete
machine/root-disk replacement following workspace preservation, rather than
in-place disk sanitization. EC2 and private implementations remain future adapters.

Live warm assignment reached running in 6.2 seconds on the existing Harvester
guest after a controller restart. A real Codex turn wrote/read a file, independently
confirmed over pinned SSH. The regular Guild HTTP and WebSocket paths also work.
Four simultaneous additional launch requests competed for one available slot;
one ran, three failed admission, and allocated machines never exceeded two.
The live admin form saved pause/resume and paused admission returned HTTP 409.
The existing Ting HTTP adapter discovered Guild's target and claimed a spare.

Focused validation includes 114 compute/application/migration tests, 80 existing
credential/routing/Ting tests, 19 mounted-settings UI tests and three Playwright
checks (save, server error, loading/denied, keyboard activation). The focused
compute modules meet the 85% coverage gate. Load evidence is deliberately bounded
to two VMs; it does not certify hundreds of sessions or production OIDC/OpenBao.

The permanent local installation is under `~/.niuu/compute-controller`, with
PostgreSQL and the standard Niuu root application supervised by user LaunchAgents.
Persisted policy survived the move. The interrupted-stop test killed the controller
after its durable stop intent: restart preserved the workspace archive and removed
the guest. This exposed a session row stuck in `stopping`; reconciliation now
finishes interrupted stops for every runtime backend, with Kubernetes/VM/Docker
regression coverage. The same session subsequently resumed on a new allocation
and a real Codex turn read its preserved file using credentials renewed through
the existing Skuld/Spark adapter. A different Ting-created session could not see
the first session's marker.

Final validation: 144 focused compute/application/migration/lifecycle tests
(91.01% coverage), and the full web suite of 6,648 tests passed its
coverage gates; web build, typecheck and formatting also passed. Sanitized live
measurements are recorded in `evidence/harvester-warm-pool-2026-09-16.json`.
The local pool is left paused and drained pending replacement of the temporary
Harvester credential. The controller and admin UI remain available. Sustained
high-capacity load and production identity/OpenBao deployment are not certified
by this two-machine, local-identity proof.

Concurrent pool maintenance can hold the allocation lock during stop. The stop
path retries this contention within its configured cleanup timeout, without
repeating a successful workspace archive or reporting a false failure.
