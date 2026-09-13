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

`niuu up --mode docker` overrides the configured mode for one run. The
installer instead writes a `niuu` wrapper that runs the CLI from the platform
image with `NIUU_MODE=docker` and the pinned image tags in its environment, so
no config file is needed on the host and every later `niuu` command uses the
same image. The wrapper passes the host's identity (`--user`, the Docker
socket's group), `~/.niuu`, the data directory, `/etc/os-release` and the host
network into that container, and `--gpus all` when Docker has the NVIDIA
runtime, so the host checks below see the real host.

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

Under `docker.data_dir` (default `~/.niuu/data`, owned by you; a system path
such as `/var/lib/niuu` is a choice, not a requirement): `postgres/`, `workspaces/`,
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
  data_dir: ~/.niuu/data   # or a system path such as /var/lib/niuu
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
| Local model | Curated models (Nemotron 3 Nano 30B, gpt-oss-120b, Qwen3-Coder 30B) with a fit verdict against the host's accelerator memory and a memory meter, a custom Hugging Face id, **a model server you already run** (URL, model ids, optional key), or cloud-only. | A staged stack change (`vllm_enabled`, `vllm_model`, or `model_server_enabled`, `model_server_url`, `model_server_models`, `model_server_api_key`) through `PUT /api/v1/niuu/setup/stack`; applied on the finish step. |
| AI providers | A list of what is connected, one row per account, empty at first, and an **Add provider** button. Adding walks three small steps in a dialog: which provider, sign in or API key, then the sign-in card or the key form; the dialog closes by itself once the connection exists. A provider can be added as often as there are accounts (work and personal GitHub, two Anthropic keys): each account gets a name, which becomes its credential name (`github-work`), and the platform refuses a name already in use rather than overwrite that account's secret. Providers: Anthropic · Claude (Claude Code sign-in, or a key), OpenAI · Codex (ChatGPT device sign-in, or a key), xAI · Grok (Grok Build device sign-in, or a key), DeepSeek (key). Sign-ins run the official CLI in a sealed helper container; the card shows the link and device code, polls until the provider confirms, and for Claude takes the authorization code the browser hands back. **Test connection** calls the provider's models endpoint with the key and reports how many models it can see. | An integration connection with an inline credential (`POST /api/v1/integrations`), or an enrollment (`POST /api/v1/integrations/enrollments`) whose credential the platform stores when the sign-in completes. Both encrypted with the key from `secrets.env`. |
| Git | The same list and **Add Git host** dialog for GitHub and GitLab: **Sign in** (OAuth device flow through an application you own; the dialog asks for its client id the first time) or a personal access token. Right after a host is connected the wizard signs in as you and lists the repositories the credential can reach, so a wrong scope shows up here, not in a session; **Test connection** repeats that check. | Same. |
| Tickets | `issue_tracker` entries (Linear). | Same. |
| Runtime & access | Where sessions run (Docker container; OpenShell and host process shown as not offered here), how many sessions may run at once (**Sessions at once**, default 4; a launch is refused once that many are running), and who can reach this Niuu: only this machine, your local network (with the LAN address and a warning while sign-in is off), or public behind sign-in (later, in Settings → Access). | Staged stack changes (`bind_host`, `max_sessions`); applied on the finish step. |
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
2. The wizard stages a whitelisted change set (`bind_host`, `max_sessions`,
   `vllm_enabled`, `vllm_model`, `vllm_max_model_len`,
   `vllm_gpu_memory_utilization`) into
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
skuld image on the compose network, read-only except for a 256 MB memory-backed
`/tmp` (the Codex app-server clones its plugin marketplace on startup), with no platform environment and all capabilities dropped. It runs the
same `login_worker.py` the Kubernetes runner uses (`claude setup-token`, the
Codex app-server device flow, or `grok login --device-auth`). The platform
reads the worker's status over `docker exec`, stores the resulting credential
in the encrypted store, and removes the container. Cancelling, expiry (15
minutes) and a crashed helper all surface in the wizard with the reason, and
the platform log keeps the helper's exit code and output; `niuu down` removes
any helper that is still around.

### Sign in with GitHub / GitLab (device flow)

GitHub OAuth Apps and GitLab applications (17.2+) support the OAuth 2.0
device authorization grant: only a public client id is needed, no secret and
no callback URL, so it works on a single host. The platform runs it in-process
(`OAuthDeviceFlowRunner`): the card shows the provider's verification page
and code, polls the token endpoint at the interval the provider asks for, and
stores the user token (plus refresh token and expiry when the app issues
expiring tokens) under the same credential the token form would use.

