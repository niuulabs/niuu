# Migration Rules

## One Location

Write Volundr migrations in `migrations/` and Ting migrations in
`migrations/ting/`. **Nowhere else.** Every consumer reads those directories:

- **Helm** — `charts/volundr/migrations` and `charts/ting/migrations` are
  symlinks to them, and each chart's `templates/migrations-configmap.yaml`
  renders them with `.Files.Glob`. `helm package` follows the links, so the
  published chart carries the files. Never paste SQL into those templates.
- **Wheel / container images** — `pyproject.toml` force-includes
  `migrations/` as `cli/migrations`, where an installed Volundr or Ting runs
  them at startup (`cli.resources.migration_dir`). Images that build the wheel
  `COPY migrations/` for that reason.
- **Nuitka binary** — `src/cli/build.py` maps `migrations/` to the same
  `cli/migrations` layout.

`tests/test_charts/test_migrations_single_source.py` fails if any of these
starts reading a copy.

### The exception: Guild and niuu-shared

`charts/guild` and `charts/niuu-shared` embed their **own tailored migration
sets** for their separate databases — not copies. Their SQL deliberately
differs (no `sessions` table changes, a `pgcrypto` bootstrap). When a
migration touches a table one of them owns, add the tailored version to that
chart's `templates/migrations-configmap.yaml`;
`tests/test_charts/test_guild_migrations.py` checks Guild's coverage.

## Migration File Naming

Follow the pattern: `NNNNNN_description.up.sql` and `NNNNNN_description.down.sql`

- Use 6-digit zero-padded sequence numbers (000001, 000002, etc.)
- Always create both up and down migrations

## Idempotent Migrations

Use idempotent SQL statements:

```sql
-- Tables
CREATE TABLE IF NOT EXISTS ...

-- Columns
ALTER TABLE x ADD COLUMN IF NOT EXISTS ...

-- Indexes
CREATE INDEX IF NOT EXISTS ...
```

## Migration Tool

This project uses `migrate` (Kubernetes-native), NOT Alembic or other ORMs.
