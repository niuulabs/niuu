---
hide:
  - navigation
  - toc
---

<div class="landing-page" markdown>

<nav class="landing-nav" aria-label="Landing navigation">
  <a class="landing-brand" href="./" aria-label="Niuu home">
    <img src="images/logo-knot.svg" alt="" />
    <span>Niuu</span>
  </a>
  <div class="landing-nav-links">
    <a href="get-started/introduction/">Docs</a>
    <a href="https://github.com/niuulabs/volundr">GitHub</a>
    <a class="landing-nav-cta" href="get-started/install/">Get started</a>
  </div>
</nav>

<section class="landing-hero" markdown>

<p class="landing-kicker">Self-hosted AI operations</p>

# Own the workspace, the agents, and the memory.

Niuu is a self-hosted platform for AI workspaces, coordinated teams, always-on assistants, shared knowledge, and local or cloud model control. Run it on a laptop while you experiment, then take the same operating model to Kubernetes when the work needs a real platform.

<div class="landing-actions">
  <a class="primary" href="get-started/install/">Get started</a>
  <a href="get-started/introduction/">Read the docs</a>
  <a href="https://github.com/niuulabs/volundr">View repository</a>
</div>

</section>

<section class="landing-gallery" aria-labelledby="gallery-title" markdown>

<div class="landing-section-heading" markdown>

## See the platform in motion

Fresh captures from the local Niuu development stack.

</div>

<div class="gallery-rail" markdown>

<figure id="gallery-forge">
  <img src="images/landing/landing-forge.png" alt="Völundr Forge showing active pods, token usage, quick launch presets, and fleet activity" />
  <figcaption>
    <strong>Launch workspaces.</strong>
    Völundr turns repos, presets, and runtime targets into isolated AI workspaces.
  </figcaption>
</figure>

<figure id="gallery-session">
  <img src="images/landing/landing-session-review.png" alt="Völundr session review view with session list and diff viewer selected" />
  <figcaption>
    <strong>Review the work.</strong>
    Sessions keep chat, terminal, files, chronicles, telemetry, logs, and diffs together.
  </figcaption>
</figure>

<figure id="gallery-workflows">
  <img src="images/landing/landing-workflows.png" alt="Ting workflow builder showing a code and review flow graph" />
  <figcaption>
    <strong>Coordinate teams.</strong>
    Ting models reusable workflows, gates, resources, and multi-agent runs.
  </figcaption>
</figure>

<figure id="gallery-memory">
  <img src="images/landing/landing-memory.png" alt="Mímir knowledge overview showing mounts, sources, wardens, lint status, and activity" />
  <figcaption>
    <strong>Keep knowledge alive.</strong>
    Mímir gives operators and assistants a shared memory system with sources, mounts, and wardens.
  </figcaption>
</figure>

<figure id="gallery-models">
  <img src="images/landing/landing-models.png" alt="Bifröst model control plane with model, provider, usage, and cache health cards" />
  <figcaption>
    <strong>Control model routing.</strong>
    Bifröst presents local and cloud model availability through one control plane.
  </figcaption>
</figure>

<figure id="gallery-topology">
  <img src="images/landing/landing-observatory.png" alt="Observatory topology view showing services, runs, agents, and realms" />
  <figcaption>
    <strong>Watch the platform.</strong>
    Observatory shows live topology, services, agents, runs, and event flow.
  </figcaption>
</figure>

</div>

<div class="gallery-dots" aria-label="Gallery shortcuts">
  <a href="#gallery-forge">Forge</a>
  <a href="#gallery-session">Review</a>
  <a href="#gallery-workflows">Workflows</a>
  <a href="#gallery-memory">Memory</a>
  <a href="#gallery-models">Models</a>
  <a href="#gallery-topology">Topology</a>
</div>

</section>

<section class="landing-steps" markdown>

<div class="landing-section-heading" markdown>

## How it works

</div>

<div class="step-grid" markdown>

<div class="step-card" markdown>

### 1. Run it where you work

Start the local stack for development or deploy the platform components to Kubernetes when you need shared infrastructure.

</div>

<div class="step-card" markdown>

### 2. Launch an AI workspace

Create a session with a repo, preset, model, tools, workspace storage, and an operator-visible lifecycle.

</div>

<div class="step-card" markdown>

### 3. Coordinate teams and workflows

Use Ting and Ravn to route work through specialists, gates, review loops, resident assistants, and human escalation.

</div>

<div class="step-card" markdown>

### 4. Keep the knowledge

Capture chronicles, memory, research, sources, and long-lived assistant context so the platform learns from the work.

</div>

</div>

</section>

<section class="landing-platform" markdown>

<div class="landing-section-heading" markdown>

## Platform map

Niuu is built as cooperating services rather than one opaque agent runner.

</div>

<div class="platform-grid" markdown>

<div markdown><strong>Völundr</strong><span>AI workspaces, session lifecycle, git review, and remote dev pods.</span></div>
<div markdown><strong>Ting</strong><span>Workflow orchestration, dispatch queues, stages, gates, and runs.</span></div>
<div markdown><strong>Ravn</strong><span>Personas, assistants, triggers, sessions, budgets, and resident behaviors.</span></div>
<div markdown><strong>Mímir</strong><span>Shared knowledge, sources, search, wardens, and memory health.</span></div>
<div markdown><strong>Bifröst</strong><span>Model catalog, provider health, aliases, routing, and usage telemetry.</span></div>
<div markdown><strong>Guild</strong><span>Runtime registry for local and remote service instances.</span></div>
<div markdown><strong>Skuld</strong><span>Live session broker for chat, terminal, tools, and workspace events.</span></div>
<div markdown><strong>Observatory</strong><span>Topology, registry, service visibility, and live event context.</span></div>
<div markdown><strong>Sleipnir</strong><span>Transport layer across local processes, NATS, NNG, RabbitMQ, and more.</span></div>

</div>

</section>

<section class="landing-faq" markdown>

<div class="landing-section-heading" markdown>

## Frequently asked questions

</div>

<details open>
  <summary>Is Niuu self-hosted?</summary>
  <p>Yes. Niuu is designed to run on your machine, in your lab, or in your Kubernetes environment. You choose which services are local, shared, or remote.</p>
</details>

<details>
  <summary>Does work stay local?</summary>
  <p>The platform is self-hosted, but model traffic depends on the providers you configure. You can route to local models through Ollama-style providers or to cloud providers when your workflow allows it.</p>
</details>

<details>
  <summary>Can it run on Kubernetes?</summary>
  <p>Yes. The repository includes Helm charts for the Niuu umbrella deployment and individual services. The local stack is the quickest path for development.</p>
</details>

<details>
  <summary>What permissions do agents have?</summary>
  <p>Agents run inside the workspaces and infrastructure you configure. Treat those workspaces as powerful automation environments: scope credentials, review changes, and use the security docs before production use.</p>
</details>

<details>
  <summary>How mature is the project?</summary>
  <p>Niuu is under active development. The docs focus on what operators can run and reason about today, while deeper legacy material remains archived until it is rewritten around the current platform.</p>
</details>

</section>

<footer class="landing-footer">
  <div>
    <strong>Niuu</strong>
    <span>Self-hosted AI workspaces, teams, assistants, memory, and model operations.</span>
  </div>
  <nav aria-label="Footer navigation">
    <a href="get-started/introduction/">Docs</a>
    <a href="get-started/install/">Install</a>
    <a href="reference/configuration/">Configuration</a>
    <a href="operations/kubernetes-deployment/">Deployment</a>
    <a href="https://github.com/niuulabs/volundr">GitHub</a>
  </nav>
</footer>

</div>
