# CLI Reference

Use the Niuu CLI for local platform and service operations.

The current development path is script-first:

```bash
./start-dev
./stop-dev
```

The Python package also exposes service commands through `uv run niuu ...`.

## Common commands

```bash
uv run niuu platform up --skip-preflight --host-profile full
```

Use the scripts unless you are debugging the platform host directly.
