# From Small To Autonomous

Niuu is easier to learn as a ladder.

Do not start by memorizing every service name. Start with one local workspace,
then add capabilities when you need them.

## 1. One local platform

Run:

```bash
niuu platform up
```

Use this when you want the browser UI, local APIs, and a place to launch
workspace sessions.

You should understand:

- where the web UI is
- how to stop the stack
- how to check platform status

Detailed walkthrough: [One local platform](one-local-platform.md).

## 2. One workspace session

Launch a session from the Völundr area of the UI.

Use this when you want an assistant to work in a repo with visible chat,
terminal, files, diffs, logs, and telemetry.

You should understand:

- a session is the unit of live work
- the workspace is the assistant's runtime boundary
- the diff is the review boundary

Detailed walkthrough: [One workspace session](one-workspace-session.md).

## 3. Model routing

Add model configuration when you want Niuu to choose between local and cloud
models consistently.

This is where Bifröst becomes useful. Treat it as the model control plane:
providers, aliases, health, routing, and usage.

You should understand:

- which providers are allowed
- which aliases map to which models
- whether work should stay local or use cloud providers

Detailed walkthrough: [Model routing](model-routing-step.md).

## 4. Durable memory

Add memory when the result should survive a single session.

This is where Mímir becomes useful. Treat it as shared knowledge: sources,
pages, research outputs, curated memory, and wardens.

You should understand:

- what belongs in shared memory
- what should stay out, especially secrets
- how assistants read from and write to knowledge

Detailed walkthrough: [Durable memory](durable-memory.md).

## 5. Workflows and teams

Add workflows when work needs more structure than one assistant in one
workspace.

This is where Ting becomes useful. Treat it as staged coordination: workflow
definitions, gates, runs, retries, and multi-role work.

You should understand:

- when a task needs a workflow instead of one session
- where human approval belongs
- how sessions remain the place where concrete work happens

Detailed walkthrough: [Workflows and teams](workflows-and-teams-step.md).

## 6. Direct and resident assistants

Install `ravn` when you want to run an assistant directly:

```bash
ravn run --config ~/.ravn/config.yaml
```

Then move to daemon mode when the assistant should stay alive:

```bash
ravn daemon --config ~/.ravn/config.yaml --persona autonomous-agent
```

This is where personas, triggers, wakefulness, dream cycles, wardens, and trust
rules become important.

You should understand:

- what the assistant is allowed to do without approval
- where its state and memory live
- how to stop it and inspect its work

Detailed walkthrough: [Direct and resident assistants](direct-and-resident-assistants.md).

## 7. Shared discovery and topology

Add shared discovery when you have more than one platform instance, cluster, or
assistant runtime.

This is where Guild and Observatory become useful:

- Guild is the runtime registry.
- Observatory shows topology, service visibility, agents, runs, and events.

You should understand:

- which instance owns a session or service
- which cluster or host it runs in
- how the UI discovers it

Detailed walkthrough: [Shared discovery and topology](shared-discovery-and-topology.md).

## 8. Kubernetes and GitOps

Move to Kubernetes when local processes are no longer enough.

Use this when you need durable services, shared ingress, managed secrets,
resource limits, and long-running assistants that survive laptop restarts.

You should understand:

- Helm deploys the platform pieces.
- GitOps owns managed cluster resources.
- Secrets should flow through the configured secret manager.
- Service-to-service traffic should use the platform routing path, not ad hoc
  localhost shortcuts.

Detailed walkthrough: [Kubernetes and GitOps](kubernetes-and-gitops-step.md).

## Keep the ladder visible

When you are unsure what to read next, ask which step you are on:

| Step | You need |
| --- | --- |
| Local platform | `niuu` |
| Direct assistant | `ravn` |
| Model routing | Bifröst |
| Durable knowledge | Mímir |
| Structured work | Ting |
| Resident behavior | Ravn daemon |
| Multi-instance discovery | Guild and Observatory |
| Production operation | Helm, Kubernetes, GitOps |
