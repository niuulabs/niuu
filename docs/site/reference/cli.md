# CLI Reference

The platform ships two command-line tools. They cover different layers, and
most work uses one or the other rather than both.

| Tool | Use it for | Full reference |
| --- | --- | --- |
| `niuu` | The platform: authentication, server contexts, the local stack, coding sessions, runs, and sagas. | [niuu CLI reference](cli-niuu.md) |
| `ravn` | The agent runtime: conversations and daemons, personas and profiles, rooms, flocks, and wardens on the local host. | [ravn CLI reference](cli-ravn.md) |

Both reference pages are generated from the live command trees, so every
command and option on them exists as written.

## Running the local stack

For day-to-day development the script path is the shortest one:

```bash
./start-dev
./stop-dev
```

`./start-dev` brings up the full local stack on `:8080` in mini mode with an
embedded Postgres. Reach for the CLI directly when you are debugging the
platform host itself:

```bash
uv run niuu platform up --skip-preflight --host-profile full
uv run niuu platform status
uv run niuu platform down
```

## Talking to an agent

`ravn run` starts a conversation — pass a prompt for a single turn, or omit it
for a REPL:

```bash
uv run ravn run "summarise the failing tests"
uv run ravn run --persona reviewer
```

`ravn personas list` and `ravn profiles list` show what `--persona` and
`--profile` accept. Persona picks *who* an agent is; profile picks *how* it is
deployed.

## Running several agents together

A room is a local collaboration space that needs no platform services. Create
one, put persona-typed agents in it, and watch the exchange:

```bash
uv run ravn room create desk
uv run ravn join --persona reviewer --room desk
uv run ravn join --persona coder --room desk --as builder
uv run ravn room members --room desk
uv run ravn room tail --room desk --follow
```

A flock is the same idea at the process level — a mesh of daemons that can
delegate work to each other. Point one at a room and its nodes join as
members:

```bash
uv run ravn flock init --room desk reviewer coder
uv run ravn flock start
uv run ravn flock peers
```

## Regenerating the reference pages

The two reference pages are generated. After adding or changing a command:

```bash
uv run python scripts/generate_cli_docs.py
```

Commit the regenerated pages with the change that prompted them.