An install never depends on an application someone else owns. The first time
someone picks **Sign in** for GitHub or GitLab, the dialog asks for the client
id of an application they create themselves, with a link to the right page:
a GitHub *OAuth App* with *Enable Device Flow* ticked (its device-flow tokens
do not expire and a secret is optional; they are requested with the `repo`,
`read:org` and `workflow` scopes, so one sign-in reaches every repository the
account can reach, and for an organisation that restricts third-party access
you register an app the organisation owns, or approve the app in its
settings, and the same token then covers the organisation and the personal
repositories alike), or a GitLab application with the device
grant enabled and the `api` and `read_user` scopes. The id is kept in the
encrypted credential store (`PUT /api/v1/integrations/oauth-clients/{slug}`)
and sign-in starts right after. A provider can carry several applications,
one per account: a second GitHub account may belong to an organisation that
only trusts its own OAuth App, so the sign-in pane lets each account pick the
application it signs in through or register another under a name of its own
(`PUT /api/v1/integrations/oauth-clients/github` with `app`). The account
remembers its application, and the token refresher uses the same one. Note
that GitHub and GitLab sign in whoever the browser is already logged in as;
for a different account use a private window or log out first. Operators can
also set the default application per instance:

```yaml
docker:
  sign_in_client_ids:
    github: Iv1.xxxxxxxxxxxxxxxx   # a GitHub App with "Device flow" enabled
    gitlab: xxxxxxxx               # an application on gitlab.com (or your instance)
  sign_in_client_secrets:          # optional; only GitHub needs one, to refresh tokens
    github: ${GITHUB_APP_CLIENT_SECRET}
```

Nobody is ever asked to edit configuration: the wizard offers the token form
and, for GitHub and GitLab, the sign-in that starts by registering the
person's own application. Linear and DeepSeek
have no device or OAuth flow usable without a registered callback, so they
stay key-based.

### How sign-in tokens stay valid

Nothing you sign into from the wizard has to be redone by hand while the
platform runs:

| Provider | Token lifetime | Who renews it |
|---|---|---|
| Claude Code (subscription) | About a year | Sign in again from the same row when the wizard shows *Token expired*. |
| OpenAI Codex (ChatGPT) | Hours | Sessions fetch tokens from the platform's Codex credential broker, which refreshes them in the store. |
| Grok Build | 7 days, no refresh token | Sign in again from the same row when the wizard shows *Token expired*. The file is mounted read-only at `~/.grok/auth.json`; the CLI hot-reloads it, so a new sign-in reaches running sessions on their next start. |
| GitHub (App sign-in) | 8 hours when the app issues expiring tokens, otherwise unlimited | The platform's token refresher. Add `docker.sign_in_client_secrets.github` for refresh, or turn off *Expire user authorization tokens* on the app. |
| GitLab (device sign-in) | 2 hours | The platform's token refresher, with the public client id alone. |

The refresher (`OAuthTokenRefreshService`) runs every five minutes in the
shared host, the one place that owns integrations in every deployment. It
refreshes any device-flow token that expires within ten minutes and
flips the connection to *Sign-in needed* with the reason `refresh_failed` when
the provider rejects the refresh. The wizard row shows how long the current
token is still good for and why a sign-in is needed. Sessions never write
credentials back: the platform is the only writer of the store, and a session
only ever reads what the platform renders for it.

### When there is no room for another session

The bundle runs at most `max_sessions` sessions at once (default 4; the
platform's `pod_manager.max_concurrent`). Launching or restarting a session
beyond that is refused at once, before any session record is created, with a
`409` whose message says how many are running and where the limit is raised:
"No session slot is free: 4 of 4 sessions are running on this host. Stop or
archive a session, or raise the session limit in Settings → Runtime
(/settings/runtime/sessions)." The launch dialogs and the session page show
that message with the path as a link. **Settings → Runtime → Sessions** shows
**Sessions at once**; saving it applies the change through the stack
controller and restarts the platform, which takes about a minute. The setup
wizard's Runtime & access step edits the same setting (`/setup?step=runtime`
opens it directly). On a host install without the bundle the field is
read-only and the refusal names `pod_manager.max_concurrent` in `config.yaml`
instead.

### Settings that persist

**Settings → Forge → Storage** (the file-manager tab in sessions, and home
volumes where the storage backend has them) is stored in the platform
database, so what an admin sets survives restarts and the next `niuu up`.
The **Home Volumes** toggle is only offered when the storage adapter can give
each user a persistent home volume (Kubernetes PVCs or host directories); the
bundle's in-memory storage cannot, so the field is hidden and
`/api/v1/forge/feature-flags` reports `home_volumes_supported: false`.

