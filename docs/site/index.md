---
hide:
  - navigation
  - toc
---

<div class="niuu-hero" markdown>

![Niuu knot](images/logo-knot.svg){ .hero-logo width="84" }

# Niuu

<p class="tagline">The self-hosted platform for AI workspaces, custom AI teams, always-on assistants, shared knowledge, and local or cloud AI.</p>

<div class="hero-pills">
  <span>AI workspaces</span>
  <span>Custom AI teams</span>
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

- **AI workspaces** for hands-on work with one assistant or several assistants working together in a live coding environment
- **Custom AI teams** you can design, launch, and steer for coding, research, operations, and your own multi-step flows
- **Always-on assistants** that monitor sources, revisit knowledge, refresh documents, and stay available for live operator guidance
- **Local and cloud AI** managed in one place, including what models are available, where they run, and when they are used

The result is a self-hosted system where you can move smoothly between direct operator work, autonomous execution, durable memory, and local or third-party models without handing control to an external vendor platform.

## In the UI

<p align="center">
  <img src="images/ui-ting-workflows.png" alt="Ting workflow builder in Niuu" width="100%">
</p>

<p align="center">
  <img src="images/ui-guild-instances.png" alt="Guild instance registry" width="49%">
  <img src="images/ui-niuu-home.png" alt="Volundr forge dashboard" width="49%">
</p>

## Why It Exists

<div class="feature-grid" markdown>

<div class="feature" markdown>

### One platform, not disconnected tools

Workspaces, AI teams, assistants, and model routing all live in one system with shared auth, shared memory, shared events, and one operator experience.

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

### Local and cloud AI from one place

Choose which models are available, whether work stays local or uses third-party providers, and how the rest of the platform can use them without every tool inventing its own rules.

</div>

</div>

## How Niuu Operates

![Niuu architecture](images/niuu-architecture.svg){ .niuu-architecture-diagram }

<div class="arch-caption" markdown>

- **Volundr** is where humans work directly.
- **Ting** coordinates specialist teams and review loops.
- **Ravn** is the harness for one assistant or a connected team of assistants.
- **Mimir** keeps shared memory alive.
- **Bifrost** decides what models are available and where they run.

</div>

## What Niuu Lets You Build

<div class="feature-grid" markdown>

<div class="feature" markdown>

### Live AI workspaces

Launch and manage coding workspaces with repos, terminals, diffs, and live conversation with one assistant or several assistants working together.

</div>

<div class="feature" markdown>

### Build and run AI teams

Compose your own specialist teams for, for example, coding, review, security, research, approvals, retries, and whatever stage logic your process needs.

</div>

<div class="feature" markdown>

### Ravn is the harness

Use one runtime layer for personas, tools, triggers, human escalation, live comms, and long-lived assistants, while connecting to models through CLI transports or provider APIs.

</div>

<div class="feature" markdown>

### Shared knowledge

Store raw ingest, scratch discussion, curated memory, research outputs, postmortems, and Warden-maintained knowledge.

</div>

<div class="feature" markdown>

### Manage local and cloud AI

Decide which models are available, which providers back them, whether work stays local or goes to third parties, and how the rest of the platform chooses between them.

</div>

<div class="feature" markdown>

### Run assistants that stay alive

Run assistants that keep working after the interactive session ends: watching sources, refreshing stale documents, curating knowledge, running scheduled reflection, and staying reachable through a live operator console.

</div>

<div class="feature" markdown>

### Human guidance stays in the loop

Let assistants and teams raise help-needed events, send directed messages, pause for operator input, and resume without losing context.

</div>

</div>

## Operator Journeys

### 1. Pair directly with AI

Use a live workspace when you want a terminal, diffs, and a direct room with one assistant or several assistants working together while keeping everything on your own infrastructure.

### 2. Launch coordinated work

Use an AI team when the work should move across multiple specialists, approvals, reviews, or custom stages you define for your own process.

### 3. Keep knowledge alive

Use always-on assistants and shared knowledge when the work should keep happening after the workspace closes: source monitoring, document refresh, ongoing curation, scheduled reflection, and long-lived assistants you can still steer directly.
