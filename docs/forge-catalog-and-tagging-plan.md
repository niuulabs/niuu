# Forge-independent catalog + instance tagging — plan

Branch: `feat/guild-minimal-backend-labels` (PR → `dev`). Scope: **volundr repo only** (no
infrastructure-repo changes). Cross-cutting: run tooling via Docker `uv`; conventional
commits; ≥85% coverage, zero pytest warnings; no magic numbers (defaults → `config.py`);
migrations dual-located.

## Why

In the central entrypoint (ymir / `yggdrasil.niuu.world`) the Forge API is scaled to zero —
the web UI, guild registry, and shared services run there, but the real Forges live in other
environments (valhalla, noatun, …) and are registered in the guild by URL. The UI's Forge
"basics" (session-definitions, profiles, templates) currently can only be served by a full,
heavy Forge, and the guild aggregator only proxies sessions/stats — not the catalog. We want
those basics to run **independently of the Forge session runtime**, and we want to **tag**
registered Forge instances so flocks/workflows can target one by label.

## Decisions already settled

- **Catalog = session-definitions + profiles + templates.** All three are config-driven
  today and served **as-is** (no new write paths — presets already provide CRUD elsewhere).
- **Presets stay forge-local.** A preset is a per-user, DB-stored "how to launch this
  workload" bundle (`source`, `integration_ids`, `env_secret_refs`, sidecars) bound to a
  specific Forge's runtime/secrets context — not a shared catalog entry. Keeping them out
  also means **no DB is required** for the catalog component.
- **Models are Bifrost's job**, not the Forge's (Workstream C).
- **Tag no-match fails loud** (503), never silently falls back to a default backend.

Tracked follow-ups (out of scope): replacing preset `env_secret_refs` with
integration-sourced secrets; making the catalog writable if central editing is later wanted.

---

## Workstream A — Forge-independent catalog component

Serve session-definitions + profiles + templates as a standalone, config-only component,
while the full Forge keeps serving them unchanged.

- `src/volundr/catalog/assembly.py` — `build_catalog(settings, *,
  launch_spec_repository=None) -> CatalogComponents` constructs the config providers, the
  `LaunchSpecService`, and the catalog router (launch-specs + session-definitions). Single
  source of truth for catalog wiring, with **no** DB, runtime adapters, or Bifrost.
- `src/volundr/main.py` reuses `build_catalog` so the catalog routes are assembled through one
  wiring path (providers are still shared with the contributor pipeline; services still land
  on `app.state`). The catalog has no separate process or console script — it is always
  mounted inside the Forge, which runs via `niuu platform up` (the only entry point).

Acceptance: the Forge serves `/api/v1/forge/{launch-specs,session-definitions}` assembled from
config alone, with the catalog builder exercised by `tests/test_catalog/` without a database.

## Workstream C — Untangle models/Bifrost from the Forge

Volundr stops co-hosting Bifrost and stops presenting itself as the models authority.

- `src/volundr/main.py` — remove the in-process Bifrost mount (`:503`) and `create_bifrost_app`
  import (`:12`). Keep `HttpBifrostCatalogAdapter` pointed at `settings.bifrost.url` **for
  pricing only** (option (a) — cost stays correct; the dependency becomes a clean HTTP call
  to the Bifrost service rather than an in-process mount). Keep a sane dev default for
  `bifrost.url` so single-binary dev still resolves.
- `src/volundr/adapters/inbound/rest.py:1628` — remove the Forge `/models` route + `ModelInfo`.
  Drop `/api/v1/forge/models` from the `forge-api` route domain in `src/volundr/plugin.py:93`.
- web-next — `plugin-volundr` has no `/models` dependency (verified); models stay sourced from
  `plugin-bifrost`.

Acceptance: Forge no longer serves `/models` or hosts Bifrost; cost math unaffected.

## Workstream D — Instance tagging + label targeting

- `src/niuu/domain/models.py` — `RegisteredInstance.tags: list[str]`.
- `migrations/000042_niuu_instance_tags.{up,down}.sql` — `ADD COLUMN tags JSONB NOT NULL
  DEFAULT '[]'` + `GIN(tags)`. **Mirror into both** `charts/guild/templates/migrations-
  configmap.yaml` and `charts/volundr/templates/migrations-configmap.yaml`.
- `src/niuu/config.py` — `InstanceSeedConfig.tags`.
- `src/niuu/ports/instances.py` + `adapters/postgres_instances.py` — optional `tags` + `match`
  (default `all`, from config) on `list_instances`; SQL `tags @> $1` (all) / `tags ?| $1`
  (any); persist tags.
- `src/niuu/domain/services/instances.py` — thread `tags`/`match` through `list_visible`,
  create/update/upsert; add `_matches_tags` helper.
- `src/niuu/adapters/inbound/rest_instances.py` — `tags` on create/update/response.
- `src/niuu/adapters/inbound/rest_volundr.py` — `_resolve_target_instance` filters by tag
  selector; **503 fail-loud** (lists unmatched tags) on no match.