### Which engines a session can use

**Launch a new session** (and **Advanced launch** behind it) offers an
**Engine** dropdown: Claude Code, Claude Code Interactive, OpenAI Codex, Grok
Build, OpenCode, DeepSeek Harness and so on. Only the engines a connected AI
provider powers are listed. Every engine is a session definition that names
the model vendors it accepts (`compatible_providers`, e.g. `["anthropic"]`),
and every AI provider in the integration catalog says which vendor a
connection of it unlocks (`model_vendor`: the Claude subscription sign-in and
an Anthropic API key both unlock `anthropic`). A provider-neutral engine such
as OpenCode appears as soon as any AI provider is connected. Under the
dropdown the launch dialog says what the selected engine is for and which of
your accounts it will use; **Manage providers** opens **Settings →
Integrations**, where accounts are added, tested and signed in again. With no
AI provider connected the dialog says so and links there instead of offering
an engine that could not start. Engines meant for other callers stay out of
the list: the `batch` Codex definition is Ting's, and Remote Control sessions
are driven from the Claude app.

When more than one account could run the engine (a Claude subscription and
an Anthropic API key, two ChatGPT logins) an **Account** dropdown appears and
the launch attaches exactly that one; nothing else about the choice is
implicit. A session gets one AI credential, the Git account that listed the
repository it clones (a pasted URL gets every Git account, since any of them
might own it), and the rest of your integrations such as a tracker. The
advanced launch shows the same selection under **Access**, where it can be
changed by hand. With only an Anthropic API key attached the session is told
to use it (`SKULD__CLAUDE_AUTH=api_key`); with a subscription attached, that
wins.

Two things a fresh sandbox needs are handled by the session itself: the Codex
app-server refuses to start until its `CODEX_HOME` directory exists, so the
transport creates it, and if the app-server still cannot start the session
reports that error rather than quietly running a different Codex. The
interactive Claude engine answers the CLI's first-run questions (onboarding,
workspace trust, the bypass-permissions notice) in the CLI's own config
before it starts, because nobody is at that keyboard and the OAuth token in
the environment does not count as a login for the onboarding screen.

### Using a model you serve yourself

You do not have to let the bundle run vLLM. Any OpenAI-compatible server,
vLLM or sparkrun on this Spark, Ollama on the same machine, a second Spark
across the network, becomes a provider in two places at once:

- **Settings → Runtime → Model server** (or the wizard's Local model step):
  switch it on, give the server's base URL *without* `/v1` as the platform
  container reaches it (a server on this host is
  `http://host.docker.internal:<port>`), the model ids it serves
  (comma-separated; the first is the default), and a bearer token only if the
  server checks one. Saving restarts the platform.
- After the restart the server is the `local` provider of the model gateway
  (Bifrost, `/api/v1/bifrost`, which speaks both the Anthropic and the OpenAI
  dialect and forwards to the server), and a **Model server** AI provider is
  seeded under **Settings → Integrations**. The wizard-managed vLLM container
  gets the same treatment as provider `vllm`.

That provider unlocks the `local` vendor, which the Claude Code, Claude Code
Interactive and OpenAI Codex engines accept, so it appears in the launch
dialogs like any other account, with a **Model** dropdown of the ids you
listed. A session launched with it gets `SKULD__MODEL_GATEWAY__URL` from the
connection and its Skuld routes the CLI through the gateway: Claude Code with
`ANTHROPIC_BASE_URL` (the platform API key is dropped so it cannot win), Codex
with a `niuu` model provider block (`wire_api = "responses"`: the gateway
serves the OpenAI Responses API at `/v1/responses` for it, its key from
`NIUU_MODEL_GATEWAY_TOKEN`; no ChatGPT sign-in is attempted). Ravn residents
already talk to the gateway, so the server's models show up for them as
`niuu/<model>` without further setup. The bundle's gateway is open; the token
the CLIs present is a placeholder there.

A server that stops serving a model fails the session's first turn with the
gateway's error, not a silent fallback to a cloud model. Switching the
server off (or picking vLLM or cloud-only in the wizard) stops seeding the
provider; delete the stale **Model server** entry under Settings →
Integrations so it leaves the launch dialogs.

## Updating

Change the image tags in `config.yaml` and run `niuu up` again. The bundle is
re-rendered, `docker compose up -d` recreates what changed, and migrations run
on the next platform start. Secrets are never regenerated once they exist.

## Stopping and removing

`niuu down` stops the containers and keeps all data. To remove everything,
also delete `docker.data_dir` and `docker.compose_dir`.
