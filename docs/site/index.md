---
hide:
  - navigation
  - toc
---

<div class="niuu-hero" markdown>

![Niuu knot](images/logo-knot.svg){ .hero-logo width="84" }

# Niuu

<p class="tagline">The self-hosted platform for AI workspaces, automated workflows, always-on assistants, shared knowledge, and local or cloud AI.</p>

<div class="hero-pills">
  <span>AI workspaces</span>
  <span>Automated workflows</span>
  <span>Always-on assistants</span>
  <span>Shared knowledge</span>
  <span>Local &amp; cloud AI</span>
  <span>Laptop to Kubernetes</span>
</div>

<div class="hero-buttons">
  <a href="#what-niuu-is" class="primary">Start here</a>
  <a href="#platform-map" class="secondary">Platform map</a>
  <a href="https://github.com/niuulabs/volundr" class="secondary">Repository</a>
</div>

</div>

## What Niuu Is

Niuu brings four operating modes into one platform you can run on your own machine, on Kubernetes, or inside your own infrastructure:

- **AI workspaces** for hands-on work with an assistant in a live coding environment
- **Automated workflows** for implementation, review, security passes, and research
- **Always-on assistants** that watch for changes, curate knowledge, and keep working over time
- **Shared model control** that decides what models exist, how they route, and how they execute

The result is a self-hosted system where you can move smoothly between direct operator work, autonomous execution, durable memory, and local or third-party models without handing control to an external vendor platform.

## Why It Exists

<div class="feature-grid" markdown>

<div class="feature" markdown>

### One platform, not disconnected tools

Workspaces, workflows, assistants, and model routing all live in one system with shared auth, shared memory, shared events, and one operator experience.

</div>

<div class="feature" markdown>

### Operators stay in control

Humans can inspect, interrupt, redirect, approve, and steer the platform at every layer instead of treating agents like opaque background jobs.

</div>

<div class="feature" markdown>

### Memory is first-class

Shared knowledge is not an afterthought. It is where research, debate, durable documentation, and long-lived assistant curation all accumulate.

</div>

<div class="feature" markdown>

### Multi-model by design

The platform owns the model catalog and routing truth so workflows, workspaces, and assistants can use different models without each subsystem inventing its own rules.

</div>

</div>

## Platform Map

| Surface | What it is for | Primary services |
|---|---|---|
| **Live workspaces** | Live coding, terminal work, review, diffs, and operator-guided execution | `volundr`, `skuld`, `web-next` |
| **Automated workflows** | Multi-stage autonomous work, review loops, security passes, and research councils | `tyr`, `ravn`, `volundr` |
| **Always-on assistants** | Long-lived agents that watch sources, curate knowledge, run dream cycles, and stay available for operator interaction | `ravn`, `mimir`, `web-next` |
| **Shared knowledge** | Scratch boards, research pages, postmortems, ingest, search, and durable knowledge | `mimir`, `ravn` |
| **Model operations** | Model catalog, aliases, providers, runtime mapping, routing policy, and health | `bifrost` |
| **Realtime collaboration** | Live rooms, chat transport, event propagation, and workflow signaling | `skuld`, `sleipnir` |

## What Niuu Does Today

<div class="feature-grid" markdown>

<div class="feature" markdown>

### Live AI workspaces

Launch and manage coding workspaces with repos, terminals, diffs, and live conversation with an assistant.

</div>

<div class="feature" markdown>

### Workflow orchestration

Dispatch typed workflows and raids, including review loops, security passes, and mixed-model research councils.

</div>

<div class="feature" markdown>

### Agent runtime

Provide the agent runtime, personas, tools, drive loops, triggers, and Warden machinery for long-lived intelligence.

</div>

<div class="feature" markdown>

### Shared knowledge

Store raw ingest, scratch discussion, curated memory, research outputs, postmortems, and Warden-maintained knowledge.

</div>

<div class="feature" markdown>

### Model control

Own the model catalog and provider/routing truth so the rest of the platform can ask for a model without duplicating control-plane logic.

</div>

<div class="feature" markdown>

### Always-on assistants

Run as long-lived assistant daemons that can watch knowledge sources, trigger curation, revisit stale documents, and expose a live operator console.

</div>

</div>

## Operator Journeys

### 1. Pair directly with AI

Use a live workspace when you want a terminal, diffs, and a direct room with the assistant while keeping everything on your own infrastructure.

### 2. Launch autonomous work

Use an automated workflow when the work should run as a repeatable flow: coding, review, security, postmortems, or research with multiple models.

### 3. Keep knowledge alive

Use always-on assistants and shared knowledge when the work should keep happening after the workspace closes: source watching, curation, reflection, document refresh, and operator-steerable long-lived agents.
