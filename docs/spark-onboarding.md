# Niuu on a DGX Spark: one command, then a wizard

Status: proposal + mockup (2026-09-12). Mockup source: `docs/mockups/spark-onboarding/`
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

## What runs (Spark "appliance" mode)

Docker-first. The Spark ships with Docker and the NVIDIA Container Toolkit,
and it keeps the host clean, so a compose bundle is the right unit:

| Container | Role | Exists today |
|---|---|---|
| `postgres` | PG 17 + pgvector, one instance, per-service databases | `service_databases.py` bootstrap SQL, `charts/` CNPG image |
| `niuu` | Volundr + Ting + Bifrost + Skuld + web in the shared root server on `:8080` | `containers/niuu/Dockerfile` (mini mode host wiring) |
| `openshell` | session sandbox gateway; one sandbox per session with GPU passthrough | `containers/openshell`, `OpenShellGatewayPodManager` |
| `vllm` | optional local model, started only if the wizard chose one | not yet |

A new CLI mode `spark` (alongside `mini` / `openshell` / `cluster`) selects
this composition. `niuu up` in that mode = preflight + `docker compose up`
with a generated env file. `niuu doctor` re-runs preflight on demand.

## Wizard steps and what each one maps to

| Step | Backs onto | Gap to build |
|---|---|---|
| Welcome | hardware facts from preflight | `GET /api/v1/setup/system` |
| System check | `src/cli/services/preflight.py` | expose over HTTP; add Docker group, NVIDIA toolkit, GPU memory checks; "fix for me" runs the remedy |
| Local model | Bifrost `ManagedModelConfig.vram_required`, provider map | GPU detection (`nvidia-smi` / NVML), a curated Spark model list, vLLM container lifecycle + pull progress |
| AI providers | `rest_codex_credentials.py` (device code), `provider-logins.md` Claude OAuth, `CredentialStorePort` | a **host/local login runner** (today's runner is a K8s Job); Bifrost keys move from env/file into the credential store |
| Git | `git.github.instances[]`, `rest_integrations.py`, `local_mounts` | GitHub App install flow (needs a published app), test-connection endpoint |
| Tickets | `integration_connections`, `tracker_factory.py`, `niuu/adapters/linear.py` | Linear OAuth (today: pasted token) |
| Runtime & access | `pod_manager.adapter`, `local_mounts`, auth adapters | Docker-container pod manager for the middle option; access-mode presets |
| Launch | `ServiceManager`, migrations, `bifrost` registration | setup-progress SSE stream; "verify a session can start" smoke test |
| Ready | existing Forge / Ting / Ravn UIs | three guided walkthroughs driven inside the product |

Everything the wizard writes lands in the same places Settings already
reads (`~/.niuu/config.yaml` → `/etc/niuu/config.yaml` in the container,
`integration_connections`, the credential store). The wizard is a
front-door, never a second config system.

## Rules the wizard must honour

- **No fallbacks.** A provider that cannot be reached is shown as failed
  with the remedy; the default model is never silently swapped.
- **Credentials encrypted at rest.** `FileCredentialStore` already takes a
  Fernet `encryption_key`; the wizard generates it on first run and the
  Ready screen tells the user to back it up. OAuth / device-code first,
  pasted tokens second.
- **Sign-in off by default** on the LAN with a visible warning and a later
  path to OIDC in Settings → Access. Never a custom auth layer.
- **Config-first.** Every knob the wizard sets is a Settings field; nothing
  reads `os.environ` directly.

## Suggested order

1. `spark` mode + compose bundle + `niuu up` / `niuu doctor` (no UI yet).
2. Setup API: system, providers, git, tracker, runtime, launch-progress.
3. Wizard UI in `web-next` as a `plugin-setup` (rail + step layout from the
   mockup; reuse `Field`, `Input`, `Select`, LaunchWizard's step indicator).
4. Local model: GPU detection, curated list, vLLM container, pull progress.
5. Host login runner for Claude / Codex; Linear OAuth; GitHub App.
6. Ready screen walkthroughs.

## Open questions

1. Model list: only what fits, or everything with a "does not fit" state?
2. Should Launch start a resident by default, or leave that to tutorial 3?
3. Is the "host process" runtime worth offering on Spark at all?
4. GitHub App vs PAT as the first-class path (the App needs publishing).
