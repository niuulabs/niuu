# Database migrations and upgrades

Local embedded startup and a shared deployment do not use the same migration
lifecycle. Identify which path owns the schema before upgrading.

## Local embedded database

The Niuu root server prepares embedded databases and applies bundled bootstrap
and migration SQL during startup. Source migrations are under `migrations/`, with
Ting migrations in `migrations/ting/`. Startup logs report the attempted migration
work; inspect errors rather than treating HTTP health as schema verification.

Do not delete local database files to solve an unexplained migration failure if
they contain sessions or other state you need. Preserve the data and the startup
logs before testing a recovery path on a copy.

## Kubernetes

The Völundr chart has a `migrations` configuration and a migration init container.
Its SQL is embedded in the chart's migration ConfigMap. Inspect the selected chart
version and the init-container logs when a pod cannot start.

Before an upgrade, take a database backup and verify how to restore it. Render
the new chart, inspect its migration changes, and confirm the application version
matches the schema. Rolling back an image does not automatically undo SQL or
restore data changed after the upgrade.

## Changing a migration

Contributors add paired `NNNNNN_description.up.sql` and `.down.sql` files and keep
the corresponding Helm migration ConfigMap synchronized. A successful local run
alone does not prove the chart contains the same migration.

Exercise the affected upgrade path and verify representative reads and writes
with existing data. New-install tests are insufficient for a migration that changes
an already populated table.
