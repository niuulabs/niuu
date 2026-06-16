# Workflows And Teams

Add workflows when one assistant in one workspace is no longer enough.

This is where Ting becomes useful. Treat it as the coordination layer for
stages, gates, reusable processes, and multi-role work.

![Ting workflow builder](../images/ui-ting-workflows.png)

## When to use a workflow

Use a workflow when:

- the task has repeatable stages
- a human should approve a stage
- different roles should handle different parts
- work should retry or escalate in a consistent way
- several sessions need to be coordinated

Do not start here for simple tasks. A normal workspace session is easier to
review and easier to debug.

## A simple first workflow

Start with a small shape:

1. Plan
2. Implement
3. Review
4. Summarize

Keep the first workflow boring. The point is to learn how state moves through
the system.

## Gates

Use gates where human judgment matters:

- before writing to a protected repo
- before spending a larger model budget
- before opening external tickets or messages
- before merging or deploying

Gates should be meaningful. Too many gates make the workflow harder to operate
than a normal session.

## Relationship to sessions

Workflows can launch, coordinate, or observe sessions. Sessions still contain
the actual workspace activity.

If a workflow produces a code change, review the session diff before promoting
that change.

## What good looks like

You should be able to answer:

- What stage is active?
- What is waiting on a human?
- Which session did the work?
- What happens if a stage fails?
- Where is the final review surface?

## Common mistake

Do not turn every task into a workflow. Use workflows for structure you expect
to reuse.

## Next

When something should stay alive outside one workflow or session, add a resident
assistant:

[Direct and resident assistants](direct-and-resident-assistants.md)
