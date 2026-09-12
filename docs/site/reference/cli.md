# Command-line tools

Niuu ships a platform CLI and an agent-runtime CLI. Choose the one that owns the
operation you want to perform.

| Tool | Responsibility | Reference |
| --- | --- | --- |
| `niuu` | Start the platform, select server contexts, authenticate, and operate sessions and runs | [Niuu commands](cli-niuu.md) |
| `ravn` | Run conversations and daemons, select personas, and operate rooms, flocks, and wardens | [Ravn commands](cli-ravn.md) |

## Inspect before running

```bash
niuu --help
niuu platform up --help
ravn --help
ravn run --help
```

The command reference is generated from the current command trees. That verifies
available commands and options, not provider access or successful execution of
every example. Use the [quick start](../get-started/first-local-stack.md) for the
validated local lifecycle, and [Ravn setup](../get-started/direct-and-resident-assistants.md)
for the model configuration needed by direct agent calls.

## Server contexts and local processes

A CLI operation against a configured server context is different from starting
a host locally. `niuu platform up` runs in its own process; stop it in that
terminal with Ctrl+C. Do not expect a separate CLI process to share its in-memory
service manager.

## Maintain the reference

After changing CLI help or command definitions, regenerate both pages:

```bash
uv run python scripts/generate_cli_docs.py
```

Review the result with the implementation change. The CLI help-coverage test
requires descriptions for visible commands and options. Tests of runtime behavior
and the release smoke test provide separate execution evidence.
