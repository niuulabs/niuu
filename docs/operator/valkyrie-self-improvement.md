# Configuring a Valkyrie for Self-Improvement

How to configure a Valkyrie (ravn resident) so it can commission, verify,
install, and evolve its own tools through A2A or Forge build workflows. Written
to be executable by an agent: every step has an action and a validation. Run
the validations; do not assume.

## Mental model (30 seconds)

A Valkyrie hits a capability gap → `build_tool` commissions a **Ting workflow**
(which spawns Forge sessions) → the workflow returns
`{manifest, tool_code, test_code, requirements}` in a canonical
`learned_tool.json` **artifact envelope** → ravn extracts the Python and tests,
then **independently verifies** them in a throwaway venv (bounded repair loop
on failure) → the **policy court** gates it on declared reach + autonomy →
install materializes executable Python separately from the envelope → the tool
becomes callable, its usage is tracked, three consecutive failures auto-roll
it back (restoring the previous version when one exists) → adoption/rollback
is mirrored to the realm's **capability ledger**.

Who is allowed to do what is governed by the Valkyrie's **realm**: an
append-only ledger of **trust grants**. The `build` grant decides which
workflow it may commission and at what autonomy level. **The most recent
`build` grant wins** — granting a lower level later genuinely demotes.

## Prerequisites

| # | Requirement | Validate with |
|---|---|---|
| 1 | Volundr/Ting reachable from the Valkyrie | `curl -sf {BASE_URL}/api/v1/forge/health` |
| 2 | Migration `000053` applied (realms, trust_grants, capabilities) | `curl -sf -H "Authorization: Bearer $TOKEN" {BASE_URL}/api/v1/realms` returns 200 (not 404) |
| 3 | A Ting workflow tagged `tool-builder` exists (the seeded "Tool & Skill Builder") | `ravn tool-build workflows --json` lists it |
| 4 | Credentials: in-cluster → projected workload token; off-cluster → PAT in an env var | Hop 3 of `ravn tool-build doctor` |

## The levers

All Valkyrie-side levers live in `ResidentEvolutionConfig`
(`resident_evolution:` in the ravn config YAML).

### 1. Build backend — HOW tools get built

```yaml
resident_evolution:
  tool_build_adapter: ravn.adapters.tool_build.a2a.A2AToolBuildBackend
  tool_build_kwargs:
    card_url: https://yggdrasil.niuu.world/.well-known/agent-card.json
    workflow_selector: {tags: [tool-builder]}
    # in-cluster (preferred): workload identity
    workload_token_file: /var/run/secrets/niuu-workload/token
    workload_exchange_url: https://yggdrasil.niuu.world/api/v1/tokens/workload/exchange
    workload_audiences: [volundr-api, forge, ting, mimir, guild]
    # off-cluster alternative: a PAT (full owner authority — see Security)
    # external_token_env: RAVN_VOLUNDR_PAT
```

