# Authoring portable workflows

Start with a working author/reviewer loop:

```sh
niuu workflows init editorial-review --model gpt-5.5
niuu workflows check editorial-review/workflow.yaml
niuu workflows build editorial-review/workflow.yaml --output editorial-review.zip
```

Choose a model configured in your gateway. From a source checkout, the same
commands are available through `.venv/bin/python -m cli workflows`.
Import the resulting ZIP from the existing Workflows page, resolve any local
bindings, and launch it through the normal workflow flow. `check` and `build`
run offline; they do not call a model, import a workflow, or launch an execution.
The commands refuse to overwrite an existing project or bundle.

## What an author edits

Each workflow has its own YAML file. The generated project contains
`workflow.yaml`, `personas/author.yaml`, and `personas/reviewer.yaml`.

- **Persona instructions:** what to produce, what to review, and when to ask for
  changes. The starter returns the artifact as Markdown and has a review/revision
  loop. It has no repository or source-control dependency.
- **Graph:** stages, event edges, selected models and budgets. These use the
  existing workflow format; the authoring tool does not introduce another engine.
- **Dependencies:** relative source paths. The build resolves exact versions and
  computes content hashes automatically.

For example, source dependencies look like this:

```yaml
persona_dependencies:
  author:
    source: personas/author.yaml
  reviewer:
    source: personas/reviewer.yaml
workflow_dependencies: {}
```

A child workflow uses the same declaration under `workflow_dependencies`, such
as `source: workflows/translation.yaml`. References are relative to the declaring
file and must remain within the root workflow's project directory. Cycles,
undeclared persona aliases, invalid graph contracts, duplicate YAML keys,
environment-bound personas, and inline graph credentials fail validation.

The build packages the transitive workflow/persona dependencies in the existing
portable ZIP format. A changed persona or child definition changes its content
pin. The source files remain small and editable; generated dependency hashes
belong to the bundle. Already-pinned portable bundles go directly to the importer,
rather than through the source compiler.

## Reviewing a workflow

`reviewAttestation` is a graph declaration connecting review roles to pinned
persona aliases. It is independent of persona display names and Git providers:

```yaml
reviewAttestation:
  version: 1
  scope: editorial
  eventType: article.review.completed
  roles:
    accuracy: fact-checker
    accessibility: accessibility-reviewer
```

The bound aliases must be declared dependencies and members of the appropriate
joined review stage. This declares who may produce evidence for each role; it
does not give an agent signing credentials. The runtime authenticates the actual
reviewing participant. Developer delivery still requires its configured evidence
policies and trusted attestation service.

Use a deterministic [evidence gate](workflow-evidence.md) when a workflow needs
verified receipts. The visual gate inspector exposes artifact selection, required
checks/reviews and their trusted producer IDs. Authors select requirements;
operators configure the services that produce and authenticate those receipts.
An ordinary agent's assertion that a check passed is not a signed receipt.

## Placing a workflow team on a Guild target

`placement` is an optional graph declaration that pins the whole workflow team
to a specific Guild target instead of the default unscoped selection. Declare
either a tag selector or an exact instance pin — never both:

```yaml
placement:
  tags: [dgx-spark]
  match: all   # "all" (default) requires every tag; "any" requires one
```

```yaml
placement:
  instance: spark-01   # matches a registered instance by id or name
```

At launch, Ting resolves `placement` against the Guild targets visible to the
launching principal. `instance` pins that exact connection. `tags` selects
among the targets carrying the requested tags, using the same balancing rule
as an untargeted launch when more than one matches. If nothing visible
satisfies the placement, the launch is rejected with a 422 naming both the
requested placement and every visible target's tags — it never falls back to
the default instance. A workflow with no `placement` launches exactly as
before, balanced across whatever is visible.

`placement` requires workflow `schema_version: 2`, like every other
structural graph addition since v1.

