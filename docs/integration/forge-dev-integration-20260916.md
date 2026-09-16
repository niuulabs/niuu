# Forge integration — 16 September 2026

Source: `xteo/forge/dev-integration` at `45ea08f69bca5fb8c63ed2ef4d98bcf707aa2029`.
Target base: `niuulabs/dev` at `242603e00ed55cdd2d916d903c9bc1a154b98b70`.
Integration branch: `codex/forge-integration-20260916`.

This is a full branch merge. Source history has 201 commits absent by identity from
target (194 non-merge, seven merges), including foundation work previously squash-merged
into dev. The net changes were reconciled; those historical identities were not treated
as 201 missing features. The original dirty dev checkout was left untouched.

The [full source assessment](forge-source-assessment-20260916.md) includes the original
200-commit ledger. A final upstream refresh adds `45ea08f69`: single-key tmux
controls now survive the real broker boundary, with nine regression cases.

## Included work

- Claude tmux: streaming text between tools, prompt consumption and steering correlation,
  native questions/approvals, trust-dialog handling, teammate lifecycle and delivery recovery.
- Codex: native resume/history hydration, durable identity, nested-agent events, runtime
  options, model and effort controls, process cleanup and bounded transport messages.
- Grok: ACP lifecycle, native history/replay, steering and protocol recovery refinements.
- Muse: MSP runtime, streaming tools, questions/approvals, steering, usage and native resume.
- PI: native RPC transport, model controls, questions, steering receipts and native resume.
- Shared delivery: durable claims, idempotency/conflict handling, prompt acceptance versus
  actual consumption, reconnect/retry and bounded history-v2 paging.
- Shared transcript/UI: canonical message identity, ordered text/tool segments, generation
  state, native history hydration, stable rendering, timeline and elapsed-turn timestamps.
- Projects: discovery, coordination, persisted briefing snapshots, API/UI and dispatch.
- Runtime lifecycle: process ownership and preservation during API restarts, resume and
  archive/history behavior integrated with current runtime managers.
- Ravn/OpenClaw: gateway/handshake work, room and collaboration behavior, memory recall
  query and input budgeting, reranking and associated regression corpus.
- Verification: native runtime probes, database/stdio/tmux fault tests, recorded replay
  fixtures, stability workflows and operator guidance.

## Reconciliation decisions

Preserved upstream authentication and tenant authority, concurrent owning-instance lookup,
model-gateway credentials, compute capacity/VM leases, compact/simplified chat preferences,
and dependency security pins. Explicit instance selection now narrows ownership probes.
Meta provider credentials now use the existing integration/secret pipeline (`META_API_KEY`),
so the Muse engine is selectable outside a manually configured mini host. Validation
logs omit request bodies and replay routes are registered once.

Preserved all existing numbered SQL bytes. Incoming overlapping migrations were assigned:

| New version | Change | Source version |
|---|---|---|
| 69 | Session turn start timestamp | 62 |
| 70 | Message delivery claims | 63 |
| 71 | Remove duplicate event-log index | 64 |
| 72 | Startup schema history | 65 |
| 73 | Forge Projects | 66 |

Root SQL, packaged CLI SQL and Helm SQL agree. Checksum aliases retain source identities.
The numbered migration bridge repairs skipped prerequisites for overlapping histories,
including upstream authority/admin/compute tables, without resetting a numeric cursor.

Configured embedding and reranking errors propagate. The incoming catch-and-continue
paths were removed. Ollama discovers the configured model's context budget; servers
without metadata must supply `max_input_chars` explicitly.

## Runtime portability

| Mode | Integration |
|---|---|
| Mini/process | Shared session environment, native process/replay ownership, host CLI installation |
| Docker | Shared broker settings and reasoning effort; explicit Docker backend identity; locked CLI image tools |
| OpenShell | Shared settings with sandbox credential/network isolation; explicit backend identity; matching CLI image tools |
| Kubernetes | Helm broker/history/PI/effort settings and Projects config; synchronized migration bundle |
| VM | Shared settings passed into guest bootstrap; VM backend identity; current capacity/lease/controller behavior retained |

