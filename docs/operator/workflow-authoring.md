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

## Current boundaries

Ordinary noncoding graphs already run through the existing workflow runtime.
Artifact evidence gates are reusable in those graphs. The neutral lifecycle core
also supports child dependency graphs, budgets, retries and joins without Git
fields; developer delivery now uses that core with its Git-specific contract.

The concrete durable database adapter and A2A launch/reconciliation gateway are
still developer-specific. Building a noncoding child definition is not sufficient
to launch dynamic noncoding runs through `/developer-executions`. That remaining
adapter work is separate from authoring, static graph execution and evidence
verification. The authoring tool validates portable definitions, not the presence
of deployment credentials, model capacity or remote provider capabilities.