Placement is a whole-team decision today: it places every persona in the
graph together. A `placement` field on an individual node is rejected at
validation time rather than silently ignored — per-stage placement onto
different targets is a later capability.

### connectionId must satisfy placement

A launch can also name an explicit `connectionId` — in the REST launch body,
in A2A `SendMessage` metadata, or supplied by default on every launch a Ravn
resident starts through its `a2a_task` tool. When the target workflow also
declares `placement`, that `connectionId` is validated against it: it must
resolve to the placement's pinned instance, or to a target eligible by the
placement's tags. A `connectionId` that conflicts is rejected with a 422
naming both — it can never silently strip a workflow's placement. A
`connectionId` compatible with the placement still resolves directly, without
being re-balanced among the placement's other eligible targets.

### Includes and placement

An `include` node pulls stages or gates from another pinned workflow, but
never that workflow's own `graph.placement` — only stage/gate nodes cross the
include boundary. If the included (child) workflow declares its own
`placement`, it must match the including (parent) workflow's `placement`
exactly (tags compared as a set, not by list order), or the include is
rejected at resolution time. A child with no `placement` of its own never
conflicts, whatever the parent declares.

## A subworkflow node's templates

A `kind: subworkflow` node fans out into a bounded generation of child
workflows that a coordinator persona proposes through the expansion route.
`templates` names every child workflow the node may run, mapping a
node-local template name to a `workflow_dependencies` alias declared on the
same document. A node offering exactly one child workflow still declares a
mapping with one entry — there is no separate single-dependency form:

```yaml
workflow_dependencies:
  breadth: {id: ..., revision: ..., digest: ..., path: workflows/research-thread-breadth.yaml}
  depth: {id: ..., revision: ..., digest: ..., path: workflows/research-thread-depth.yaml}
  general: {id: ..., revision: ..., digest: ..., path: workflows/research-thread.yaml}
graph:
  nodes:
    - id: research-threads
      kind: subworkflow
      templates:
        breadth: breadth
        depth: depth
        general: general
      allowedCoordinator: research-coordinator
      maxChildren: 6
      maxAttempts: 2
      joinMode: all
      blockedEvent: research.threads.blocked
```

`inputSchema` and `resultSchema` stay declared once on the node, not per
template: every child the node runs, whichever template it uses, validates
its proposed `input` and completed `result` against the same pair of
schemas.

Each child proposed through the expansion tool — whether declared up front
or invented by the coordinator at runtime — names the `template` it runs.
Omitting `template` is only valid when the node offers exactly one; with
several on offer, a proposal that omits it is rejected, naming the templates
it could have chosen from. A child is stamped with its own named template's
exact `id`/`revision`/`digest`, independent of any sibling running a
different template in the same generation; a retry keeps the template its
earlier attempt used.

