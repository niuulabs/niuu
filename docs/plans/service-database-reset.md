# Service Database Reset

This plan tracks the storage reset that moves Niuu from a shared application
database to service-owned databases and service-owned migrations.

## Target endstate

- One PostgreSQL server per environment is acceptable.
- Each deployable service owns its own logical database.
- Each deployable service owns its own migrations.
- No service reaches into another service's tables directly.
- Local mode mirrors production shape by running one embedded PostgreSQL server
  with multiple logical databases.

## Database ownership

- `volundr`: runtime sessions, chronicles, prompts, workspaces, Forge state.
- `niuu-shared`: users, tenants, memberships, PATs, integrations, features,
  project mappings, credential metadata, shared admin state.
- `guild`: instance registry and Guild-owned registry support tables.
- `ting`: saga, workflow, dispatcher, review, notification state.
- `observatory`: observatory registry and stream state.
- `bifrost`: accounting and audit state.
- `ravn`: checkpoints, personas, long-lived memory state.
- `mimir`: search and indexing state, if persisted in PostgreSQL.

## Execution order

1. Guild
2. Observatory
3. Niuu Shared
4. Ting
5. Volundr
6. Bifrost
7. Ravn
8. Mimir

## Checklist

### Foundations

- [x] Add shared helpers for per-service database naming in local mode.
- [x] Teach embedded PostgreSQL bootstrap to create multiple logical databases.
- [x] Switch Guild local runtime to its own database.
- [x] Switch Observatory local runtime to its own database.
- [x] Switch Niuu Shared local runtime to its own database.
- [ ] Extend local bootstrap to create `ting`, `bifrost`, `ravn`, and `mimir`.
- [ ] Remove all remaining fallback assumptions that every service shares `DATABASE__NAME`.

### Migrations

- [x] Give `guild` chart-owned migrations.
- [x] Give `observatory` chart-owned migrations.
- [x] Give `niuu-shared` chart-owned migrations.
- [x] Ensure migration config changes roll deployments by checksum.
- [ ] Give `ting` an isolated database and database-specific migration ownership.
- [ ] Give `volundr` an isolated runtime-only schema boundary.
- [ ] Move `bifrost` off adapter-level auto-DDL and into owned migrations.
- [ ] Move `ravn` off adapter-level auto-DDL and into owned migrations.
- [ ] Add `mimir` migrations if PostgreSQL remains part of its storage path.

### Runtime untangling

- [ ] Remove top-level `niuu` migration orchestration for unrelated services.
- [ ] Make Ting stop reading `niuu_instances` from a shared database.
- [ ] Make Ting stop depending on shared `integration_connections` tables.
- [ ] Move any shared PAT verification logic behind the chosen service owner.
- [ ] Audit raw SQL repositories and confirm they touch only owned tables.
- [ ] Delete legacy runtime auto-DDL from `src/volundr/infrastructure/database.py`.

### Infrastructure

- [ ] Update chart defaults so every split service uses its own database name and secret naming.
- [ ] Provision one logical database per service in environment CNPG clusters.
- [ ] Provision one application role and secret per service where needed.
- [ ] Deploy Guild in Ymir as the first clean split service.
- [ ] Point Valhalla shared services at split databases.
- [ ] Turn off Infisical deployment paths in Valhalla.
- [ ] Remove bootstrap SQL hacks once chart-owned migrations are authoritative.

### Cutover

- [ ] Reset development and staging databases.
- [ ] Recreate schemas from owned migrations only.
- [ ] Verify local mode from a clean state.
- [ ] Verify Guild-only umbrella deployment in Ymir.
- [ ] Verify full umbrella deployment with disabled services omitted from ingress.
- [ ] Make startup fail fast when required schemas are missing.

## Current slice

This branch currently completes the first clean split path:

- Guild owns its migrations and now defaults to the `guild` database.
- Observatory owns its migrations and now defaults to the `observatory` database.
- Niuu Shared owns its migrations and now defaults to the `niuu_shared` database.
- Local mode creates separate logical databases for `volundr`, `niuu_shared`,
  `guild`, and `observatory`.

The remaining heavy lift is the Ting and Volundr untangling work, because those
services still rely on tables that belong in shared platform storage.
