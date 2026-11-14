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
    - label: Building blocks
      href: "#blocks"
    - label: At work
      href: "#at-work"
    - label: Use cases
      href: "#use-cases"
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
      Bring the models, agent harnesses and tools you already use. NIUU gives you
      the building blocks to run them together, from one agent to many that
      collaborate with people, learn and evolve.
    actions:
      - label: See the building blocks
        arrow: →
        style: primary
        href: "#blocks"
      - label: Get started
        arrow: →
        style: secondary
        href: "#start"
    trust: Open source under Apache-2.0. Self-hosted, with your choice of models.
  blocks:
    label: Why NIUU
    title:
      - Great tools.
      - Hard to make work together.
    body: There are hundreds of models, agent harnesses, memory systems and runtimes, and many are excellent at what they do. The hard part is making them work together, and keeping that working as better ones arrive.
    subtitle: Your tools. Your models. Your environment.
    intro: NIUU provides the building blocks. Each is useful on its own and has its own API. Use one, bring your own, or combine them all.
    pieces:
      - id: harnesses
        name: Harnesses
        line: Bring the agents you already use.
        uses: Claude Code, Codex, OpenCode, Pi, Grok Build, Muse, DeepSeek Harness, OpenClaw, Hermes, NemoClaw
        products: Forge · Agents
        doc: concepts/agents-and-personas/
      - id: sessions
        name: Sessions
        line: Any harness, live in one place. Watch it work, step in, and pick up where it left off.
        uses: Chat, Terminal, Files and diffs, Chronicle, Resume, Browser or Telegram
        products: Forge
        doc: concepts/sessions-and-workspaces/
      - id: memory
        name: Memory
        line: Shared knowledge that keeps its evidence.
        uses: Shared and private memory, Evidence, Search
        products: Memory
        doc: concepts/memory-and-knowledge/
      - id: runtimes
        name: Runtimes
        line: Run wherever you have compute.
        uses: Local processes, Docker, Kubernetes, VMs, OpenShell sandboxes, Bare metal
        products: Forge · Platform
        doc: operations/kubernetes-deployment/
      - id: models
        name: Models
        line: Route by cost, latency or failover, with budgets and rules.
        uses: Anthropic, OpenAI, any OpenAI-compatible endpoint, Ollama
        products: AI models
        doc: concepts/model-routing/
      - id: collaboration
        name: Collaboration
        line: Agents delegate, review and commission work. People join when needed.
        uses: Rooms, Agent mesh, A2A, Telegram, Review inbox
        products: Agents
        doc: concepts/workflows-and-teams/
      - id: workspaces
        name: Workspaces
        line: Ongoing work gets a home, with jobs that run on a schedule or when something happens.
        uses: Charter, Trust, Budget, Activity log, Scheduled and event jobs
        products: Workspaces
        doc: concepts/agents-and-personas/#mandates-and-environments
      - id: observability
        name: Observability
        line: Telemetry, traces and a live map of everything connected.
        uses: OpenTelemetry, Session traces, Live map, Audit log
        products: Observatory
        doc: get-started/shared-discovery-and-topology/
      - id: workflows
        name: Workflows
        line: Graphs with stages, parallel branches, gates and checks.
        uses: Stages, Parallel branches, Gates, Waits on checks, Boards
        products: Work
        doc: concepts/workflows-and-teams/
      - id: residents
        name: Residents
        line: Persistent agents that look after a workspace. They react, learn and commission the tools they lack.
        uses: Mandates, Triggers, Inbox, Learning, Tool building
        products: Agents · Workspaces
        doc: concepts/agents-and-personas/#direct-agents-and-residents
      - id: security
        name: Security & governance
        line: Identity, isolation, access control and organizations.
        uses: Any OIDC provider, Policies, Scoped credentials, Sandboxes, Vault or OpenBao
        products: Platform
        doc: operations/security-and-permissions/
    assemble:
      title: Assemble what you need.
      body: Start with one block and add more as your needs grow. These are configurations of the same blocks, not separate products.
      configs:
        - id: day-1
          name: Day 1
          line: The agent harness you already use, live on your own machine, with memory that carries from one session to the next.
          blocks: harnesses sessions memory runtimes
        - id: day-2
          name: Day 2
          line: Work spreads across machines and models, agents work together, ongoing work gets a home with its own jobs, and you can see all of it.
          blocks: harnesses sessions memory runtimes models collaboration workspaces observability
        - id: day-3
          name: Day 3
          line: Residents look after the work, processes run with stages and gates, under the security and governance an organization needs.
          blocks: harnesses sessions memory runtimes models collaboration workspaces observability workflows residents security
      swap: Blocks talk over APIs, and many load their adapters from configuration, so you can replace or extend one without rebuilding the rest.
      plugins: Missing an integration? NIUU is an extensible platform with a plugin architecture. Bring your own tools and integrations.
  gallery:
    title:
      - Combine them,
      - and see what becomes possible.
    chapters:
      - id: process
        step: Workflows
        label: Workflows · Harnesses · Models
        title: Work becomes a process.
        body: The path is fixed in advance, with stages, reviews and gates. Agents do the work inside it, across models and harnesses. Your graph becomes the board.
        body_more: This is one example. Build your own workflows, and create personas with their own tools to do the work.
        link:
          label: Workflows and teams
          doc: concepts/workflows-and-teams/
        views:
          - name: A run
            image: graph-stage
            width: 2400
            height: 962
            caption: One workflow that ships with NIUU.
            alt: A workflow graph where Implement code branches into Review changes and Security review, which converge into Capture post-mortem and Curate shared memory, with a dashed line looping failures back to Implement code
            flow: true
          - name: Gates
            image: graph-gates
            width: 2000
            height: 787
            caption: Gates hold the work until a decision is made.
            alt: Ticket Planning Flow as a graph of Draft the plan, Review the plan and Record the approved plan, with two purple gates and dashed lines back to the first stage
          - name: Tickets
            image: board
            width: 2000
            height: 1046
            caption: Your tickets stay in your tracker and run through workflows on a board.
            alt: A project board with columns from Ticket Planning Flow and Reviewed Delivery Flow, with tickets running or waiting for a person
          - name: Decision
            image: gate
            width: 1884
            height: 1272
            caption: You approve or ask for changes, and see where each answer leads.
            alt: A ticket at Code review, with its pull request and buttons to request changes or approve and move to Merging
      - id: together
        step: Collaboration
        label: Collaboration · Workflows
        title: Work becomes collaborative.
        body: Agents work autonomously. They hand work on, review it and send it back, and you're only asked when you're needed.
        link:
          label: Workflows and teams
          doc: concepts/workflows-and-teams/
        handoffs:
          - who: Planner
            what: Splits the work and assigns it.
          - who: Coder
            what: Builds it and reports back.
          - who: Reviewer
            what: Finds a silent failure and sends it back.
          - who: Planner
            what: Asks you to decide.
        views:
          - name: Room
            image: room
            width: 2000
            height: 1774
            caption: One room, with its delegations and reviews on the right.
            alt: A room where a planner splits the work, a coder builds it, a reviewer sends it back, and the planner asks a person to decide
          - name: Coordinator
            image: coordinator
            width: 1884
            height: 1932
            caption: Every ticket in motion gets a coordinator that stays until it closes.
            alt: A ticket panel open beside the board, where a coordinator reports that a change waits at Code review and answers a question about it
      - id: continuity
        step: Memory
        label: Memory · Workspaces
        title: Work doesn't start over.
        body: A model changes. A person steps away. The work is still there, and further along.
        link:
          label: Agents and residents
          doc: concepts/agents-and-personas/
        views:
          - name: Memory
            image: memory-map
            width: 2000
            height: 1220
            caption: Agents and people search the same Memory.
            alt: The Memory map, a graph of 764 pages and their links, with search and the most connected pages
          - name: Evidence
            image: memory-page
            width: 1800
            height: 1007
            caption: Every belief keeps the evidence behind it.
            alt: A Memory page with What we believe, the pages around it and an append-only Evidence list
          - name: Inbox
            image: inbox
            width: 2000
            height: 1220
            caption: A risky action stops here. Your decision goes on record.
            alt: The Review inbox with a high-risk action draft and fields to approve or reject with a recorded reason
          - name: Workspaces
            image: workspaces
            width: 2000
            height: 1220
            caption: A Workspace gives ongoing work a home.
            alt: The Workspaces home with three Workspaces, each kept by a resident, and a list of decisions that need you
      - id: autonomy
        step: Autonomy
        label: Residents · Collaboration · Workflows
        title: A resident commissions the work it needs.
        body: Reusing the same workflows and tooling a person can.
        link:
          label: Agents and residents
          doc: concepts/agents-and-personas/
        steps:
          - name: Notices
            body: It records what keeps getting in its way.
          - name: Commissions
            body: A team of agents builds the tool it lacks, within the limits you set.
          - name: Verifies
            body: It runs the tests itself. A tool that keeps failing is rolled back.
          - name: Evolves
            body: Residents can mesh to share tested tools and knowledge, and duplicates are retired.
        views:
          - name: Tool builder
            image: toolbuilder
            width: 1700
            height: 455
            caption: The workflow a resident commissions to build a missing tool.
            alt: The Tool and Skill Builder workflow as a graph of framing, building, reviewing and publishing stages
          - name: Resident
            image: resident
            width: 2000
            height: 1220
            caption: A resident keeps a responsibility, with no prompt needed. A schedule, an event, a ticket, a message or a reading from a machine can set it off.
            alt: The Resident console, with a roster of residents and Sigrun's report, reading Simpler now, 2 duplicate tools retired
      - id: scale
        step: Scale
        label: Runtimes · Security & governance · Observability
        title: Work becomes an environment.
        body: Run on a workstation, a DGX Spark or a whole cluster. Organizations share one installation, each seeing what it owns, and you can follow any session down to a single tool call.
        link:
          label: How it fits together
          doc: concepts/platform-model/
        views:
          - name: Live map
            image: observatory
            width: 2000
            height: 1220
            caption: A live map of the agents, services and models you have connected.
            alt: The Observatory live map in 3D with meshes and residents listed, one resident selected and a stream of signals
          - name: Trace
            image: trace
            width: 1800
            height: 1063
            caption: Every turn, tool call and gate on one timeline.
            alt: The Telemetry tab of a session, with a timeline of turns, tool calls, a reviewer and a human gate
          - name: Your hosts
            image: platform
            width: 2000
            height: 1203
            caption: Every host in one place, with its health and tags.
            alt: The Platform page listing Forge hosts for this machine, a GPU workstation, a DGX Spark and a cluster, each healthy and tagged
          - name: Add a host
            image: register
            width: 2000
            height: 1203
            caption: Add a host with an address and tags.
            alt: The Register a new instance dialog with a name, an endpoint URL, a plaintext option, a TLS fingerprint and tags
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
        needs: You need Git, a C toolchain, uv, Node.js, pnpm and Bun.
        code: |-
          git clone https://github.com/niuulabs/niuu.git
          cd niuu
          uv sync --python 3.12 --extra dev
          make build-postgres build-gbrain build-web
          uv run niuu platform init
          uv run niuu platform up
        note: The build compiles PostgreSQL, gbrain (local memory) and the web UI, so give it a while. At the init prompt, press Enter for mini mode. This mode has no login of its own, so keep NIUU on 127.0.0.1, its default.
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
