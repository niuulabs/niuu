# Workflow execution graph

Open **Ting → Workflows → Runs**, select a run, and choose **View execution graph**.
The graph uses the workflow definition frozen when the run was launched, including
the pinned definition used by each child workflow.

Select a node to inspect its public outputs and outcome history. Repeated outputs
remain visible, including review findings and repair attempts. Drag the canvas to
pan; use the zoom buttons or **Fit** to change the view. The canvas supports arrow
keys for node selection, `+`/`-` for zoom, and `F` to fit. Nodes can also be reached
with Tab and selected with Enter.

Select an expansion node to open its child workflows, or use the **Workflow
execution** selector. Each child is identified by its workstream, generation, and
attempt; earlier generations are labelled historical. Child logs load only when
selected. **Open run evidence** opens the existing Markdown report, which can be
copied or downloaded.

## What the history means

**Output recorded** means that a persisted public outcome can be associated with
that node. It does not prove stage completion or independently verify a signed
receipt. **Unobserved** means no outcome has been associated with that node in the
loaded portion of history; it does not mean the stage failed or was skipped.

The trace associates outcomes using explicit node identifiers or the frozen
workflow's event routing and pinned persona outcome mappings. Ambiguous outcomes
remain in **Run activity without a unique stage**. The original event type and
mapping information are preserved. Public outputs may include plans, review
findings, candidate references, and verification results; model reasoning and
private tool transcripts are not part of this view.

Active runs refresh periodically. Completed runs retain their recorded history.
For longer histories, use **Load more output**; the UI explicitly identifies a
partial history. A refresh failure remains visible alongside previously loaded
data and offers retry.

## Integration

`WorkflowExecutionGraph` is exported from `@niuulabs/plugin-ting` and accepts
provider-neutral projected nodes, edges, outputs, history, and callbacks for
evidence and child navigation. It does not fetch services or mutate workflows.
Other workflow experiences can reuse it with their own projection.

The developer run page uses `IDeveloperExecutionService.trace`, backed by:

```text
GET /api/v1/ting/developer-executions/{executionId}/trace
GET /api/v1/ting/developer-executions/{executionId}/trace?childId={attemptId}&after={sequence}&limit={pageSize}
```

The endpoint checks execution ownership and tenant boundaries, resolves persisted
session associations, and returns sanitized topology and one page of public
outcomes. It reads through Ting's existing Volundr port. It does not run agents,
change the execution ledger, or introduce another workflow engine.
