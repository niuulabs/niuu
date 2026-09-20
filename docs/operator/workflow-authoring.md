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
