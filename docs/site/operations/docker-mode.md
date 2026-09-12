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
`home/`, `credentials/`, `session-secrets/`, `models/`, `residents/`, plus
`config.yaml`, `host-facts.json`, `setup-state.json`, `stack.yaml` and the
wizard's `stack-staged.yaml` / `stack-overrides.yaml`. The data directory is
bind-mounted into the platform container at the same path so session
containers can mount workspaces and secrets from it.

### How sessions get their credentials

Credentials entered in the wizard are stored encrypted (Fernet, key in
`secrets.env`) under `credentials/`. When a session starts, the platform
renders only the fields that session's integrations ask for into
`session-secrets/<session-id>/`: an `env.sh` with `export NAME='value'` lines
and one file per requested file mount. Those files are bind-mounted read-only
into the session container (`/run/secrets/env.sh` and the requested paths) and
the skuld entrypoint sources them on start-up. Secret values never appear in
the container's environment as seen by `docker inspect`, the platform's own
environment is not inherited by the sandbox, and the directory is removed when
the session stops. A session whose integration credential is missing from the
store fails to start with an error naming the credential.

Kubernetes-only session features are switched off explicitly in this mode:
workload identity (the projected service-account token) has no issuer on a
single host, so the bundle disables that contributor. Any other volume a
contributor asks for must be a host path; anything else fails the session
start with a clear error instead of being silently dropped.

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
  bind_host: 0.0.0.0        # 127.0.0.1 = this machine only (the wizard can change this)
  applier_image: docker:28-cli
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

The bundle sets these on the platform container beyond the usual mini-mode
values; they are plain environment variables in `docker-compose.yaml` and can
be read there:

| Variable | Purpose |
| --- | --- |
| `NIUU_SETUP_MODE=docker` | What the wizard reports as the runtime (the platform itself still runs `NIUU_MODE=mini`). |
| `NIUU_STACK_DIR` | Where the stack controller finds `stack.yaml` and keeps staged and applied wizard changes. |
| `NIUU_DATABASE_MODE=external` + `DATABASE__*` | Use the `postgres` service instead of embedded PostgreSQL; service databases are created on start-up. |
| `INTEGRATIONS__DATABASE_NAME=niuu_shared` | Sessions resolve the integration connections the wizard created through the shared API. |
| `CREDENTIAL_STORE` / `SECRET_INJECTION` | File credential store under `credentials/` and the per-session materializer under `session-secrets/`, both keyed by `NIUU_CREDENTIAL_KEY`. |
| `SESSION_CONTRIBUTORS` | Enables the secret-injection contributor and disables workload identity (see above). |
| `NIUU_POD_MANAGER__*` | `DockerContainerPodManager` with the compose network, the skuld image and the in-network platform URL sessions call back to. |
| `SESSION_ROOM__INTERNAL_BASE_URL` | The platform dials session brokers through its own loopback proxy instead of the published LAN address, which is not reachable from inside the container. |

## The setup wizard

`niuu up` ends by printing `http://<host>:8080/setup`. Until setup is marked
complete, the web app sends every visit to `/setup`; afterwards it opens the
normal dashboard and `/setup` stays reachable for changes. The wizard is a
front door over the platform's existing APIs: every value it stores lands where
**Settings → Integrations** already reads, so nothing is configured twice.

| Step | What it shows | What it writes |
| --- | --- | --- |
| Welcome | Host facts recorded by `niuu up` (hostname, OS, memory, GPU, Docker version). | Nothing. |
| System check | Every preflight result `niuu up` recorded (Docker, Compose, NVIDIA runtime, GPU, data directory, disk space, ports, outbound network to the registries and providers, git) plus live checks from inside the platform: database reachable, Docker socket present, git installed. A failed check blocks **Continue**; a warning does not. | Nothing. |
| Local model | Curated models (Nemotron 3 Nano 30B, gpt-oss-120b, Qwen3-Coder 30B) with a fit verdict against the host's accelerator memory and a memory meter, a custom Hugging Face id, or cloud-only. | A staged stack change (`vllm_enabled`, `vllm_model`) through `PUT /api/v1/niuu/setup/stack`; applied on the finish step. |
| AI providers | One pane per provider with a switch between **Sign in with your subscription** and **Use an API key**. Anthropic · Claude (Claude Code sign-in, or a key), OpenAI · Codex (ChatGPT device sign-in, or a key), xAI · Grok (Grok Build device sign-in, or a key), DeepSeek (key). Sign-ins run the official CLI in a sealed helper container; the card shows the link and device code, polls until the provider confirms, and for Claude takes the authorization code the browser hands back. **Test connection** calls the provider's models endpoint with the key and reports how many models it can see. | An integration connection with an inline credential (`POST /api/v1/integrations`), or an enrollment (`POST /api/v1/integrations/enrollments`) whose credential the platform stores when the sign-in completes. Both encrypted with the key from `secrets.env`. |
| Git | GitHub and GitLab panes: **Sign in** (OAuth device flow, needs only the public client id in `docker.sign_in_client_ids`) or a token. **Test connection** signs in as you and lists the repositories the credential can reach, so a wrong scope shows up here, not in a session. | Same. |
| Tickets | `issue_tracker` entries (Linear). | Same. |
| Runtime & access | Where sessions run (Docker container; OpenShell and host process shown as not offered here) and who can reach this Niuu: only this machine, your local network (with the LAN address and a warning while sign-in is off), or public behind sign-in (later, in Settings → Access). | A staged stack change (`bind_host`); applied on the finish step. |
| Finish | What was connected, the local model and access choice, and the staged changes about to be applied. **Apply and open Niuu** applies them (the platform restarts the services whose configuration changed, the page waits for it to answer again and warns when the current address stops being served), then marks setup complete and opens `/ready`. | `POST /api/v1/niuu/setup/stack/apply`, then `POST /api/v1/niuu/setup/complete`. |

