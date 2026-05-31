# Database

Volundr uses PostgreSQL with raw SQL queries via asyncpg. There is no ORM.

## Configuration

```yaml
database:
  host: localhost
  port: 5432
  user: volundr
  password: volundr
  name: volundr
  min_pool_size: 5
  max_pool_size: 20
```

Or via environment variables:

```bash
DATABASE__HOST=postgres.svc.cluster.local
DATABASE__PASSWORD=secret
```

## Connection pooling

asyncpg manages a connection pool. Connections are acquired from the pool per-request and returned automatically. Pool size is configurable via `min_pool_size` and `max_pool_size`.

## Schema management

- **Development**: tables are auto-created on startup
- **Production**: migrations are managed by the `migrate` tool (Kubernetes-native)

See [Migrations](../deployment/migrations.md) for details.

## Repositories

Each domain entity has a dedicated PostgreSQL repository adapter:

| Adapter | Table(s) |
|---------|----------|
| `PostgresSessionRepository` | `sessions` |
| `PostgresChronicleRepository` | `chronicles` |
| `PostgresTimelineRepository` | `timeline_events` |
| `PostgresStatsRepository` | `sessions`, `token_usage` (aggregations) |
| `PostgresTokenTracker` | `token_usage` |
| `PostgresPresetRepository` | `presets` |
| `PostgresPromptRepository` | `saved_prompts` |
| `PostgresTenantRepository` | `tenants` |
| `PostgresUserRepository` | `users`, `tenant_memberships` |
| `PostgresMappingRepository` | `project_mappings` |
| `PostgresWorkspaceRepository` | `workspaces` |
| `PostgresEventSink` | `session_events` |

All queries use parameterized SQL (`$1`, `$2`, etc.) to prevent injection.
