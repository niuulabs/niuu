# Inspect workflows and runs

A workflow defines a process; a run records an execution of it. Start by inspecting
these separately before enabling automatic dispatch. You need Ting enabled in the
platform, and a configured executor before you can run real agent work.

## Find the definition and its executions

Open **Ting** in the platform UI. Inspect a workflow's stages and transitions,
then inspect a run using that workflow. On an empty installation there may be no
runs yet; an empty list is not a failed connection.

For each stage, identify the input, the responsible executor or role, the output,
and the condition that permits the next stage. If a stage launches a session,
follow its session reference and inspect the workspace output there.

You can inspect the workflow catalog directly on the default local host:

```bash
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/ting/workflows
```

Ting requires authentication for this endpoint, including on the local host.
Without an authenticated caller the command returns HTTP 401; use your configured
identity flow or the logged-in UI. This is not a public health check.

An authenticated response records each definition's nodes, edges, resource bindings, and
version. Inspect those alongside the UI; a saved graph is not proof of a
successful execution.

## Prepare a workflow in the editor

Open **Ting → Workflows**. The **Templates** sidebar lists saved definitions;
**+ new** creates a new user definition. A saved empty definition is not yet a
runnable workflow.

Use the **Library** to drag structural blocks and personas into the graph. For a
first process, use implementation and review stages connected in that order.
Inspect each stage's **config**, **flock**, and **validate** tabs: assign a persona
that exists in this deployment, choose an available model, set its budget, and
check the transition and fan-in behavior. Add the trigger and completion path
required by the graph's validation results.

If you start from a saved template, inspect its resource bindings before reuse.
A template can reference a Mímir registry entry or provider model from another
deployment. Replace those with real resources available to yours. Resolve the
editor's validation errors before choosing **Save as…**; verify the saved catalog
entry contains the graph you intended.

The current toolbar also displays **Test**, **Dispatch**, **Diff**, and **History**
buttons without action handlers in the editor. Do not use their presence as proof
that a workflow was tested, dispatched, or version-reviewed. **Save as…** and
**Launch…** are wired to actual operations.

## Launch and verify one execution

Select the saved definition, choose **Launch…**, and complete:

| Field | Enter |
| --- | --- |
| Prompt | A bounded task with an observable result, such as adding and testing one small repository function |
| Session name | An optional name to recognize this execution |
| Repo | The repository the workflow should change |
| Branch | The branch to use for that repository |

Choose **Launch**. Successful launch opens the returned Völundr session. This is
a workflow-backed flock session, so verify the participating runtime and provider
configuration first; it is not the same setup as the single Claude quick start.
Inspect the session's actual output and repository diff, then confirm that the
workflow reaches its completion condition.

The launch dialog does not expose every API field. The API accepts a prompt,
repository, branch, model/runtime overrides, and context; consult Ting's schema
when driving launches programmatically. Do not infer these fields from Völundr's
OpenAPI download.

## Read a blocked run

| Observation | Check |
| --- | --- |
| No execution started | Dispatch state and configured executor |
| Session never becomes running | Target, runtime startup, and session logs |
| Session starts but produces no answer | Provider authentication and runtime stream |
| Output exists but the stage does not advance | Required output and transition condition |
| Waiting for review | The pending gate and the artifact it asks you to judge |

Before retrying, inspect what the previous attempt already changed. A repeated
stage can repeat external side effects. Record the failure reason and verify the
next attempt addresses it.

This page explains the inspection process. A complete workflow creation tutorial
still requires validation against a configured executor; the local quick-start
verification does not certify Ting execution.
