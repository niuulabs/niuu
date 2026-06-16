# Migrations

Database migrations must be handled deliberately in shared environments.

## Guidance

- Back up production databases before applying migrations.
- Apply migrations during a maintenance window when possible.
- Verify migration state after deployment.
- Keep app versions and schema versions aligned.

Use service-specific migration commands and deployment automation for exact migration behavior.