- Ting — `target_tags` on `FlockFlowConfig`, `SagaTemplate`, `WorkflowDefinition`/`Saga`,
  `DispatchRequest`/`DispatchItem`, `WorkflowLaunchBody`; `dispatch_service.resolve_target_
  adapter` + `volundr_factory._resolve_registered_instances` filter by tag, fail-loud.
- web-next — `GuildPage.tsx` + volundr instance adapter: tag chips editor, display, filter.

Acceptance: an instance can be tagged; a flock/workflow/dispatch with `target_tags` runs only
on a matching backend and 503s loudly when nothing matches.

---

## "Still runs as one solution" — guardrails

There is one entry point — `niuu platform up` — and one shared catalog core; nothing in the
combined path is removed or branched on a run mode.

1. **One catalog builder, reused.** `build_catalog` is the single source of truth. The Forge's
   `create_app` mounts it alongside the runtime, so one process serves catalog + runtime.
2. **Config-only catalog.** `build_catalog` takes no DB/runtime adapters; passing a launch-spec
   repository only adds user-scope CRUD. The catalog is always mounted in-process — no separate
   server, no run-mode `if` branches in the lifespan.
3. **C preserves the combined solution.** Bifrost is already its own mounted plugin, so the
   all-in-one shell still serves `/api/v1/bifrost`; only volundr's redundant in-process mount
   goes away. Volundr consumes Bifrost via `settings.bifrost.url`.
4. **Catalog test.** `tests/test_catalog/` mounts `build_catalog(settings).router` on a bare
   app and asserts it serves `/api/v1/forge/{launch-specs,session-definitions}` from config
   with no database.
5. **Run mode tested to boot**; smoke check that `start-dev` (all-in-one `niuu platform up`)
   still serves catalog, Bifrost, and a session end-to-end after C.

Net: a single image and entry point (`niuu platform up`) serves the catalog and the runtime
through the one shared catalog builder, whether launched via `start-dev` or `start-guild`.

## Sequencing

1. **A** (self-contained; unblocks UI-loads-without-Forge).
2. **D** (independent feature).
3. **C** (cleanup; touches the same `main.py`/`rest.py` regions — after A to avoid churn).

---

# Launch-spec consolidation (E)

Collapse the overlapping layers — **profiles + templates + presets** — into a single
**LaunchSpec** with a `scope` (`system` = config-seeded read-only; `user` = DB CRUD), keeping
**session-definition** as the distinct runtime-type concept. Lands on this branch. No infra
changes; no breaking renames — external surfaces stay and are only deprecated.

## Load-bearing facts (from the usage map)

- `ForgeProfile` is already deprecated in favour of `WorkspaceTemplate`; `TemplateContributor`
  already prefers `template_name` and falls back to `profile_name` ([template.py:42](../src/volundr/adapters/outbound/contributors/template.py)).
- **Presets are stored but not applied at launch** — the pipeline only consumes
  `definition`/`template_name`/`profile_name`/raw config ([session.py:769-790](../src/volundr/domain/services/session.py)).
  We keep that behaviour (presets remain a managed library; applying them is a separate feature).
- session-definition is genuinely distinct (runtime type + Helm `defaults` deep-merge) — keep.
- **Do not touch** ting's `SpawnRequest.profile` — it is a *persona* name, a different concept
  that only collides by name ([dispatch_service.py:1143](../src/ting/domain/services/dispatch_service.py)).
- Parity-critical merge: SessionDefinitionContributor (base) → TemplateContributor →
  Prompt (after template) → … → SessionMCP; deep-merge, last-writer-wins, `mcpServers` by name.

## Proof mechanism

Local, **uncommitted** behaviour-parity harness (`/private/tmp/parity_harness.py`) snapshots
the merged contributor `values` for representative configs. Every phase must reproduce the
golden snapshot byte-for-byte. (`PARITY OK` gate.)

## Phases (each revertible, each gated by the harness)

- **Phase 0 — parity harness + golden snapshot** on current code. ✅ Done (uncommitted).
- **Phase 1 — introduce `LaunchSpec` + `LaunchSpecProvider`** internally; map existing config
  providers + preset repo into it; switch the contributor pipeline to consume it.
  `ForgeProfile`/`WorkspaceTemplate`/`Preset` remain as facades. Contributor public
  constructors unchanged so the harness stays valid. Gate: `PARITY OK` + boots.
- **Phase 2 — unify the catalog component (A) on `LaunchSpec`** for `scope=system`; keep
  `/profiles`, `/templates`, `/session-definitions` as compat shims; add one unified read route.
- **Phase 3 — presets as `scope=user` `LaunchSpec`** behind existing `/presets` CRUD; **not**
  applied at launch (behaviour preserved).
- **Phase 4 — point web-next at the unified surface**, old endpoints kept as deprecated shims.
- **Phase 5 — mark old surfaces deprecated** (no removal; config YAML keys untouched).