The execution response and the parent session's launch context both expose a
`templates` map (name → pinned identity, A2A skill id, and the child
workflow's own one-line `description`) so a coordinator persona can see what
the node offers and which `skillId` belongs to which template before it
decides.

## Declaring a subworkflow node's default children

When the author already knows the shape of a fan-out — a fixed set of
research threads, review angles, or translation targets — the node can
declare its own default children instead of leaving every one of them to be
invented at runtime:

```yaml
- id: research-threads
  kind: subworkflow
  templates: {breadth: breadth, depth: depth, general: general}
  allowedCoordinator: research-coordinator
  maxChildren: 6
  maxAttempts: 2
  joinMode: all
  blockedEvent: research.threads.blocked
  children:
    - key: breadth
      objective: Map the landscape widely across the framed question.
      template: breadth
      input: {}
    - key: depth
      objective: Drill into the highest-value thread from the frame.
      template: depth
      dependencies: [breadth]
      input: {}
```

Each entry names a unique `key`, a non-empty `objective`, and (when the node
offers more than one template) the `template` it runs — omitting it is a
validation error naming the node's templates when there is more than one.
`dependencies` must reference other declared keys and must not form a cycle;
`input` is validated against the node's own `inputSchema` when the node
declares one.

The engine never expands these on its own. A coordinator persona still calls
the expansion tool to propose them — verbatim, amended, or alongside
additional children it invents — the same way it would for any other
generation. The declared list is exposed on the execution response
(`declaredChildren`) purely so the coordinator can see what the graph already
knows about its node before it decides.

## Reusing stages with `kind: include`

Two workflows sometimes need the exact same stage — the same plan-and-review
loop, the same verification step — running inline in their own session, not
as a separate child run. Hand-copying the nodes works until one copy drifts
from the other. An `include` node names another pinned workflow and a set of
its nodes to copy in instead:

```yaml
workflow_dependencies:
  planning:
    id: 195d5026-7adb-4ce7-97ee-fb1e18ea4c3a
    revision: sha256:...
    digest: sha256:...
    path: workflows/developer-planning.yaml
graph:
  nodes:
    - id: delivery-planning
      kind: include
      label: Investigate and review the canonical plan
      workflow: planning
      nodes:
        planning-analysis: delivery-plan-author
        planning-reviews: delivery-plan-reviews
      overrides:
        delivery-plan-author: {label: "Plan the delivery"}
```

`workflow` names an alias already declared in this document's own
`workflow_dependencies`, pinned exactly like any other child. `nodes` maps
each node id in *that* workflow's graph to the id it takes in *this* one —
every value must be unique and must not collide with any other node id
already in this graph. Only `stage` and `gate` nodes may be included;
including a `trigger`, `end`, `subworkflow`, `wait`, `resource`, or another
`include` node (no nested includes) is a validation error. Every edge of the
included graph whose source *and* target are both named in `nodes` comes
along automatically, remapped to the local ids — the copied stages keep
whatever review loop, join, or retry edge they had in the source graph. An
edge elsewhere in the parent graph that already names one of those local ids
as its source or target needs no change; it was already written in terms of
the id the copied node takes.

Everything about a copied node — its `kind`, `stageMembers`, `executionMode`,
`joinMode`, `reviewVerdictPolicy`, budgets, prompts, and so on — comes
verbatim from the included document, which remains the single source of
truth for that stage. Only `position` and `label` can be overridden per node,
through `overrides` keyed by the node's local id; anything else in
`overrides` is rejected. Without an override, a copied node's `position` is
placed relative to the include node's own `position`, preserving the copied
nodes' relative layout from the source graph.

A copied stage's persona aliases must already be declared in this document's
own `persona_dependencies`, pinned to the exact same revision and digest as
the included workflow declares for that alias — the include does not import
a new persona pin, it requires this document to already agree with the one
it is borrowing from. `niuu workflows check` and the platform's own bundled
workflow set both catch a stale or missing pin before it can run.

An include is not a dispatch mechanism: the copied stages run inline, in the
same session as everything else in this graph, exactly as if they had been
written here by hand. A `subworkflow` node is the only construct that runs a
child workflow as a separate session; nothing about `include` changes how
`subworkflow` behaves, and the two are not interchangeable. By the time a
workflow launches, every `include` node has been resolved away — the runtime
that walks the graph, the trace shown for a running execution, and the
included workflow's own pinned identity in the execution's dependency
closure never see an `include` node, only the stages it stood in for.

## Current boundaries

Ordinary noncoding graphs already run through the existing workflow runtime.
Artifact evidence gates are reusable in those graphs. The neutral lifecycle core
also supports child dependency graphs, budgets, retries and joins without Git
fields; developer delivery now uses that core with its Git-specific contract.

The concrete durable database adapter and A2A launch/reconciliation gateway are
still developer-specific. Building a noncoding child definition is not sufficient
to launch dynamic noncoding runs through `/workflow-executions`. That remaining
adapter work is separate from authoring, static graph execution and evidence
verification. The authoring tool validates portable definitions, not the presence
of deployment credentials, model capacity or remote provider capabilities.
