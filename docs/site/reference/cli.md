# CLI Reference

The platform ships two command-line tools. They cover different layers, and
most work uses one or the other rather than both.

| Tool | Use it for | Full reference |
| --- | --- | --- |
| `niuu` | The platform: authentication, server contexts, the local stack, coding sessions, runs, and sagas. | [niuu CLI reference](cli-niuu.md) |
| `ravn` | The agent runtime: conversations and daemons, personas and profiles, rooms, flocks, and wardens on the local host. | [ravn CLI reference](cli-ravn.md) |

Each reference page opens with worked examples for the common tasks, then
lists every command and option. Both are generated from the live command
trees, so everything on them exists as written.

## Where to start

**Running the platform locally** — `./start-dev` is the short path; see
[run the local stack](cli-niuu.md#run-the-local-stack) for the CLI equivalent
and for choosing which services start.

**Talking to an agent** — `ravn run` takes a prompt for a single turn or opens
a REPL. See [talk to an agent](cli-ravn.md#talk-to-an-agent).

**Running several agents together** — a room is a local collaboration space
that needs no platform services. See
[start a room and put agents in it](cli-ravn.md#start-a-room-and-put-agents-in-it).

## Regenerating the reference pages

The two reference pages are generated. After adding or changing a command:

```bash
uv run python scripts/generate_cli_docs.py
```

Commit the regenerated pages with the change that prompted them. A test
(`tests/test_cli_help_coverage.py`) fails if a command or a visible option
lacks help text, so a new flag cannot land undocumented.
