# Single-host Docker mode

`docker` mode runs the whole platform as containers on one machine: a
PostgreSQL container, the all-in-one `niuu` container serving the API and the
web UI on one port, one `skuld` container per session, and an optional vLLM
container for a local model. It is the mode the installer selects and the one
a DGX Spark ships with, but it has no hardware assumptions: the same bundle
runs on a Linux server, a Mac with Docker Desktop, or a Raspberry Pi with the
local model turned off.

## Commands

| Command | What it does |
| --- | --- |
| `niuu up` | Run the host checks, render the compose bundle, `docker compose up -d`, wait for `/health`, print the setup URL. Same as `niuu platform up` in this mode. |
| `niuu doctor` | Run the host checks only. Exit code 1 when one fails. |
| `niuu status` | `docker compose ps` for the bundle. Same as `niuu platform status`. |
| `niuu down` | `docker compose down`. Data under the data directory is kept. Same as `niuu platform down`. |
| `niuu platform init` | Interactive first-time config; choose `4` for docker mode. |

`niuu up --mode docker` overrides the configured mode for one run, which is
what the installer uses on a machine without a config file yet.

## What `niuu up` checks

Docker CLI and daemon (with the socket-permission remedy when the user is not
in the `docker` group), Compose v2, the NVIDIA container runtime and a visible
GPU (reported, never required unless `docker.require_gpu` is on), the data
directory, free disk, the published port, registry reachability, and git.
Warnings do not block a start; failures do.

## What it writes

Under `docker.compose_dir` (default `~/.niuu/docker`):

| File | Content |
| --- | --- |
| `docker-compose.yaml` | The rendered bundle. Plain YAML; safe to read and run by hand. |
| `.env` | Non-secret variables the bundle references: images, bind host, external host, uid/gid, Docker socket group. |
| `secrets.env` | Mode 0600. `NIUU_POSTGRES_PASSWORD` and `NIUU_CREDENTIAL_KEY`, generated on the first run. Back this file up: losing the credential key makes every stored credential unreadable. |
| `host-facts.json` | Host facts shown on the wizard's welcome step. |

Under `docker.data_dir` (default `/var/lib/niuu`): `postgres/`, `workspaces/`,
`home/`, `credentials/`, `models/`, `residents/`, plus `config.yaml`,
`host-facts.json` and `setup-state.json`. The data directory is bind-mounted
into the platform container at the same path so session containers can mount
workspaces from it.

## Configuration

Everything lives under `docker:` in `~/.niuu/config.yaml` (env prefix
`NIUU_DOCKER__`):

```yaml
mode: docker
docker:
  data_dir: /var/lib/niuu
  compose_dir: ~/.niuu/docker
  image: ghcr.io/niuulabs/niuu:dev
  skuld_image: ghcr.io/niuulabs/skuld:dev
  postgres_image: pgvector/pgvector:pg17
  bind_host: 0.0.0.0        # 127.0.0.1 = this machine only
  require_gpu: false
  min_disk_space_gib: 50
  startup_timeout_seconds: 180
  vllm:
    enabled: false
    model: ""               # e.g. nvidia/Nemotron-3-Nano-30B-A3B
    max_model_len: 65536
    gpu_memory_utilization: 0.6
```

`server.port` stays the single published port (8080). `server.external_host`
sets the host in the printed setup URL; when empty the LAN address is detected.

## Inside the platform container

The `niuu` container runs `niuu platform up` in mini mode against the external
PostgreSQL container, with the Docker socket mounted. Sessions therefore run
as sibling containers (`DockerContainerPodManager`) rather than as host
processes, and the browser reaches them through the platform's session proxy.
The setup wizard is enabled with `NIUU_SETUP_ENABLED=true`, which only this
bundle sets; cluster deployments never show it.

## The setup wizard

`niuu up` ends by printing `http://<host>:8080/setup`. Until setup is marked
complete, the web app sends every visit to `/setup`; afterwards it opens the
normal dashboard and `/setup` stays reachable for changes. The wizard is a
front door over the platform's existing APIs: every value it stores lands where
**Settings → Integrations** already reads, so nothing is configured twice.

| Step | What it shows | What it writes |
| --- | --- | --- |
| Welcome | Host facts recorded by `niuu up` (hostname, OS, memory, GPU, Docker version). | Nothing. |
| System check | The same facts plus live checks from inside the platform: database reachable, Docker socket present, git installed. A failed check blocks **Continue**; a warning does not. | Nothing. |
| AI providers | Every `ai_provider` entry in the integrations catalog (Anthropic, OpenAI, ...). API-key entries get a form; entries that need a browser or device sign-in are marked as unavailable in this install. **Test connection** appears once connected. | An integration connection with an inline credential (`POST /api/v1/integrations`), stored encrypted with the key from `secrets.env`. |
| Git | `source_control` entries (GitHub, GitLab) with token and instance fields from the catalog schema. | Same. |
| Tickets | `issue_tracker` entries (Linear). | Same. |
| Finish | What was connected, grouped by type, and the access note (sign-in is off on this install). **Open Niuu** marks setup complete and opens `/ready`. | `POST /api/v1/niuu/setup/complete`. |

Progress is kept in `setup-state.json` under the data directory (each finished
step, and the completion time) so a reload resumes at the first unfinished
step. `POST /api/v1/niuu/setup/reset` clears it and the wizard shows again on
the next visit. Steps that need a backend this bundle does not have yet (a
local model served by vLLM, runtime and access choices, Claude and Codex device
sign-in) are not offered rather than shown as placeholders; enable vLLM through
`docker.vllm` in `config.yaml` for now.

## Updating

Change the image tags in `config.yaml` and run `niuu up` again. The bundle is
re-rendered, `docker compose up -d` recreates what changed, and migrations run
on the next platform start. Secrets are never regenerated once they exist.

## Stopping and removing

`niuu down` stops the containers and keeps all data. To remove everything,
also delete `docker.data_dir` and `docker.compose_dir`.
