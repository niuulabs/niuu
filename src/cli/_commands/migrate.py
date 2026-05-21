"""``niuu migrate`` — run database migrations."""

from __future__ import annotations

from cli.resources import migration_dir, ordered_migration_files


def execute(target: str = "latest") -> int:
    """Apply SQL migrations from embedded files."""
    try:
        mig_dir = migration_dir("volundr")
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        return 1

    sql_files = ordered_migration_files(mig_dir)
    if not sql_files:
        print("No migration files found.")
        return 1

    print(f"Found {len(sql_files)} migrations (target={target})")
    for f in sql_files:
        print(f"  {f.name}")
    # TODO: execute migrations against database
    return 0
