---
template: home.html
hide:
  - navigation
  - toc
# Landing page copy. docs/overrides/home.html renders this; change the words here.
home:
  title: NIUU — A place where intelligent systems do real work
  action:
    label: Brand system ↗
    href: "#identity"
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
      - label: Explore NIUU
        arrow: ↗
        style: primary
        href: overview/
        docs: true
      - label: See how it works
        arrow: →
        style: secondary
        href: "#difference"
    features:
      - Autonomous
      - Collaborative
      - Persistent
      - Observable
      - Self-hosted
    tagline: Continuity / not a session
  principle:
    label: 01 / The principle
    title:
      - Work continues.
      - Even when sessions end.
    body: >-
      A model changes. An agent hands off. A person steps away. NIUU keeps the
      thread: goals, knowledge, responsibility, decisions and evidence remain
      connected.
    visual: Continuity of work
    steps:
      - title: Intent
        body: Define the outcome
      - title: Collaboration
        body: Work across boundaries
      - title: Progress
        body: Carry context forward
  products:
    label: 02 / The NIUU environment
    title: One place. Six ways to work.
    body: >-
      One recognizable product family. No mythology required to understand what
      each part does.
    family:
      - name: Platform
        icon: platform
        line: Your environment
        body: The common foundation for identity, policy and execution.
      - name: Workspaces
        icon: workspaces
        line: Where work happens
        body: Shared spaces for people, agents and ongoing work.
      - name: Agents
        icon: agents
        line: Who does the work
        body: Autonomous actors with responsibilities and capabilities.
      - name: Forge
        icon: forge
        line: Build and execute
        body: Capabilities, tools and isolated execution environments.
      - name: Memory
        icon: memory
        line: What is known
        body: Knowledge and context that survive individual sessions.
      - name: Observatory
        icon: observatory
        line: What is happening
        body: Traceable activity, decisions and evidence.
  experience:
    label: 03 / The experience
    title: See the work. Understand the progress.
    body: >-
      A workspace should tell you what's happening, who is responsible, what has
      been learned, and what needs your attention.
    demo:
      selected: workspaces
      breadcrumb:
        - Workspaces
        - Product engineering
      title: Product engineering
      body: Responsibilities, decisions and progress in one place.
      action:
        label: Review decisions ↗
        feedback: "Concept interaction: decisions are shown in the attention panel."
      kpis:
        - value: "08"
          label: Active goals
        - value: "12"
          label: Working agents
        - value: "03"
          label: Pending decisions
        - value: 96%
          label: Context retained*
      footnote: "* Illustrative interface and data, not measured product metrics."
      tabs:
        - id: progress
          label: Progress
          panels:
            - title: Work in motion
              rows:
                - mark: ↗
                  title: Prepare next platform release
                  meta: 2 agents · validation running
                  progress: 75
                - mark: ↗
                  title: Evaluate memory consolidation
                  meta: 3 agents · review underway
                  progress: 48
                - mark: ✓
                  title: Document deployment recovery
                  meta: Evidence stored · context preserved
            - title: Your attention
              body: Most work is progressing independently. One decision is ready for review.
              decision:
                title: Approve staging release
                body: Verification complete. Changes and evidence are ready to inspect.
                link: Review evidence →
                feedback: "Concept interaction: evidence would open a linked review."
              note: "↗ &nbsp;Work resumes with context after handoffs."
        - id: activity
          label: Activity
          panels:
            - title: Recent activity
              rows:
                - mark: ✓
                  title: Validation evidence attached
                  meta: Release agent · 4 minutes ago
                - mark: ↗
                  title: Review requested
                  meta: Coordinator · 12 minutes ago
                - mark: ✓
                  title: Knowledge consolidated
                  meta: Memory service · 1 hour ago
            - title: How the work stays connected
              body: >-
                Actions are linked to goals, responsible agents, decisions and
                supporting evidence, so work can be resumed and reviewed.
        - id: memory
          label: Memory
          panels:
            - title: Retained knowledge
              rows:
                - mark: ≡
                  title: Release validation checklist
                  meta: Shared workspace knowledge · 6 sources
                - mark: ≡
                  title: Deployment recovery guide
                  meta: Updated from completed work
                - mark: ≡
                  title: Platform architecture decisions
                  meta: Accepted decisions and rationale
            - title: Context outlives the session
              body: >-
                Knowledge is associated with ongoing work rather than being
                trapped inside one temporary conversation or a single agent.
  documentation:
    label: 04 / Documentation and learning
    title: Advanced underneath. Clear on the surface.
    body: >-
      Documentation should explain what you can do before asking you to learn
      how every internal service works.
    demo:
      label: NIUU / Docs
      current: Getting started
      title: Give intelligent work a place to happen.
      body: >-
        Create a workspace, connect your first agent, and give it a clearly
        defined responsibility. NIUU provides a common environment for work,
        context and oversight.
      code: workspace → goal → agent → progress → evidence
      note: >-
        Public documentation speaks in outcomes, responsibilities and workflows.
        Implementation details remain available when you need them.
  identity:
    label: 05 / Identity system
    title: One identity, everywhere.
    body: >-
      The orbital mark and the custom wide NIUU wordmark remain consistent from
      the homepage to the product, docs, presentation covers and app icons.
    lockup: Primary lockup / expressive vector
    sizes: One design, simplified at small sizes
    color: Color / one accent family
    palette:
      - name: Ink
        hex: "#071019"
      - name: Midnight
        hex: "#0B1722"
      - name: Panel
        hex: "#10212D"
      - name: Raised
        hex: "#182B38"
      - name: Mist
        hex: "#EDF4F7"
      - name: Signal
        hex: "#86D0E8"
    applications:
      label: Applications / consistent visual language
      site:
        body: A place where intelligent systems do real work.
        label: Site and launch materials
      docs:
        kicker: NIUU / Docs
        body: Build environments for intelligent work.
        label: Documentation
      icon: Application and favicon
  footer:
    - NIUU / Continuity identity study · v0.4 · concept
    - One idea. One palette. One environment.
---