Options:
- `A2AToolBuildBackend` (shown above) — launches the build as an A2A workflow
  task against any agent card (Ting's facade or a foreign platform).
  Configuration is a card URL plus credentials; workflow discovery happens
  via the card's skills (see `docs/operator/a2a-workflow-tasks.md`).
  The resident must be reachable by push: builder questions arrive after the
  commissioning turn ends, so the daemon refuses to start unless
  `gateway.platform.a2a_push_callback_url` is set and
  `gateway.channels.http` has `enabled` and `a2a_push_enabled` (with
  `a2a_push_auth` verifying Ting's workload token). The callback origin must
  also be in Ting's `a2a.push_callback_allowed_origins`.
- `ForgeSessionToolBuildBackend` — drives a single Forge session directly; it
  remains the non-A2A path while the A2A deployment is being validated.
- Empty `tool_build_adapter` — the investigating agent writes tool code
  inline in-session (no external build; verification still runs).

Do not remove an existing build path based on a configuration or doctor result
alone. Retain it until a resident has completed a recorded A2A commission,
verification, court decision, and installation in the target environment.

The backend automatically requests a least-privilege build token scoped to
exactly its launch endpoint (`ting:workflow:launch` or
`forge:session:create`).

### 2. Realm governance — WHO decides, per Valkyrie

```yaml
resident_evolution:
  realm_slug: noatun            # this Valkyrie's realm; empty = static config only
  autonomy_mode: guarded        # fallback when realm is unreachable / has no grant
  # realm_api_base_url / realm_api_kwargs: only when the realm API is not at
  # tool_build_kwargs.base_url with the same auth
```

With `realm_slug` set, the effective **workflow** and **autonomy mode** come
from the realm's most recent `build` trust grant:
- `grant.limits.workflow` → which Ting workflow may be commissioned
- `grant.level` → autonomy mode via the trust table (below)

Create the realm + grant via the UI (**/valkyrie/realms → Tool Builder card**)
or the API:

```bash
curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  {BASE_URL}/api/v1/realms -d '{"slug": "noatun", "name": "Noatun"}'
curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  {BASE_URL}/api/v1/realms/noatun/trust-grants \
  -d '{"action_class": "build", "target": "*", "level": 2,
       "limits": {"workflow": "Tool & Skill Builder"}, "granted_by": "human:operator"}'
```

To **demote**, post a new grant with a lower level — latest grant wins.

### 3. Autonomy ladder — how much may happen without a human

| Trust level | Mode | Meaning |
|---|---|---|
| 0–1 | `guarded` | Every install is held for operator review |
| 2–3 | `autonomous` | Low-risk (read-reach) tools install automatically; mutating reach still needs review |
| 4–5 | `yolo` | Low/medium risk installs automatically within delegated boundaries |

Thresholds are config (`trust_level_autonomy_table: {autonomous: 2, yolo: 4}`).
Hard-gated boundaries (credentials, spending, destructive, external_send,
authority expansion) **always** require explicit delegation regardless of mode.

### 4. Verification & repair — correctness gates

```yaml
resident_evolution:
  build_repair_attempts: 3      # verify→repair rounds before a build fails loudly
```

Every built tool is re-verified on the Valkyrie's side (its `test_code` runs
in a fresh venv with its `requirements`). Failures trigger deterministic
dependency healing, then re-commission with the failure log; artifacts without
tests pass structure-only (weaker signal, not a rejection). Peers re-run tests
before adopting a flock-shared tool — always in the SAME execution boundary
the tool would later run in, never on the resident's own host. `container`,
`local`, and `k8s_job` all implement this `verify()` re-check; `forge`/
`devrunner` do not, so a peer proposal on those backends is declined outright
rather than silently verified with the resident's own network/filesystem
access.

### 5. Execution backend — WHERE installed tools run

```yaml
resident_evolution:
  learned_tool_execution_backend: container   # container | local | forge | devrunner | k8s_job
```

- `container` (default): one fresh OCI container per invocation. The runtime
  is read-only, capability-free, resource- and time-bounded, and receives no
  workspace, network, credential, or host path unless the reviewed manifest
  grants it. Filesystem grants mount only the exact existing target. Credential
  grants pass only the named environment variable. The default image is pinned
  by digest and runs with `--pull=never`; preload it on the execution host.

  ```bash
  docker pull ghcr.io/niuulabs/devrunner@sha256:ec7a32ffd8ca1f3ddb8bd4983198988538ab74804201ce45e14e56241adfc518
  ```
- Network containment fails closed. No grant means `--network=none`.
  Target-specific or read-only/write-only network grants are rejected because
  an ordinary Docker bridge cannot enforce their target or operation. A tool
  may request explicit broad `network/read_write` reach, which is treated as
  mutating by the normal autonomy/review policy.
- `local`: a scrubbed subprocess of the resident with per-tool venvs. It
  enforces one reach boundary — the network. A tool whose declared reach is
  entirely local runs with every network socket denied: a fresh user +
  network namespace on Linux, a no-network Seatbelt profile on macOS. Local
  kinds are `pure_compute`/`compute`/`none`, `file`/`filesystem`/`path`/
  `workspace`, `credential`/`secret`, and `native_tool`/`host_tool`/`host_sdk`
  (the host SDK channel keeps working). **Every other kind is network reach**
  — `network`, `http`, `kubernetes`, `k8s_api`, `prometheus`, or a free-text
  description — because it names something off the machine. Where the host
  cannot isolate (Linux with unprivileged user namespaces disabled by sysctl,
  seccomp, or Ubuntu's `kernel.apparmor_restrict_unprivileged_userns=1`), a
  no-network tool is refused when it is loaded and again when it is run, with
  the remedy in the error. It is never run with the network. What `local` does
  not narrow: a networked tool gets the resident's whole network (a grant's
  `target` is not an egress filter, and its Kubernetes reach is whatever the
  pod's service account RBAC allows), and every tool reads the filesystem as
  the resident's user.
- `forge`/`devrunner`: legacy workspace-mounted persistent-container path.
  It scopes networking but exposes the workspace, so use `container` for
  autonomous generated code.
- `k8s_job`: the production Kubernetes backend — one locked-down Job per
  invocation, with no Docker daemon involved. Set it via
  `charts/agent`'s `learnedToolRunner.enabled: true`, which also installs
  the verified deny/allow NetworkPolicies and RBAC the runner checks live
  before every Job. Requires explicit `learned_tool_k8s.namespace`,
  `deny_policy_name`, and `allow_policy_name`; the runner refuses to start
  without them (no silent unenforced fallback). Jobs run digest-pinned,
  read-only-root, non-root/seccomp/capability-restricted. Requirements are
  never installed: a gate inside the Job checks them against the pinned image
  (the devrunner image bakes in `kubernetes`, `requests` and `PyYAML`; no
  `kubectl`, so a tool that only shells out to it fails at run time) and refuses the run, naming what is missing, otherwise.

  Each run's egress is derived from the tool's declared reach
  (`ravn.valkyrie_evolution.reach_plan`):

  | Declared reach | Egress the Job gets |
  |---|---|
  | none, or only `pure_compute`/`process` | none (deny policy) |
  | Kubernetes API (`kubernetes`, `k8s*`, `kubernetes.default.svc`, the SA token path) | the API server's endpoints, plus the read-only `tool_service_account` token |
  | `namespace/service[:port]`, `svc.ns.svc[:port]`, `http://svc.ns.svc:port` | the pods behind that Service, on its target port |
  | `https://host`, `host:port`, `ip:port` | those addresses (host names resolved at run time) |
  | a word declared in `named_targets` (e.g. `prometheus`) | the Service the operator mapped it to |
  | untargeted `network` with `read_write` | unrestricted egress (allow policy) |

  A targeted run keeps the denied label, so the verified deny policy isolates
  its pod from the moment it starts; a per-run NetworkPolicy owned by the Job
  adds exactly the egress above plus cluster DNS (`dns_service`). Anything the
  planner cannot express is refused with the remedy: an untargeted `read`
  grant, a wildcard host, prose with no address and no declared word, a
  filesystem path other than the service-account token, credentials, host
  SDK calls, or Kubernetes access beyond `read`. The tool's own timeout
  counts from container start; `job_pod_start_timeout_seconds` covers
  scheduling and image pull.

  ```yaml
  learned_tool_k8s:
    namespace: nats
    image: ghcr.io/niuulabs/devrunner@sha256:<reviewed digest>
    deny_policy_name: learned-tool-deny
    allow_policy_name: learned-tool-allow
    tool_service_account: <resident>-tools   # bound to read-only RBAC only
    dns_service: kube-system/rke2-coredns-rke2-coredns
    named_targets:
      prometheus: monitoring/kube-prom-kube-prometheus-prometheus:9090
  ```

  The resident's own service account needs, in that namespace: create/get/
  list/watch/delete `jobs`; create/delete `secrets` and `networkpolicies`;
  get/list `networkpolicies`, `pods`, `pods/log`; and get `services` (any
  target namespace) and `endpoints` (`default/kubernetes`).

  Peer-adoption re-verification (`verify()`) is supported on `k8s_job`: a
  peer's test suite runs in the SAME denied-network, NetworkPolicy-verified
  Job shape `run()` uses. A peer proposal that declares requirements is
  declined outright — same posture as `run()` — rather than spun up in a
  throwaway Job with real egress to install packages a `k8s_job` invocation
  could never actually use anyway.

  Two budgets, deliberately decoupled: `job_pod_start_timeout_seconds`
  bounds scheduling and image pull, and the verify timeout only starts
  counting once the container's `state.running.startedAt` is observed — so
  a slow pull never eats into the test's own run time (a 100s pull ahead of
  a 30s test still gets the test its full 30s). `activeDeadlineSeconds` on
  the Job covers both budgets combined, so Kubernetes itself never kills
  the Job before either one has had its say. A container that never leaves
  scheduling/image-pull within its own pod-start budget is infrastructure;
  a container that started and then ran past the test timeout is a durable
  failed verification, not infrastructure — retrying it on every
  redelivery would never converge. An eviction is classified the same way:
  one caused by the pod's own resource usage (its ephemeral-storage or
  emptyDir volume, or memory, exceeding its limit) is a failed
  verification; only a cluster-driven eviction (preemption, node drain, the
  taint-manager) is infrastructure. A kubelet admission refusal
  (`OutOfcpu`, `NodeAffinity`) is diagnosed as infrastructure too — the
  container never actually ran, regardless of the pod's reported phase.

  The live NetworkPolicy check additionally enumerates every OTHER policy
  in the namespace (not only the two verified by name) for a
  denied-network Job — NetworkPolicies are additive, so an unrelated
  namespace-wide policy (e.g. "allow DNS for everyone") could otherwise
  silently grant denied pods egress the deny policy appeared to forbid.
  This needs the `list` RBAC verb on `networkpolicies` (the chart grants
  it) and only runs for a denied-network invocation — an allowed-network
  tool has nothing for it to protect, so it is skipped there. **Upgrade
  note:** before enabling `k8s_job` (or upgrading into this check), run
  `kubectl get netpol -n <resident-namespace>` and confirm no other policy
  in the namespace selects the `niuu.world/tool-network: denied` label with
  an egress rule — otherwise `verify()`/`run()` for denied-network tools
  will refuse to start. Cluster-level policy layers (Kubernetes
  `AdminNetworkPolicy`, Cilium `CiliumNetworkPolicy`/`CiliumClusterwideNetworkPolicy`,
  Calico `GlobalNetworkPolicy`) are NOT enumerated by this check — only
  namespaced `networking.k8s.io/v1` NetworkPolicies. An operator relying on
  one of those layers must verify its egress posture for denied pods
  separately.