Progress is kept in `setup-state.json` under the data directory (each finished
step, and the completion time) so a reload resumes at the first unfinished
step. `POST /api/v1/niuu/setup/reset` clears it and the wizard shows again on
the next visit.

### How the wizard changes the stack

The platform cannot re-bind its own published port from a browser toggle, so
changes go through a stack controller (`NIUU_STACK_DIR`, the data directory):

1. `niuu up` records the effective bundle settings in `stack.yaml`, mounts the
   compose directory into the platform container and pre-pulls the applier
   image (`docker.applier_image`, default `docker:28-cli`).
2. The wizard stages a whitelisted change set (`bind_host`, `vllm_enabled`,
   `vllm_model`, `vllm_max_model_len`, `vllm_gpu_memory_utilization`) into
   `stack-staged.yaml`; `GET /api/v1/niuu/setup/stack` shows current, staged
   and effective settings plus the curated model list with fit verdicts.
3. **Apply** folds the staged set into `stack-overrides.yaml`, re-renders the
   compose bundle, and runs `docker compose up -d` from a short-lived applier
   container on the Docker socket, so the platform container can be recreated
   underneath it. `GET /api/v1/niuu/setup/stack/status` reports applying,
   applied or failed (with the applier's log tail), and the vLLM container's
   state (absent, starting with its last log line, ready, failed) while a
   model downloads.
4. Every later `niuu up` merges `stack-overrides.yaml` over `config.yaml`, so
   a restart from the CLI never reverts what the wizard applied. Delete that
   file to go back to `config.yaml` alone.

### Subscription sign-in (Claude Code, Codex)

The bundle configures `CREDENTIAL_ENROLLMENT_RUNNER` with
`DockerLoginRunner`: each sign-in starts a `niuu-login-<id>` container from the
skuld image on the compose network, read-only except for a memory-backed
`/tmp`, with no platform environment and all capabilities dropped. It runs the
same `login_worker.py` the Kubernetes runner uses (`claude setup-token`, the
Codex app-server device flow, or `grok login --device-auth`). The platform
reads the worker's status over `docker exec`, stores the resulting credential
in the encrypted store, and removes the container. Cancelling, expiry (15
minutes) and a crashed helper all surface in the wizard with the reason, and
the platform log keeps the helper's exit code and output; `niuu down` removes
any helper that is still around.

### Sign in with GitHub / GitLab (device flow)

GitHub Apps and GitLab applications (17.2+) support the OAuth 2.0 device
authorization grant: only a public client id is needed, no secret and no
callback URL, so it works on a single host. The platform runs it in-process
(`OAuthDeviceFlowRunner`): the card shows the provider's verification page
and code, polls the token endpoint at the interval the provider asks for, and
stores the user token (plus refresh token and expiry when the app issues
expiring tokens) under the same credential the token form would use. Configure
the client ids once:

```yaml
docker:
  sign_in_client_ids:
    github: Iv1.xxxxxxxxxxxxxxxx   # a GitHub App with "Device flow" enabled
    gitlab: xxxxxxxx               # an application on gitlab.com (or your instance)
```

Until a client id is set the pane still offers the token form and the sign-in
tab says exactly what is missing. Linear and DeepSeek have no device or OAuth
flow usable without a registered callback, so they stay key-based.

## Updating

Change the image tags in `config.yaml` and run `niuu up` again. The bundle is
re-rendered, `docker compose up -d` recreates what changed, and migrations run
on the next platform start. Secrets are never regenerated once they exist.

## Stopping and removing

`niuu down` stops the containers and keeps all data. To remove everything,
also delete `docker.data_dir` and `docker.compose_dir`.
