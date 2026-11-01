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
    - label: At work
      href: "#at-work"
    - label: Residents
      href: "#evolution"
    - label: Environment
      href: "#environment"
    - label: Use cases
      href: "#use-cases"
    - label: For teams
      href: "#teams"
    - label: Get started
      href: "#start"
  docs:
    label: Docs
    doc: get-started/introduction/
  action:
    label: GitHub ↗
    href: https://github.com/niuulabs/niuu
  hero:
    title:
      - A place where
      - intelligent systems
      - do real work.
    body: >-
      NIUU is the operating environment for autonomous work. Agents collaborate
      with each other and with people, build on what they learn, and evolve
      together.
    actions:
      - label: See NIUU at work
        arrow: →
        style: primary
        href: "#at-work"
      - label: Get started
        arrow: →
        style: secondary
        href: "#start"
    trust: Open source under Apache-2.0. Self-hosted, with your choice of models.
  idea:
    label: The idea
    title:
      - Agents that work for you,
      - not agents you have to drive.
    body: Prompting only gets you so far. Steering every session doesn't scale, and giving agents access to everything isn't safe.
    body_more: NIUU gives work a process, clear limits and a place to continue. Agents get on with it, and reach out when they need you.
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
  gallery:
    title: See NIUU at work.
    body: From one agent to a whole environment. Real product interfaces, shown with sample data.
    chapters:
      - id: forge
        name: Forge
        title: Where the work runs.
        body: Forge runs agents on the machine that suits the job. You can see what each one is doing right now, and step into its session at any time.
        layout: right
        link:
          label: Sessions and workspaces
          doc: concepts/sessions-and-workspaces/
        views:
          - name: Running now
            image: forge
            width: 2000
            height: 1135
            caption: What is running, what each agent is doing and how loaded each machine is.
            alt: The Forge overview with active pods, tokens and cost, a list of in-flight sessions and the load on each host
          - name: Session
            image: session
            width: 1600
            height: 1120
            caption: Inside one session. Sample conversation.
            alt: A session chat where an agent reproduces a failing export, fixes it and points to the diff for review
      - id: together
        name: Multi-agent
        title: Work that moves between agents.
        body: Agents share a room with each other and with you. They hand work on, review it and send it back when it falls short.
        layout: left
        link:
          label: Workflows and teams
          doc: concepts/workflows-and-teams/
        views:
          - name: Room
            image: room
            width: 2000
            height: 1774
            caption: A planner, a coder and a reviewer in one room, with you. Sample conversation.
            alt: A room where a planner splits the work, a coder builds it, a reviewer sends it back, and the planner asks a person to decide
      - id: workflows
        name: Workflows
        title: A path the work has to follow.
        body: A workflow fixes the stages, who does each one and where a person decides. Agents do the work inside it, each with its own tools and model.
        layout: wide
        link:
          label: Workflows and teams
          doc: concepts/workflows-and-teams/
        views:
          - name: Workflow
            image: workflow
            width: 1339
            height: 470
            caption: A workflow that ships with NIUU. It stops at the diamonds for you.
            alt: Ticket Planning Flow as a row of stages for drafting, reviewing and recording a plan, with two You approve gates
      - id: tickets
        name: Tickets
        title: Your tickets, run by workflows.
        body: Tickets stay in your tracker and appear on a board whose columns come from your workflows. Move a ticket in and its workflow starts. It stops at the gates where you decide, and asking for changes continues the same run.
        layout: right
        link:
          label: Workflows and teams
          doc: concepts/workflows-and-teams/
        views:
          - name: Board
            image: board
            width: 2000
            height: 1046
            caption: Two workflows on one board, and a coordinator on every ticket in motion.
            alt: A project board with columns from Ticket Planning Flow and Reviewed Delivery Flow, with tickets running or waiting for a person
          - name: Gate
            image: gate
            width: 1884
            height: 1272
            caption: At a gate you approve or ask for changes, and see where each answer leads.
            alt: A ticket at Code review, with its pull request and buttons to request changes or approve and move to Merging
      - id: coordinator
        name: Coordinator
        title: One agent that stays with the ticket.
        body: Every ticket in motion gets its own coordinator. It starts the workflows, hands over from one to the next, hears each gate and tells you what needs you. Ask it anything about the ticket until the ticket closes.
        layout: left
        link:
          label: Workflows and teams
          doc: concepts/workflows-and-teams/
        views:
          - name: Coordinator
            image: coordinator
            width: 1884
            height: 1932
            caption: The ticket's coordinator, right beside the board. Sample conversation.
            alt: A ticket panel open beside the board, where a coordinator reports that a change waits at Code review and answers a question about it
      - id: memory
        name: Memory
        title: Knowledge you can check.
        body: Agents and people search the same Memory. Every belief keeps the evidence behind it, so what one agent learns can be found, tested and reused by the next.
        layout: right
        link:
          label: Memory and knowledge
          doc: concepts/memory-and-knowledge/
        views:
          - name: Map
            image: memory-map
            width: 2000
            height: 1220
            caption: Everything known, and how it connects.
            alt: The Memory map, a graph of 764 pages and their links, with search and the most connected pages
          - name: Page
            image: memory-page
            width: 1800
            height: 1007
            caption: One page, with what is believed, what surrounds it and an append-only evidence list.
            alt: A Memory page with What we believe, the pages around it and an append-only Evidence list
      - id: observatory
        name: Observatory
        title: See the whole environment.
        body: A live map of the agents, services and models you have connected, and how they reach each other. Follow any session down to a single tool call, and export traces to any OpenTelemetry backend.
        layout: wide
        link:
          label: Shared discovery and topology
          doc: get-started/shared-discovery-and-topology/
        views:
          - name: Trace
            image: trace
            width: 1800
            height: 1063
            caption: Every turn, tool call and gate on one timeline.
            alt: The Telemetry tab of a session, with a timeline of turns, tool calls, a reviewer and a human gate
          - name: Live map
            image: observatory
            width: 2000
            height: 1220
            caption: Meshes, residents and clusters, with one resident selected.
            alt: The Observatory live map in 3D with meshes and residents listed, one resident selected and a stream of signals
  environment:
    label: The NIUU environment
    title: Six parts of one system.
    body: Each part does one job, and they work as one. Use the parts you need.
    workspaces:
      name: Workspaces
      icon: workspaces
      line: Where work happens
      body: Give ongoing work a home and clear limits.
      doc: concepts/sessions-and-workspaces/
    parts:
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
    platform:
      name: Platform
      icon: platform
      line: Your environment
      body: Connect your machines into one environment.
      doc: concepts/platform-model/
    observatory:
      name: Observatory
      icon: observatory
      line: What is happening
      body: See everything you connect on one live map.
      doc: get-started/shared-discovery-and-topology/
    built:
      title: We extend the best tools. We don't rebuild them.
      body: Keep your agents, models, tracker and infrastructure. NIUU builds on them.
      groups:
        - name: Agent harnesses
          entries: Claude Code, OpenAI Codex, OpenCode, Pi, OpenClaw, Hermes, NemoClaw, xAI Grok Build, Meta Muse Code, DeepSeek Harness
        - name: Models
          entries: Anthropic, OpenAI, DeepSeek, xAI, Meta, Ollama and OpenAI-compatible endpoints
        - name: Connects to
          entries: GitHub, GitLab, Linear, Jira, Telegram, MCP, A2A, any OIDC provider, any OpenTelemetry backend
        - name: Runs on
          entries: Local processes, Docker, OpenShell, VMs, bare metal, DGX Spark, Kubernetes
  evolution:
    label: Continuity and evolution
    title:
      - Work doesn't start over.
      - It evolves.
    body: A model changes. A person steps away. The work is still there, and further along.
    kept:
      - title: Responsibility
        body: Agents keep their mandate from day to day.
      - title: Decisions
        body: Decisions stay on record.
      - title: Knowledge
        body: Memory keeps what is known, and why.
      - title: Capability
        body: A tool built once can be reused.
    resident:
      id: residents
      name: Residents
      title: Agents that keep a responsibility.
      body: A resident is an agent you don't have to start. It looks after an environment over time, watches what changes, judges what matters, acts within its limits and brings you what it can't decide.
      layout: right
      link:
        label: Agents and residents
        doc: concepts/agents-and-personas/
      views:
        - name: Console
          image: resident
          width: 2000
          height: 1220
          caption: What a resident uses, what it changed and what it picked up from a peer.
          alt: The Resident console, with a roster of residents and Sigrun's report, reading Simpler now, 2 duplicate tools retired
        - name: Activity
          image: activity
          width: 2000
          height: 1220
          caption: What it decided, and how far each thing it watches has drifted.
          alt: Resident activity, with recent decisions and the operational state of a cluster, an inbox and a print workshop
        - name: Inbox
          image: inbox
          width: 2000
          height: 1220
          caption: A risky action stops here. Your decision goes on record with its reason.
          alt: The Review inbox with a high-risk action draft and fields to approve or reject with a recorded reason
    commission:
      title: A resident commissions the work it needs.
      body: Residents don't wait to be improved. When the same problem keeps coming back, a resident works out what would fix it. It can change what it believes, file a ticket, or commission a tool from a team of agents.
      steps:
        - name: Notices
          title: The same wall again
          body: It records what keeps getting in its way, and reviews that while it consolidates what it learned.
        - name: Commissions
          title: A team builds the fix
          body: It starts a workflow, and agents build the tool it lacks. It answers their questions and reviews their gates, within the limits you set.
        - name: Verifies
          title: It tests the result
          body: It runs the tests itself before anything is adopted. A tool that keeps failing is rolled back.
        - name: Evolves
          title: The next problem starts further along
          body: A peer can adopt that tested tool, and duplicates are retired. If a fix doesn't help, it is dropped and not tried again.
      image: toolbuilder
      width: 1700
      height: 455
      alt: The Tool and Skill Builder workflow as a graph of framing, building, reviewing and publishing stages
      caption: The workflow a resident commissions to build a missing tool.
    workspaces:
      id: workspaces
      name: Workspaces
      title: A home for ongoing work.
      body: A Workspace holds the goal, the work in motion and what is known, and a resident looks after it. Whatever needs you is in one place, across all of them.
      layout: wide
      link:
        label: Sessions and workspaces
        doc: concepts/sessions-and-workspaces/
      views:
        - name: All Workspaces
          image: workspaces
          width: 2000
          height: 1220
          caption: Every Workspace, what its resident is doing, and the decisions waiting for you.
          alt: The Workspaces home with three Workspaces, each kept by a resident, and a list of decisions that need you
        - name: One Workspace
          image: workspace
          width: 2000
          height: 1220
          caption: One Workspace, with what its resident did and what it asked for.
          alt: A Workspace showing what its resident did, with some actions taken alone and others waiting for approval
  use_cases:
    label: Use cases
    title: Not just for code.
    body: Software is where it started. Any work with a process and an outcome fits.
    cases:
      - title: Software delivery
        body: From ticket to reviewed, merged change.
        doc: concepts/workflows-and-teams/
      - title: Infrastructure
        body: Keep clusters healthy, and change them through reviewed GitOps.
        doc: concepts/agents-and-personas/
      - title: Research
        body: Explore a question from several angles and weigh the evidence.
        doc: concepts/workflows-and-teams/
      - title: Specifications
        body: Turn a brief into requirements, designs and a delivery plan.
        doc: concepts/workflows-and-teams/
      - title: Physical systems
        body: Watch a workshop, a lab or a home, through any signal you can connect.
        doc: concepts/agents-and-personas/
      - title: Add your own
        body: NIUU is built from composable parts. Extend, evolve, integrate or change any of them.
        doc: concepts/platform-model/
        own: true
  teams:
    label: For teams
    title: From one person to an organization.
    body: Start alone. When others join, NIUU runs under your identity provider, your policies and your infrastructure.
    areas:
      - title: Teams
        body: Everyone signs in as themselves, with a role. Residents and shared Memory belong to the organization, not to one person, so they stay when someone leaves.
        doc: reference/identity/
      - title: Multi-tenancy
        body: One installation can hold several organizations. Each sees what it owns, and model use can be capped per organization.
        doc: operations/security-and-permissions/
      - title: Scaling
        body: Move from one machine to Kubernetes, where each session runs in its own pod and services can autoscale. Join more machines and clusters, and they run as one environment.
        doc: operations/kubernetes-deployment/
      - title: Security
        body: Sign in through any OIDC provider, with policies deciding who can do what. Agents use scoped, short-lived credentials. Sessions can run in an OpenShell sandbox with limited network, and tools agents build run isolated. Larger multi-tenant deployments keep secrets in Vault or OpenBao, with access scoped to each session, and give sandboxes SPIFFE identities through SPIRE.
        doc: operations/security-and-permissions/
    note: These apply to a shared deployment. A local install is single-user, with no login.
    direction: "Where this is heading: inviting colleagues into a session from the app, sharing projects, workflows and Workspaces across an organization, managing members from your identity provider, and hosting unrelated customers on one installation. That is direction, not what ships today."
  start:
    label: Get started
    title: Try it on your own machine.
    body: The shortest way in is Docker. One command starts everything, and a setup page in your browser connects your models and repositories.
    options:
      - id: docker
        name: Docker
        title: On any Docker host
        needs: You need Docker with Compose, on Linux or a Mac.
        code: |-
          curl -fsSL https://raw.githubusercontent.com/niuulabs/niuu/dev/scripts/install.sh \
            | NIUU_IMAGE_TAG=dev sh
        note: It ends with a setup URL. Open it to finish in your browser. This mode has no login of its own and listens on every network interface, so run it on a network you trust.
      - id: source
        name: From source
        title: From source
        needs: You need Git, a C toolchain, uv, Node.js and pnpm.
        code: |-
          git clone https://github.com/niuulabs/niuu.git
          cd niuu
          uv sync --python 3.12 --extra dev
          make build-postgres build-web
          uv run niuu platform init
          uv run niuu platform up
        note: The build compiles PostgreSQL and the web UI, so give it a while. At the init prompt, press Enter for mini mode. This mode has no login of its own, so keep NIUU on 127.0.0.1, its default.
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
    - title: Start here
      body: Install it and run your first session.
      doc: get-started/introduction/
    - title: Understand NIUU
      body: How the parts fit together.
      doc: concepts/platform-model/
    - title: Operate
      body: Deploy it and keep it running.
      doc: operations/docker-mode/
    - title: Reference
      body: Commands, configuration and APIs.
      doc: reference/cli-niuu/
    - title: GitHub
      body: The source, under Apache-2.0.
      href: https://github.com/niuulabs/niuu
  footer: NIUU · Apache-2.0
---
