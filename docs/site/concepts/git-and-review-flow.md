# Review agent changes

An agent's final message is a claim about its work. Review the changed files,
tests, and repository state before accepting that claim.

## Establish the starting point

For repository work, record the branch and existing changes before launching.
A local mount uses your existing path; unrelated uncommitted edits may already
be present. The [repository guide](../get-started/configure-project.md) starts
with a small dedicated checkout so ownership of changes is clear.

## Inspect the result

In the session, read the agent response and inspect Files and the diff view.
Then use the workspace terminal or the local checkout:

```bash
git status --short
git diff --stat
git diff --check
git diff
```

These commands inspect changes and whitespace; they do not run tests. Run the
checks required by the repository, and verify both their exit status and what
they cover. A test command printed in a chat message is not evidence it executed.

## Promote the work

Keep one reviewable change per branch where practical. Commit only the files
belonging to that change, then use your repository's normal pull-request and
approval process. Git-provider access and permission to push are separate from
having a local checkout.

Stopping a local session preserves files, but it is not a remote backup. Preserve
useful work before deleting a workspace or retiring its host.
