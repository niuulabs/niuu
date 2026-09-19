# Niuu on a DGX Spark: one command, then a wizard

Status: proposal + mockup (2026-09-12); `docker` mode implemented on this
branch. Mockup source: `docs/mockups/spark-onboarding/`
(run `node docs/mockups/spark-onboarding/build.mjs` to regenerate the artboards).

## Goal

A person with a clean Spark should get to a working Niuu, with their own
providers, Git and tracker connected, in about ten minutes and without
reading docs. Two surfaces:

1. **Terminal, one command.** `curl -fsSL https://get.niuu.ai | sh` (or
   `niuu up` if the CLI is already there). It installs the CLI, runs
   preflight, pulls the stack, starts it, prints the setup URL. It never
   asks a question.
2. **Browser, `/setup` wizard.** Eight steps, one decision per screen,
   every integration with a "connected" state and a fix-it path. Ends on
   `/ready` with three in-product walkthroughs.

## What runs (`docker` mode)

Docker-first, and deliberately not a Spark-only path: `docker` mode runs the
whole stack as containers on any single Docker host (a Mac, a Linux box, a
DGX Spark). The Spark is the reference target and gets the polish (GPU
detection, local models, the wizard's presentation), but nothing in the code
branches on "spark". A compose bundle is the unit:

| Container | Role | Status |
|---|---|---|
| `postgres` | PG 17 + pgvector, one instance, per-service databases | rendered; databases and migrations provisioned by `RootServer` on first start |
| `niuu` | Volundr + Ting + Bifrost + Skuld proxy + web in the shared root server on `:8080` | rendered; runs `niuu platform up` inside the container, web assets and git now baked into `containers/niuu` |
| session containers | one `skuld` container per Forge session, GPU-capable, reached by container name | `DockerContainerPodManager` |
| `vllm` | optional local model on the host GPU | rendered when `docker.vllm.enabled` |

`docker` is a CLI mode alongside `mini` / `openshell` / `cluster`. `niuu up`
in that mode = host preflight + rendered compose bundle under
`~/.niuu/docker` (compose file, env file, `secrets.env` with mode 0600,
`host-facts.json`) + `docker compose up -d` + wait for health.
`niuu doctor` re-runs the host checks on demand. GPU and NVIDIA runtime are
detected and reported, never required (`docker.require_gpu` opts in).

Implemented on this branch:

- `DockerConfig` / `DockerVllmConfig` in `src/cli/config.py` (data dir,
  images, bind host, vLLM knobs).
- `src/cli/services/docker_host.py` — preflight (docker daemon, compose v2,
  NVIDIA runtime, GPU, data dir, disk, ports, registry reachability, git) and
  `HostFacts` for the wizard's welcome step.
- `src/cli/services/compose_bundle.py` — renderer, one-time secrets
  (postgres password, Fernet credential key), health wait.
- `src/cli/commands/stack.py` + top-level `niuu up|down|status|doctor`
  (`niuu platform init` offers `docker` as choice 4).
- `scripts/install.sh` — the `curl | sh` installer (release binary +
  checksum, then `niuu up --mode docker`).
- `RootServer` creates the per-service databases and applies migrations
  against an external Postgres (`NIUU_DATABASE_MODE=external` +
  `DATABASE__*`), which previously only happened for the embedded one.
- `volundr.adapters.outbound.docker_container.DockerContainerPodManager` —
  subclasses the local-process manager, runs each session as a sibling
  container on the compose network, never leaks the platform environment
  into the sandbox, routes the session proxy by container name.
- `containers/niuu/Dockerfile` — bakes the web UI into the image and installs
  git.

## Wizard steps and what each one maps to

| Step | Backs onto | Gap to build |
|---|---|---|
| Welcome | `host-facts.json` written by `niuu up` | `GET /api/v1/setup/system` |
| System check | `docker_host.py` checks | expose over HTTP; "fix for me" runs the remedy |
| Local model | Bifrost `ManagedModelConfig.vram_required`, provider map, `docker.vllm` | curated model list, vLLM pull progress, hot re-render of the bundle |
| AI providers | `rest_codex_credentials.py` (device code), `provider-logins.md` Claude OAuth, `CredentialStorePort` | a **host/local login runner** (today's runner is a K8s Job); Bifrost keys move from env/file into the credential store |
| Git | `git.github.instances[]`, `rest_integrations.py`, `local_mounts` | GitHub App install flow (needs a published app), test-connection endpoint |
| Tickets | `integration_connections`, `tracker_factory.py`, `niuu/adapters/linear.py` | Linear OAuth (today: pasted token) |
| Runtime & access | `pod_manager.adapter`, `local_mounts`, auth adapters, `docker.bind_host` | access-mode presets |
| Launch | `ServiceManager`, migrations, `bifrost` registration | setup-progress SSE stream; "verify a session can start" smoke test |
| Ready | existing Forge / Ting / Ravn UIs | three guided walkthroughs driven inside the product |

Everything the wizard writes lands in the same places Settings already
reads (`config.yaml` under the data dir, `integration_connections`, the
credential store). The wizard is a front-door, never a second config system.

## Rules the wizard must honour

- **No fallbacks.** A provider that cannot be reached is shown as failed
  with the remedy; the default model is never silently swapped.
- **Credentials encrypted at rest.** `FileCredentialStore` takes a Fernet
  `encryption_key`; `niuu up` generates it once into `secrets.env` and the
  Ready screen tells the user to back it up. OAuth / device-code first,
  pasted tokens second.
- **Sign-in off by default** on the LAN with a visible warning and a later
  path to OIDC in Settings → Access. Never a custom auth layer.
- **Config-first.** Every knob the wizard sets is a Settings field; nothing
  reads `os.environ` directly.

## Suggested order

1. `docker` mode + compose bundle + `niuu up` / `niuu doctor` — done.
2. Setup API: system, providers, git, tracker, runtime, launch-progress.
3. Wizard UI in `web-next` as a `plugin-setup` (rail + step layout from the
   mockup; reuse `Field`, `Input`, `Select`, LaunchWizard's step indicator).
4. Local model: curated list, vLLM container, pull progress.
5. Host login runner for Claude / Codex; Linear OAuth; GitHub App.
6. Ready screen walkthroughs.

## Open questions

1. Model list: only what fits, or everything with a "does not fit" state?
2. Should Launch start a resident by default, or leave that to tutorial 3?
3. Is the "host process" runtime worth offering on Spark at all?
4. GitHub App vs PAT as the first-class path (the App needs publishing).
