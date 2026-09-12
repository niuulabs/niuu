# Develop Niuu locally

Use a source checkout when changing the platform or testing unreleased fixes.
[Installation](../get-started/install.md#from-source) lists the required build
tools and dependency setup.

## Build and start

From the repository root:

```bash
uv sync --python 3.12 --extra dev
./start-dev
```

The script prepares dependencies and assets and starts a background stack. Use
the exact URL it prints, including its UI configuration query string. The first
run can take longer because PostgreSQL and web assets must be built.

## Inspect and stop

```bash
tail -n 100 build/dev-run/logs/platform.log
./stop-dev
```

`stop-dev` manages the stack launched by `start-dev`. For a direct
`niuu platform up` run, use Ctrl+C in that process's terminal. A separate
`niuu platform down` invocation does not own the foreground process's service
manager.

## Verify a change

Run focused tests for the affected behavior while developing. Repository checks:

```bash
make verify
```

For web changes, from `web-next`:

```bash
pnpm test
```

For docs, from the repository root:

```bash
uv run --extra dev python scripts/extract_openapi.py -o docs/site/openapi.json
uvx --from mkdocs-material==9.7.5 mkdocs build --strict
```

The [quick-start check](quickstart-verification.md) exercises real database and
session startup. Its live mode requires provider authentication. Unit tests,
docs rendering, and a real model response verify different parts of the system.
