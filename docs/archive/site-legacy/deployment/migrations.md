# Database Migrations

Volundr uses the [`migrate`](https://github.com/golang-migrate/migrate) tool for database migrations. Not Alembic, not an ORM.

## Migration files

Migrations live in two locations that must be kept in sync:

1. `migrations/*.sql` — source files
2. `charts/volundr/templates/migrations-configmap.yaml` — embedded in the Helm chart

## File naming

```
migrations/
  000001_initial.up.sql
  000001_initial.down.sql
  000002_add_chronicles.up.sql
  000002_add_chronicles.down.sql
```

- 6-digit zero-padded sequence number
- Always create both `.up.sql` and `.down.sql`

## Idempotent SQL

All migrations must be idempotent:

```sql
-- Tables
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    ...
);

-- Columns
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS owner_id TEXT;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
```

## Running migrations

### Development

Tables are auto-created on startup. No migration tool needed.

### Production

```bash
# Apply all pending migrations
migrate -path ./migrations -database "$DATABASE_URL" up

# Rollback last migration
migrate -path ./migrations -database "$DATABASE_URL" down 1
```

In Kubernetes, migrations run as an init container using the migrations ConfigMap.

## Creating a new migration

1. Create the SQL files in `migrations/`
2. Add them to `charts/volundr/templates/migrations-configmap.yaml`
3. Test both up and down migrations
