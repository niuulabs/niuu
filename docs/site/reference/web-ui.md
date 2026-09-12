# Find your way around the UI

Open the platform root URL, then choose a service from the navigation. A service
can be absent because it is disabled or unavailable in that deployment. The UI
composes the services offered by the host; it does not install missing runtimes.

| Area | Start here for | Inspect next |
| --- | --- | --- |
| Völundr / Forge | Launch a session | Runtime state, chat, files, diffs, logs |
| Ting | Workflows and runs | Active stage, failed attempt, pending gate |
| Mímir | Sources and knowledge | Mount, page evidence, search result, lint |
| Ravn | Agent behavior and instances | Persona, runtime configuration, state |
| Valkyrie | Resident work | Case, requested input, decision, outcome |
| Bifröst | Models and providers | Catalog, provider health, usage |
| Guild | Service instances | Service group, endpoint, target metadata |
| Observatory | Topology | Relationships, ownership, telemetry |
| Settings | Connections and access | Credential scope and integration configuration |

## First launch

On a single-host Docker install the first visit opens `/setup`, a full-screen
wizard that checks the host, offers a local model served by vLLM, connects AI
providers (one pane per provider: a Claude / Codex subscription sign-in run by
the platform, or an API key; Anthropic, OpenAI and xAI), Git hosting and an
issue tracker through the integrations catalog, and lets you choose who can
reach the install. **Apply and open Niuu** on its last step applies the staged
changes (the platform restarts what changed), marks setup complete and opens
`/ready`, which walks through the first things to do (a session in Völundr, a
workflow in Ting, the residents in Ravn). The wizard
stays reachable at `/setup` afterwards. It appears only when the host sets
`NIUU_SETUP_ENABLED`; see [Single-host Docker mode](../operations/docker-mode.md#the-setup-wizard).

## Launching work

In Völundr, open **Forge → custom launch…**. The wizard separates **Source**,
**Runtime**, and **Confirm**. Start with a blank source to learn the flow; add a
repository once the runtime works. The [quick start](../get-started/first-local-stack.md)
explains every required field with screenshots from an actual launch.

## Inspecting work

Open the session to see the interfaces its runtime exposes. A missing or failed
chat connection is distinct from a failed process launch. If the session says
running but no answer arrives, inspect logs and verify provider authentication.

If a direct URL shows JSON, return to the platform root and navigate through the
UI. Service APIs and browser routes can share similar names; an API response is
not the intended entry page.
