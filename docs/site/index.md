---
template: home.html
hide:
  - navigation
  - toc
# Landing page copy. docs/overrides/home.html renders this; change the words here.
# Links: `doc` is a path inside the docs, `href` is an anchor or an external URL.
# Structure and reasoning: docs/plans/homepage-narrative.md.
home:
  title: NIUU — A place where intelligent systems do real work
  nav:
    - label: Sessions
      href: "#sessions"
    - label: Workflows
      href: "#workflows"
    - label: Environment
      href: "#environment"
    - label: In practice
      href: "#practice"
    - label: Trust
      href: "#trust"
    - label: Products
      href: "#products"
  docs:
    label: Docs
    doc: get-started/introduction/
  action:
    label: GitHub ↗
    href: https://github.com/niuulabs/niuu
  hero:
    label: An operating environment for intelligent work
    title:
      - A place where
      - intelligent systems
      - do real work.
    body: >-
      NIUU is the operating environment for autonomous work. Agents collaborate
      with each other and with people, build on what they learn, and evolve
      together.
    actions:
      - label: See how it works
        arrow: →
        style: primary
        href: "#idea"
      - label: Get started
        arrow: →
        style: secondary
        href: "#start"
    trust: Open source under Apache-2.0. Self-hosted, with your choice of models.
    features:
      - Autonomous
      - Collaborative
      - Persistent
      - Observable
      - Scalable
      - Self-hosted
    tagline: Continuity / not just a session
  idea:
    label: The idea
    title:
      - Work doesn't start over.
      - It evolves.
    body: An AI conversation starts empty and ends with a transcript. NIUU gives work somewhere to continue.
    body_more: A model changes. A person steps away. The work is still there, and further along.
    visual: What carries forward
    kept:
      - title: Responsibility
        body: Agents keep their mandate from day to day.
      - title: Decisions
        body: Decisions stay on record.
      - title: Knowledge
        body: Memory keeps what is known, and why.
      - title: Capability
        body: A tool built once can be reused.
  sessions:
    label: Where it starts
    title:
      - You start with a session.
      - Then you want more.
    body: Chat, diffs, files and a full trace in one view, with the agent you already use.
    body_more: But prompting only gets you so far.
    image: chat.webp
    height: 1120
    alt: A session chat where an agent reproduces a failing export, fixes it and points to the diff for review
    caption: A session in Forge, with a sample conversation.
    stages:
      - name: Session
        title: You and one agent
        body: You steer every turn. The session holds its chat, diffs, files and trace.
      - name: Workflow
        title: A deterministic process
        body: The path is fixed in advance, with stages, reviews and gates. Agents do the work inside it.
      - name: Team
        title: Agents working together
        body: Agents hand work to each other, review it and send it back when it falls short.
      - name: Resident
        title: Work that reacts
        body: A lasting responsibility, and no prompt needed. A schedule, an event, a ticket, a message or a reading from a machine can set it off.
    note: Nothing is replaced. You can always step in and chat.
  workflows:
    label: Why NIUU
    title:
      - Agents that work for you,
      - not agents you have to drive.
    body: Steering every session doesn't scale. Giving agents access to everything isn't safe. Workflows solve both.
    image: approve.webp
    height: 1125
    alt: Ticket Planning Flow, with a planner, a reviewer and a publisher in stages and two You approve gates
    caption: A shipped workflow. It stops at the diamonds for you.
    points:
      - title: A defined process
        body: Stages, reviews and gates are set before it runs.
      - title: One job each
        body: Every agent has a clear responsibility.
      - title: The right tools and model
        body: A reviewer can read the code, not change it.
      - title: They reach out
        body: To you when they need a decision, and to each other.
  environment:
    label: The environment
    title:
      - People and agents share
      - a place to work.
    body: A Workspace holds the goal, the work in motion and what is known. A resident keeps it running.
    image: workspaces.webp
    height: 1050
    alt: The Workspaces home with three environments, each kept by a resident, and a list of decisions that need you
    points:
      - title: Start with a sentence
        body: Describe the job, and setup fills in what it can.
      - title: Set the limits
        body: Choose how far it may go.
      - title: Not only code
        body: Clusters, codebases, even a workshop of 3D printers.
    note: Shown with sample data.
  practice:
    label: In practice
    title: One responsibility, followed through.
    body: One job, from start to finish. Screens use sample data.
    story:
      - title: A responsibility is set
        body: Keep the payments cluster healthy.
        image: realm.webp
        height: 989
        alt: A Workspace showing what its resident did, with some actions taken alone and others waiting for approval, and its trust levels
      - title: Something changes
        body: A signal arrives, and the resident judges what to do.
        image: activity.webp
        height: 623
        alt: Resident activity with recent decisions and the operational state of a cluster, an inbox and a print workshop
      - title: It brings in help
        body: Missing a tool? It commissions a team to build one.
        image: toolbuilder.webp
        height: 575
        alt: The Tool and Skill Builder workflow as a graph of framing, building, reviewing and publishing stages
      - title: You decide what is yours to decide
        body: When it escalates, you get the request and the reason.
        image: inbox.webp
        height: 590
        alt: The Review inbox with a high-risk action draft and fields to approve or reject with a recorded reason
      - title: What it learned is kept
        body: A confirmed diagnosis goes into Memory, with its evidence.
        image: memory.webp
        height: 1007
        alt: A Memory page with What we believe, the pages around it and an append-only Evidence list
      - title: The next problem starts further along
        body: Peers can adopt tested tools, and duplicates are retired.
        image: resident.webp
        height: 846
        alt: A resident page reading Simpler now, 2 duplicate tools retired, with its most used tools and a tool picked up from another resident
  trust:
    label: Visibility and trust
    title:
      - Capable on its own.
      - Accountable to you.
    body: Autonomy only works if you can see it and contain it.
    image: telemetry.webp
    height: 1063
    alt: The Telemetry tab of a session, with a timeline of turns, tool calls, a reviewer and a human gate
    caption: Every turn, tool call and gate on one timeline. Sample data.
    pillars:
      - title: Tracing
        body: See every step. Export to any OpenTelemetry backend.
      - title: Security
        body: Your identity provider, with scoped, short-lived credentials.
      - title: Multi-tenancy
        body: One installation, several teams.
  products:
    label: One environment
    title: Six parts of one environment.
    body: They work as one system, not six separate products.
    family:
      - name: Platform
        icon: platform
        line: Your environment
        body: Connect your machines into one environment.
        doc: concepts/platform-model/
      - name: Workspaces
        icon: workspaces
        line: Where work happens
        body: Give ongoing work a home and clear limits.
        doc: concepts/sessions-and-workspaces/
      - name: Agents
        icon: agents
        line: Who does the work
        body: Give agents roles, tools and responsibilities.
        doc: concepts/agents-and-personas/
      - name: Forge
        icon: forge
        line: Build and execute
        body: Run agents, and build the tools they lack.
        doc: concepts/sessions-and-workspaces/
      - name: Memory
        icon: memory
        line: What is known
        body: Keep what is known, with its evidence.
        doc: concepts/memory-and-knowledge/
      - name: Observatory
        icon: observatory
        line: What is happening
        body: See everything you connect on one live map.
        doc: get-started/shared-discovery-and-topology/
  direction:
    label: Direction
    title: Toward an operating system for agents.
    body: Goals, agents, capabilities and memory in one model. That is where we are heading, not what ships today.
    link:
      label: Read the direction
      doc: concepts/operating-system/
  infrastructure:
    label: Built on what you have
    title: We extend the best tools. We don't rebuild them.
    body: Keep your agents, models, tracker and infrastructure. NIUU builds on them. Open source and self-hosted.
    body_more: Start on one machine. Grow to many clusters, run as one environment.
    groups:
      - name: Agent harnesses
        entries: Claude Code, OpenAI Codex, OpenCode, Pi, OpenClaw, Hermes, NemoClaw, xAI Grok Build, Meta Muse Code, DeepSeek Harness
      - name: Models
        entries: Anthropic, OpenAI, DeepSeek, xAI, Meta, Ollama and OpenAI-compatible endpoints
      - name: Ticketing and code
        entries: GitHub, GitLab, Linear, Jira
      - name: Channels and protocols
        entries: Telegram, MCP, A2A
      - name: Identity
        entries: Any OIDC provider, such as Keycloak, Entra ID or Okta
      - name: Observability
        entries: Any OpenTelemetry backend, such as Tempo, Jaeger, Grafana or Honeycomb
      - name: Runs on
        entries: Local processes, Docker, OpenShell, VMs, bare metal, DGX Spark, Kubernetes
    note: Not every combination has been tested.
    link:
      label: What has been verified
      doc: operations/quickstart-verification/
  start:
    label: Get started
    title: Try it on your own machine.
    body: >-
      The shortest way in today is a local install and a coding agent. The quick
      start takes you to one verified result. From there, add workflows, Memory
      and residents as your work calls for them.
    needs: >-
      You need Git, a C toolchain, uv, Node.js, pnpm, and Claude Code with a
      working login.
    needs_note: This mode has no login, so keep NIUU on 127.0.0.1, its default.
    code_title: From source
    code: |-
      git clone https://github.com/niuulabs/niuu.git
      cd niuu
      uv sync --python 3.12 --extra dev
      make build-postgres build-web
      uv run niuu platform init
      uv run niuu platform up
    code_note: The build compiles PostgreSQL and the web UI, so give it a while. At the init prompt, press Enter for mini mode.
    actions:
      - label: Follow the quick start
        arrow: →
        style: primary
        doc: get-started/first-local-stack/
      - label: Other ways to install
        arrow: →
        style: secondary
        doc: get-started/install/
  closing:
    title:
      - Give intelligent systems
      - a place to do real work.
    actions:
      - label: Read what NIUU is
        arrow: →
        style: primary
        doc: get-started/introduction/
      - label: See the code
        arrow: ↗
        style: secondary
        href: https://github.com/niuulabs/niuu
    map:
      - title: Start here
        body: What NIUU is, how to install it, and a first result.
        doc: get-started/introduction/
      - title: Understand NIUU
        body: Agents, residents, workflows, memory and how they fit.
        doc: concepts/platform-model/
      - title: Operate
        body: Run it on Docker or Kubernetes, and keep it healthy.
        doc: operations/docker-mode/
      - title: Reference
        body: Commands, configuration and APIs.
        doc: reference/cli-niuu/
  footer:
    - NIUU · Apache-2.0
    - A place where intelligent systems do real work.
---
