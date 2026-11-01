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
    - label: The system
      href: "#system"
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
  gallery:
    title:
      - One agent is a starting point.
      - Not the limit.
    body: Follow one agent as it grows into a whole environment. Real product interfaces, shown with sample data.
    chapters:
      - id: session
        step: One agent
        label: Sessions and execution
        layout: opening
        title: It starts with one agent.
        body: You steer every turn. The session holds its chat, diffs, files and trace.
        link:
          label: Sessions and workspaces
          doc: concepts/sessions-and-workspaces/
        lead:
          image: session
          width: 1600
          height: 1120
          caption: Inside one session. Sample conversation.
          alt: A session chat where an agent reproduces a failing export, fixes it and points to the diff for review
        aside:
          image: forge
          width: 2000
          height: 1135
          caption: Forge runs agents on the machine that suits the job.
          alt: The Forge overview with active pods, tokens and cost, a list of in-flight sessions and the load on each host
      - id: process
        step: A process
        label: Workflows and graphs
        layout: stage
        title: Work becomes a process.
        body: The path is fixed in advance, with stages, reviews and gates. Agents do the work inside it, each with its own tools and model.
        link:
          label: Workflows and teams
          doc: concepts/workflows-and-teams/
        stage:
          image: graph-stage
          width: 2400
          height: 962
          caption: A workflow that ships with NIUU. The moving points show how a run travels through it.
          alt: A workflow graph where Implement code branches into Review changes and Security review, which converge into Capture post-mortem and Curate shared memory, with a dashed line looping failures back to Implement code
        tiles:
          - image: graph-gates
            width: 2000
            height: 787
            span: 5
            caption: Gates hold the work until a decision is made.
            alt: Ticket Planning Flow as a graph of Draft the plan, Review the plan and Record the approved plan, with two purple gates and dashed lines back to the first stage
          - image: board
            width: 2000
            height: 1046
            span: 7
            caption: Your tickets stay in your tracker and run through workflows on a board.
            alt: A project board with columns from Ticket Planning Flow and Reviewed Delivery Flow, with tickets running or waiting for a person
      - id: together
        step: A team
        label: Multi-agent collaboration
        layout: room
        title: Work becomes collaborative.
        body: Agents hand work to each other, review it and send it back when it falls short. You join at decisions.
        link:
          label: Workflows and teams
          doc: concepts/workflows-and-teams/
        lead:
          image: room
          width: 2000
          height: 1774
          caption: One room, with its delegations and reviews on the right. Sample conversation.
          alt: A room where a planner splits the work, a coder builds it, a reviewer sends it back, and the planner asks a person to decide
        handoffs:
          - who: Planner
            what: Splits the work and assigns it.
          - who: Coder
            what: Builds it and reports back.
          - who: Reviewer
            what: Finds a silent failure and sends it back.
          - who: Planner
            what: Asks you to decide.
        tiles:
          - image: coordinator
            width: 1884
            height: 1932
            span: 5
            caption: Every ticket in motion gets a coordinator that stays until it closes. Sample conversation.
            alt: A ticket panel open beside the board, where a coordinator reports that a change waits at Code review and answers a question about it
          - image: gate
            width: 1884
            height: 1272
            span: 7
            caption: You approve or ask for changes, and see where each answer leads.
            alt: A ticket at Code review, with its pull request and buttons to request changes or approve and move to Merging
      - id: continuity
        step: Continuity
        label: Memory and residents
        layout: mosaic
        title: Work doesn't start over.
        body: A model changes. A person steps away. The work is still there, and further along.
        link:
          label: Agents and residents
          doc: concepts/agents-and-personas/
        mosaic:
          - image: memory-map
            width: 2000
            height: 1220
            place: map
            caption: Agents and people search the same Memory.
            alt: The Memory map, a graph of 764 pages and their links, with search and the most connected pages
          - image: memory-page
            width: 1800
            height: 1007
            place: side
            caption: Every belief keeps the evidence behind it.
            alt: A Memory page with What we believe, the pages around it and an append-only Evidence list
          - image: inbox
            width: 2000
            height: 1220
            place: side
            caption: A risky action stops here. Your decision goes on record.
            alt: The Review inbox with a high-risk action draft and fields to approve or reject with a recorded reason
          - image: resident
            width: 2000
            height: 1220
            place: wide
            caption: A resident keeps a responsibility, with no prompt needed. A schedule, an event, a ticket, a message or a reading from a machine can set it off.
            alt: The Resident console, with a roster of residents and Sigrun's report, reading Simpler now, 2 duplicate tools retired
          - image: workspaces
            width: 2000
            height: 1220
            place: narrow
            caption: A Workspace gives that work a home.
            alt: The Workspaces home with three Workspaces, each kept by a resident, and a list of decisions that need you
        commission:
          title: A resident commissions the work it needs.
          body: When the same problem keeps coming back, a resident works out what would fix it. It can change what it believes, file a ticket, or commission a tool from a team of agents.
          steps:
            - name: Notices
              body: It records what keeps getting in its way.
            - name: Commissions
              body: A team of agents builds the tool it lacks, within the limits you set.
            - name: Verifies
              body: It runs the tests itself. A tool that keeps failing is rolled back.
            - name: Evolves
              body: A peer can adopt the tested tool, and duplicates are retired.
          image: toolbuilder
          width: 1700
          height: 455
          alt: The Tool and Skill Builder workflow as a graph of framing, building, reviewing and publishing stages
          caption: The workflow a resident commissions to build a missing tool.
      - id: scale
        step: An environment
        label: Scale, multi-tenancy and observability
        layout: environment
        title: Work becomes an environment.
        body: Add machines, teams and organizations to one system, each with the isolation it needs.
        link:
          label: How it fits together
          doc: concepts/platform-model/
        map:
          title: Six parts of one system.
          tenants:
            - Organization A
            - Organization B
          boundary: Each organization sees what it owns.
          workspaces:
            name: Workspaces
            line: Ongoing work, kept by residents
            doc: concepts/sessions-and-workspaces/
          agents:
            name: Agents
            line: Roles, tools and residents
            doc: concepts/agents-and-personas/
          memory:
            name: Memory
            line: What is known, with its evidence
            doc: concepts/memory-and-knowledge/
          forge:
            name: Forge
            line: Runs sessions and workflows on any connected host
            doc: concepts/sessions-and-workspaces/
          platform:
            name: Platform
            line: Joins your machines, with one sign-in, shared policy and model limits per organization
            doc: concepts/platform-model/
          observatory:
            name: Observatory
            line: See everything you connect, filtered to what you may see
            doc: get-started/shared-discovery-and-topology/
          hosts:
            - name: This machine
              line: Local process
            - name: Workstation
              line: Docker
            - name: DGX Spark
              line: Docker
            - name: Cluster
              line: A pod per session
            - name: Sandbox
              line: OpenShell
        tiles:
          - image: platform
            width: 2000
            height: 1203
            span: 6
            caption: Every host in one place, with its health and tags. Sample data.
            alt: The Platform page listing Forge hosts for this machine, a GPU workstation, a DGX Spark and a cluster, each healthy and tagged
          - image: register
            width: 2000
            height: 1203
            span: 6
            caption: Add a host with an address and tags. Sessions and workflows can target it by tag.
            alt: The Register a new instance dialog with a name, an endpoint URL, a plaintext option, a TLS fingerprint and tags
          - image: trace
            width: 1800
            height: 1063
            span: 5
            caption: Every turn, tool call and gate on one timeline.
            alt: The Telemetry tab of a session, with a timeline of turns, tool calls, a reviewer and a human gate
          - image: observatory
            width: 2000
            height: 1220
            span: 7
            caption: A live map of the agents, services and models you have connected.
            alt: The Observatory live map in 3D with meshes and residents listed, one resident selected and a stream of signals
        areas_label: In place today
        areas:
          - title: Teams
            body: Everyone signs in with a role. Residents and shared Memory stay with the organization when someone leaves.
            doc: reference/identity/
          - title: Multi-tenancy
            body: Several organizations on one installation, with model use capped per organization.
            doc: operations/security-and-permissions/
          - title: Scaling
            body: A pod per session on Kubernetes, services that can autoscale, and several clusters as one environment.
            doc: operations/kubernetes-deployment/
          - title: Security
            body: Any OIDC provider, policies, scoped short-lived credentials and OpenShell sandboxes. Larger deployments add Vault or OpenBao for secrets and SPIFFE identities through SPIRE.
            doc: operations/security-and-permissions/
        note: These apply to a shared deployment. A local install is single-user, with no login.
        direction: "Planned, not shipped: inviting colleagues into a session from the app, sharing projects, workflows and Workspaces across an organization, managing members from your identity provider, and hosting unrelated customers on one installation."
  environment:
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