The shared environment carries history limits, hydration settings, effort controls and
PI/Muse executable configuration. Regression tests exercise actual Docker launch settings,
OpenShell sandbox environment, SSH VM bootstrap and rendered Kubernetes config.
Project briefings append to the resolved persona prompt and survive session restart.

`GitProjectWorkspace` reads the checkout on the API host. Container/cluster deployments
must mount that checkout into the API and configure allowed prefixes. Runtime guests get
the persisted briefing through their prompt; API filesystem paths are not assumed to
exist inside guests.

Skuld, OpenShell and devrunner images now include locked PI `0.85.1` and checksum-pinned Muse
`1.3.0-R3233.1` for Linux amd64/arm64. Docker/Kubernetes/SSH VMs use the Skuld image;
OpenShell-backed sessions use the OpenShell image. Rebuild images before using these
runtimes. Mini hosts install CLIs separately. PI/Muse credentials remain runtime-local;
configure credentials in the session environment/home through the deployment's existing
secret and mount mechanisms. Host credentials are not copied into arbitrary containers.

Muse MSP has no system-prompt field. Sessions supplying system prompts (including Project
briefings) now fail explicitly; use a transport supporting system prompts for those
sessions, or configure Muse workspace instructions in `AGENTS.md`. Failed Muse resume
propagates instead of creating a different native session.

## Validation

- Web: 488 test files, 6,690 tests passed. Coverage: 93.01% statements,
  85.52% branches, 92.20% functions, 94.54% lines. Typecheck, production build,
  ESLint and Prettier checks passed.
- PostgreSQL: 17 real-database checks passed against a disposable PostgreSQL 17
  instance, including both migration lineages, numeric versions 62–66, retry,
  row preservation, rollback, checksum drift and delivery contention.
- Runtime adapters: 161 targeted tests passed; five environment-dependent checks
  skipped. Both chart lint checks and 78 Skuld chart tests passed; rendered settings
  were loaded through Skuld's configuration model.
- Linux release guard: all 28 tests passed in a disposable Linux container. Its two
  real `/proc` tests are explicitly Linux-only in the macOS suite.
- Full Skuld, OpenShell and devrunner production Dockerfiles built successfully for Linux arm64,
  including all advertised CLI executable checks and Skuld's observability check.
  Native PI RPC startup and Muse startup/resume passed as the normal non-root user
  in the rebuilt Skuld and OpenShell images. The builds include the locked PI dependency graph and
  checksum-verified Muse executable.
- Ruff lint/format, lockfile consistency and Git whitespace checks passed.

The full backend run passed 21,173 tests, with 77 skips, 186 deselections and one
expected failure. Its single unexpected failure was the Skuld/devrunner CLI-pin
alignment check; devrunner was then updated to the same locked tools. The final
application/configuration/packaging rerun passed all 102 tests without warnings.
Backend coverage is 86.82%, above the unchanged 85% gate. Coverage for the application
factory was refreshed after the final validation-log and duplicate-route corrections.

Reproduce the backend gate with `uv sync --extra dev --extra k8s --extra otel`, then
`uv run pytest tests --cov=src --cov-fail-under=85`. The web gate is `pnpm test` in
`web-next`. The native PI probe is `scripts/forge_pi_probe.py --isolated`; it reports
RPC startup separately from authenticated agent execution and replay.

Live authenticated model turns and full deployment lifecycle acceptance on an OpenShell
gateway, Kubernetes cluster and VM provider have not been run. Adapter tests and Helm
renders are evidence of wiring, not proof of a deployed end-to-end model turn. The native
PI probe covered RPC startup without credentials; the native Muse check covered startup
and resume without a model turn. All three production images were rebuilt locally; they have not been published or deployed.
Linux amd64 artifacts are pinned but were not built on this arm64 host.


## Release gate corrections

CI now uses the declared Python 3.12 minimum. Stability fixtures provide a completed
setup state and select Advanced mode; the database-free restart fixture also replaces
development identity seeding. All five browser cases and four Linux API restart cases
passed locally; the complete Forge Stability workflow then passed in CI.

Preview cache directory names now hash session identifiers as well as tool identifiers,
so filesystem containment does not rely on REST validation. Recovery logs sanitize
identifiers and coerce numeric fields. All 59 focused cache/archive/conversation tests
passed, including traversal inputs.