The `container` adapter currently requires a Docker-compatible daemon. If one
is unavailable (for example, inside a Kubernetes pod without an execution
service), learned-tool invocation fails loudly; it never falls back to local
execution. Configure `local` only as a conscious risk acceptance. Use
`k8s_job` for Kubernetes deployments instead.

### 5b. Injection mode — HOW installed tools reach the prompt

```yaml
resident_evolution:
  learned_tool_injection_mode: dispatch   # dispatch (default) | bulk
```

- `dispatch` (default): learned tools are NOT preloaded into the tool schema
  of every LLM call. The resident discovers them with `capability_list`
  (entries tagged `learned`) and executes them by name through the single
  `learned_tool_run` tool — the same retrieval-on-demand model markdown
  skills already use. Per-turn prompt size stays independent of how many
  tools the resident has accumulated (NIU-1118).
- `bulk`: legacy behavior — every persisted artifact is loaded as a native
  callable tool on every turn, and every manifest description lands in every
  request's tool schema. With ~50 accumulated tools this alone consumed a
  large share of a 131k context window; keep it only as a temporary
  escape hatch.

Related bounds (all NIU-1118):

- `context_management.context_window_tokens` (0 = unknown) — the model's real
  context window. Ravn subtracts the configured maximum output tokens before
  deriving the safe prompt ceiling; it never infers this value from a model
  name.
