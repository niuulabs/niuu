# Configuring a Valkyrie for Self-Improvement

How to configure a Valkyrie (ravn resident) so it can commission, verify,
install, and evolve its own tools through Ting/Forge build workflows. Written
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
  tool_build_adapter: ravn.adapters.tool_build.TingWorkflowToolBuildBackend
  tool_build_kwargs:
    base_url: https://yggdrasil.niuu.world
    # in-cluster (preferred): workload identity
    workload_token_file: /var/run/secrets/niuu-workload/token
    workload_exchange_url: https://yggdrasil.niuu.world/api/v1/tokens/workload/exchange
    workload_audiences: [volundr-api, forge, ting, mimir, guild]
    # off-cluster alternative: a PAT (full owner authority — see Security)
    # external_token_env: RAVN_VOLUNDR_PAT
  tool_builder_workflow:
    tags: [tool-builder]        # discovery by tag; or pin names: ["Tool & Skill Builder"]
```

Options:
- `A2AToolBuildBackend` — launches the build as an A2A workflow task against
  any agent card (Ting's facade or a foreign platform). Configuration shrinks
  to a card URL plus credentials; workflow discovery happens via the card's
  skills (see `docs/operator/a2a-workflow-tasks.md`):

  ```yaml
  resident_evolution:
    tool_build_adapter: ravn.adapters.tool_build.a2a.A2AToolBuildBackend
    tool_build_kwargs:
      card_url: https://yggdrasil.niuu.world/.well-known/agent-card.json
      workflow_selector: {tags: [tool-builder]}
      workload_token_file: /var/run/secrets/niuu-workload/token
      workload_exchange_url: https://yggdrasil.niuu.world/api/v1/tokens/workload/exchange
      workload_audiences: [volundr-api, forge, ting, mimir, guild]
  ```
- `TingWorkflowToolBuildBackend` — commissions a Ting workflow campaign over
  the bespoke Ting REST API (superseded by the A2A backend; scheduled for
  removal once the A2A path is validated in dev — NIU-1115).
- `ForgeSessionToolBuildBackend` — drives a single Forge session directly.
- Empty `tool_build_adapter` — the investigating agent writes tool code
  inline in-session (no external build; verification still runs).

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
before adopting a flock-shared tool.

### 5. Execution backend — WHERE installed tools run

```yaml
resident_evolution:
  learned_tool_execution_backend: container   # container | local | forge | devrunner
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
- `local`: explicit compatibility mode. It uses a scrubbed subprocess and
  per-tool venvs, but it is not a security boundary and does not enforce
  declared reach.
- `forge`/`devrunner`: legacy workspace-mounted persistent-container path.
  It scopes networking but exposes the workspace, so use `container` for
  autonomous generated code.

The `container` adapter currently requires a Docker-compatible daemon. If one
is unavailable (for example, inside a Kubernetes pod without an execution
service), learned-tool invocation fails loudly; it never falls back to local
execution. Configure `local` only as a conscious risk acceptance.

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

- `context_management.max_prompt_tokens` (0 = off) — hard per-call budget for
  the estimated prompt (system + tool schemas + history). When a turn still
  exceeds it after context compression, the call fails loudly with a
  per-section breakdown instead of overflowing the model window. Every turn
  also logs a `prompt_composition:` line with per-section token estimates.
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

## Security model — read this honestly

What each layer actually provides:

| Layer | Provides | Does NOT provide |
|---|---|---|
| Review/policy gate | Prevention: reach-gated adoption, blocked instructions rejected in any mode | Runtime containment |
| Independent verification | Correctness: the tool does what its tests say, in a clean env | Malice detection |
| Scoped build tokens | Blast radius: a leaked build token can only launch builds | Scoping for PATs (a PAT keeps full owner authority) |
| Env scrubbing | Hygiene: tools can't read tokens from `os.environ` (proven by test) | A wall — same-user file reads still work |
| `local` backend | Crash/timeout isolation | Network isolation |
| Docker backend | Container + network isolation where Docker exists | Anything in Kubernetes |
| Audit + rollback + capability ledger | Detection and recovery | Prevention |

There is **no hard runtime wall in-process**. Real runtime containment in
Kubernetes means pod-per-run execution (future runner adapter). Until then,
autonomy levels + reach gating + short-lived scoped credentials are the
security budget — set trust levels accordingly.

## Agent runbook: enable self-improvement end to end

1. **Configure** — add the `resident_evolution:` block (levers 1, 2, 5 above)
   to the Valkyrie's ravn config; restart the daemon.
2. **Diagnose the chain** — run `ravn tool-build doctor --json`. All five hops
   must be PASS (config → backend → auth → reachability → workflow
   discovery). Fix the first FAIL; later hops depend on it.
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

## Troubleshooting

| Symptom | Check |
|---|---|
| "no build backend configured" | Hop 1 of doctor: `tool_build_adapter` empty |
| 403 on workflow launch | Build token lacks `ting:workflow:launch` — auth kwargs wrong or exchange unreachable |
| Selector matches 0 or 2+ workflows | `ravn tool-build workflows` lists candidates; pin `names:` or fix tags |
| Realm configured but static config used | WARNING in daemon log: realm unreachable or no `build` grant — resolution degrades, never invents a grant |
| Tool with requirements refuses to run | No `venvs_dir` reachable — state dir not writable, or provisioning (uv/pip) failed loudly; see the run error |
| Everything lands in the operator inbox | Trust level ≤ 1 (guarded); raise the grant level to 2–3 for autonomous low-risk installs |
