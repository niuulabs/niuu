# Memory in mini mode

`niuu platform up` in mini mode runs memory out of the box: one database-only
gbrain on this machine, started and stopped with the stack. Mímir serves its
knowledge from it, the memory gateway keeps every layer (org, project, run,
session) in it, and Forge gives each session a `memory` server that reaches the
gateway. Memory › Search, Review and the knowledge graph work against it.

The design of memory is in [docs/design/memory/README.md](../design/memory/README.md);
the gateway on a cluster is in [mimir.md](mimir.md).

## What runs

| Step | What happens |
| --- | --- |
| Before the API server | gbrain starts on `127.0.0.1:3141` (`local_memory.gbrain.port`) and must answer `/health` |
| First start only | gbrain's admin token and the memory signing key are created (64 random hex characters, 0600); `gbrain init --pglite --non-interactive --no-embedding --db-only`; `gbrain auth create niuu-mimir-<random> --scopes read,write` for Mímir's own store |
| Every start | `gbrain serve --http` with the admin token in `GBRAIN_ADMIN_BOOTSTRAP_TOKEN`; `~/.niuu/memory/mimir.yaml` is rewritten |
| Stop (Ctrl+C or SIGTERM) | after the API server: SIGTERM, then a kill after `local_memory.gbrain.stop_timeout_seconds` (logged at error) |

`--db-only` matters: without it gbrain turns on managed persistence and refuses
the `sources_add` the gateway makes for each layer.

gbrain gets only `PATH`, `HOME`, `TMPDIR`, the locale and its own variables, so a
`DATABASE_URL` in the platform's environment cannot move it off PGlite. It runs in
its own process group, so the platform stops it in order; a gbrain that dies while
the platform runs is logged at error, and memory calls fail until the next start.

The platform sets these for the services it hosts. Setting any of them yourself
while `local_memory.enabled` is true stops `niuu platform up` with the conflict
named:

| Variable | Value |
| --- | --- |
| `MIMIR__BACKEND` | `GBrainMimirAdapter` on gbrain's `/mcp`, Mímir's own token file, `query_expansion: false` (this gbrain has no model) |
| `MIMIR__MEMORY` | one system `local-brain` (`GBrainBackend`, admin token file, signing key file), `tenants: {default: local-brain}`, `NoAuthHeaderAdapter` service auth |
| `MEMORY__GATEWAY_URL` | `http://127.0.0.1:<server.port>/api/v1/mimir`, the gateway as sessions reach it |

## Files (`~/.niuu/memory`, mode 0700)

| File | What it is |
| --- | --- |
| `gbrain/` | gbrain's home (`GBRAIN_HOME`): its config and the PGlite database |
| `memory-signing-key` | Seals the trust and layer of every record. **Back it up**: a new key makes every existing record fail its seal |
| `gbrain-admin-token` | gbrain's owner credential; the gateway creates each layer's source and client with it |
| `gbrain-mimir-token` | Mímir's read/write token for its own store |
| `mimir.yaml` | Mímir's settings for this brain, for your own `python -m mimir` commands |
| `gbrain.log` | gbrain's output |
| `gbrain.pid` | The running gbrain, while the platform runs |

A secret file that others can read, or an empty one, stops the start with the
remedy; nothing is regenerated over an existing file.

## Where gbrain comes from

gbrain is compiled into one executable (Bun, gbrain and PGlite together) from the
commit and security patch that `containers/gbrain/Dockerfile` pins, by
`scripts/build_gbrain.sh`. A laptop and a cluster run the same gbrain.

- **Release binary:** carried inside `niuu` (the release workflow builds it per
  platform).
- **Source checkout:** `make build-gbrain` (needs Bun >= 1.3.11) writes
  `build/gbrain/gbrain`; `./start-dev` runs it.
- **Your own build:** set `local_memory.gbrain.executable`.

With none of these, `niuu platform up` stops and says so.

Docker mode (`niuu up` with `mode: docker`) runs the platform from an image that
carries no gbrain, so its bundle sets `NIUU_LOCAL_MEMORY__ENABLED=false`: memory
there still needs a gbrain you run and `MIMIR__BACKEND` / `MIMIR__MEMORY` you set.

## Configuration (`local_memory` in `~/.niuu/config.yaml`)

The section is `local_memory`, not `memory`: Forge reads `memory` from the same
file for its own gateway settings.

```yaml
local_memory:
  enabled: true                 # false: run without memory (Mímir then needs MIMIR__BACKEND)
  home: ~/.niuu/memory
  system_name: local-brain      # memory.systems[].name
  tenants: [default]            # without authentication every caller is 'default'
  gbrain:
    executable: ""              # empty: the carried gbrain
    host: 127.0.0.1
    port: 3141
    command_timeout_seconds: 300     # gbrain init / auth create on the first start
    startup_timeout_seconds: 120     # until gbrain answers /health
    health_poll_interval_seconds: 0.5
    health_timeout_seconds: 5
    stop_timeout_seconds: 15         # SIGTERM to kill
```

Each timeout that runs out stops the start (or, for the stop, kills gbrain and
logs at error) and names its setting.

Memory needs `host_auth.mode: none`. The gateway calls Forge without a credential
of its own, which only an allow-all Forge admits; under `host_auth.mode: oidc`,
`niuu platform up` refuses `local_memory.enabled: true` and asks you to set it to
false.

## Import a wiki

```bash
MIMIR_CONFIG=~/.niuu/memory/mimir.yaml \
  uv run python -m mimir import-wiki --root ./old-wiki --tenant default --layer org
```

Run it while the platform runs: it writes through gbrain's HTTP endpoint. Use
`--layer project:<project id>` for a project's memory.

## Troubleshooting

| Message | What to do |
| --- | --- |
| `this niuu carries no gbrain` | `make build-gbrain` in a source checkout, set `local_memory.gbrain.executable`, or `local_memory.enabled: false` |
| `gbrain from an earlier niuu platform up is still running (pid N)` | The platform was killed before it could stop gbrain. `kill N` and start again |
| `gbrain cannot listen on 127.0.0.1:3141` | Another process holds the port. Stop it, or set `local_memory.gbrain.port` |
| `gbrain exited with code ... while starting` | The message quotes the end of `gbrain.log`, which says why |
| `MIMIR__BACKEND is already set in the environment` | Unset it, or set `local_memory.enabled: false` to configure memory yourself |