- `context_management.max_prompt_tokens` (0 = off) — hard per-call budget for
  the conservative estimated prompt (system + tool schemas + complete nested
  tool/history payloads). The lower of this value and
  `context_window_tokens - llm.max_tokens` wins. When a turn still
  exceeds it after context compression, the call fails loudly with a
  per-section breakdown instead of overflowing the model window. Every turn
  also records a `ravn.prompt.budget` trace event and exposes the current
  estimate, ceiling, output reservation, and compaction state in the resident
  HUD.
- `context_management.token_estimate_safety_factor` (default 1.5) — conservative
  multiplier for providers that do not expose an exact preflight token count.
- A human-directed task carries the current human message and any prior room
  message it replies to whole. `resident_state.directed_message_context_max_chars`
  (default 4000) was removed: the prompt is bounded by
  `context_management.max_prompt_tokens` and compaction, and a config that
  still sets the key is refused at load.
- `tools.max_result_chars` (default 100000, 0 = off) — caps one tool result's
  contribution to history. Compression protects the most recent messages, so
  a single giant result (observed: a 229MB mimir result) would otherwise make
  the turn unrecoverable; oversized results are truncated with an explicit
  marker telling the model to narrow the query.
- `resident_inbox.signal_retention_max_pages` / `signal_retention_max_age_days`
  (defaults 500 / 7d, 0 = off) — rolling retention for
  `resident/inbox/signals` Mimir pages, swept off the signal write path.
  Signal pages are write-only operational records; without retention they
  accumulate without bound (observed: 104k pages / 440MB) and poison mimir
  search over the wiki.

### 6. Lifecycle knobs

```yaml
resident_evolution:
  rollback_consecutive_failures: 3    # auto-rollback threshold
  self_registered_tool_confidence: 0.74
  tool_timeout_seconds: 10.0
  review_attention_tiers: [present, urgent]        # judgment→inbox gates
  observational_actions: ["", none, n/a, watch, observe]
```

Rollback archives the tool, prunes its venv, restores the superseded version
when one exists (through the same review gate), and reopens the capability gap.

### 7. Repair, usage evidence and retirement

A resident keeps **one tool per capability**, using the mechanisms that were
already there: `find_installed_capability`, the `capability_id` version chain
(`source_gap_id`), the `_archive` directory, and the lifecycle registry
(`skill_management.json`).

- **Repair over variant.** Before anything is commissioned, `build_tool` looks
  for the tool that already serves the request: the declared `capability_id`,
  the exact name, the same name in another spelling, and — when
  `reflection.matching.mode: semantic` — the same capability in other words
  (the lesson embedding shortlist and judge, asked "is this request the next
  version of that tool"). Retired tools are candidates too. A healthy match
  answers `already_installed`. A failing, unverified or rolled-back match, or
  `replace: true`, is rebuilt **as that tool's next version**: same name,
  `version` + 1, `supersedes` the previous artifact, `provenance.repair_of`
  recording what was asked. A build that fails verification is kept under
  `learned_tool_artifacts/_failed/` and never replaces the installed envelope.
- **Supersession retires.** When an installed version supersedes a
  differently named tool, the predecessor is archived and uninstalled instead
  of staying beside it.
- **Usage evidence** is the lifecycle record: runs, successes, failures, last
  use, and now `first_used_at`. `capability_list` shows it per tool; the
  resident health snapshot carries `learned_tools_installed`,
  `learned_tools_never_used`, `learned_tools_retired`,
  `learned_tools_retired.<reason>` and `learned_tool_restores_total`.
- **Capability backfill.** Each consolidation dream compares every artifact
  whose capability was never decided by matching with the others, gives it
  the `capability_id` of the tool it duplicates, and records the decision on
  the envelope (`provenance.capability_matched`), so nothing is judged twice.
- **Retirement.** The same dream then retires, in this order: tools
  **superseded** by another runnable tool with the same capability (the
  best-evidenced one is kept), **debug leftovers** (name starts with one of
  `tool_curation.leftover_name_prefixes` and never succeeded), **unrunnable**
  tools (no passing verification, rolled back, or refused by the execution
  backend, see below), and **unused** tools —
  never run, and installed longer than the longest install-to-first-use wait
  known on this resident (observed first uses, or the created-to-last-use
  bound of tools used before `first_used_at` existed). Retirement moves the
  envelope into `_archive`, removes the code, and archives the lifecycle
  record with `archived_reason` / `archived_detail` / `superseded_by`. A
  `capability_list` query that matches a retired tool says so; asking
  `build_tool` for its capability restores it. A peer's copy of a tool retired
  here as superseded or a debug leftover is ignored, and the next dream
  restores an unused retirement the widened window no longer supports.
- **Tools the backend cannot load.** `capability_list`, the published learned
  tool inventory and the bulk loader offer only what the configured
  `learned_tool_execution_backend` can load, using the loader's own check: the
  tool is verified, its declared reach is enforceable there, and it does not
  import the host SDK (`ravn.sdk`) on a backend with no channel back to the
  resident (`container`, `k8s_job`, `forge`). A tool that fails that check is
  not dropped silently: the first time the process sees it, it is logged at
  error level and counted as `degraded.learned_tool.refused`; the health
  snapshot carries `learned_tools_refused` (a standing count that falls to zero
  once each is repaired); `capability_list` names it under
  `unavailable_learned_tools` with the reason; and `learned_tool_run` answers
  with the `build_tool` repair rather than "use capability_list". The dream
  retires it as `unrunnable` (it is never counted as kept; a pinned or
  unmanaged one is listed under `refused` in the dream's `learned_tools`
  summary), and `build_tool` for the same capability restores it as a repair
  with the refusal as the stated problem. On such a backend `build_tool`'s
  description tells the builder not to use the host SDK, and a build that
  would still be refused is rejected before it replaces the working version.

```yaml
resident_evolution:
  tool_curation:
    enabled: true                       # needs resident_wakefulness
    leftover_name_prefixes: [test, debug, tmp, temp, scratch]
```

`ravn learned-tools-plan --config <file>` prints what the next dream would
backfill and retire, and why, without changing anything.

## Security model — read this honestly

What each layer actually provides:

| Layer | Provides | Does NOT provide |
|---|---|---|
| Review/policy gate | Prevention: reach-gated adoption, blocked instructions rejected in any mode | Runtime containment |
| Independent verification | Correctness: the tool does what its tests say, in a clean env | Malice detection |
| Scoped build tokens | Blast radius: a leaked build token can only launch builds | Scoping for PATs (a PAT keeps full owner authority) |
| Env scrubbing | Hygiene: tools can't read tokens from `os.environ` (proven by test) | A wall — same-user file reads still work |
| `local` backend | Crash/timeout isolation; no network for a tool that declares none (refused where the host cannot isolate) | Per-target egress filtering, filesystem isolation |
| Docker backend (`container`) | Container + network isolation where Docker exists | Anything in Kubernetes |
| `k8s_job` backend | Pod-per-run Kubernetes containment: per-run egress derived from declared reach (API server, named Services, named addresses), a read-only tool identity only for Kubernetes-API reach, non-root/seccomp/dropped capabilities, resource/time/output bounds — for both invocation and `verify()` | A Docker daemon (none needed); runtime dependency installation (bake into the pinned image); the HTTP verb on a non-Kubernetes target (the policy fixes *where*, not *what*) |
| Audit + rollback + capability ledger | Detection and recovery | Prevention |

There is **no hard runtime wall in-process**. Real runtime containment in
Kubernetes means pod-per-run execution via the `k8s_job` backend — set it in
a Kubernetes deployment rather than accepting `local`'s weaker posture.
Autonomy levels + reach gating + short-lived scoped credentials remain the
security budget on every backend — set trust levels accordingly.

## Agent runbook: enable self-improvement end to end

1. **Configure** — add the `resident_evolution:` block (levers 1, 2, 5 above)
   to the Valkyrie's ravn config; restart the daemon.
2. **Diagnose the chain** — run `ravn tool-build doctor --json`. For the A2A
   backend, all six hops must be PASS (config → backend → auth → agent-card
   fetch → workflow discovery → JSON-RPC endpoint). The endpoint probe sends
   only `GetTask` for a reserved nonexistent id; it never commissions a build.
   Fix the first FAIL; later hops depend on it.
3. **Seed governance** — create the realm and a `build` grant (lever 2).
   Validate: `curl {BASE_URL}/api/v1/realms/{slug}/trust-grants` shows it, and
   `ravn tool-build workflows` marks the granted workflow with `*`.
4. **Exercise the loop** — from an investigation session, call `build_tool`
   with a `build_request` (no inline `tool_code`). Expected: a Ting campaign
   runs; the result verifies; in `guarded` mode an install review appears in
   the operator inbox, in `autonomous` mode a read-reach tool installs
   directly.
5. **Validate adoption** — the tool is registered in the building session's
   toolbox, appears in `capability_list` (tagged `learned`, runnable via
   `learned_tool_run` in every later session), and shows in the realm ledger:
   `curl {BASE_URL}/api/v1/realms/{slug}/capabilities` with `status: present`.
6. **Validate rollback** — after `rollback_consecutive_failures` real
   failures, the skill archives, the capability flips to `status: gap`, and a
   `rebuild` judgment reaches the inbox. (Do not force this in production;
   assert the wiring via the test suite instead:
   `uv run --extra dev pytest tests/test_ravn/test_learning_ledger.py -q`.)

### Dev Kubernetes A2A proof record

`ravn tool-build doctor` is non-commissioning. Its A2A endpoint check sends
only a `GetTask` probe for a reserved nonexistent id, so a green result proves
only the configured diagnostic hops; it does not commission a tool or prove
its verification, court decision, installation, or ledger update. For an approved
dev Kubernetes proof, change the resident configuration only through the
repository's GitOps `infra_change` workflow and let Fleet reconcile it. Do not
mutate Fleet-managed resources directly, and keep credentials in
Doppler/ExternalSecret rather than Git.

Before recording the proof as complete, retain the deployed configuration and
owner evidence identifying the resident and cluster, plus the commission/task
id, verification result, court verdict, installation id or learned-tool
version, and capability-ledger entry. A bounded submission endpoint or scoped
workload identity must be available for the one approved capability-gap
request; push registration alone is not evidence that a submission succeeded.

## Troubleshooting

| Symptom | Check |
|---|---|
| "no build backend configured" | Hop 1 of doctor: `tool_build_adapter` empty |
| 403 on workflow launch | Build token lacks `ting:workflow:launch` — auth kwargs wrong or exchange unreachable |
| Selector matches 0 or 2+ workflows | `ravn tool-build workflows` lists candidates; pin `names:` or fix tags |
| Realm configured but static config used | WARNING in daemon log: realm unreachable or no `build` grant — resolution degrades, never invents a grant |
| Tool with requirements refuses to run | No `venvs_dir` reachable — state dir not writable, or provisioning (uv/pip) failed loudly; see the run error |
| Everything lands in the operator inbox | Trust level ≤ 1 (guarded); raise the grant level to 2–3 for autonomous low-risk installs |
