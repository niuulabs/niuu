# Sessions and workspaces

A **session** is a managed execution of an agent runtime and its conversation.
A **workspace** is the filesystem where that work happens. Stopping execution
and deleting its files are separate operations.

## From launch to result

A launch supplies a source, runtime, model, target, and optional persona,
credentials, and resources. Völundr records the session and asks the configured
backend to start it. Skuld starts the runtime and connects its messages and
workspace interfaces to the platform.

A session that is running has a live process. That does not prove its model
credentials work or that the task succeeded. Check the agent response and the
produced files, then run the project's checks.

## Choosing a source

| Source | Effect | Appropriate use |
| --- | --- | --- |
| Blank | Starts without cloning a repository | Learning the launch flow or creating new files |
| Git repository | Uses repository and branch information for workspace preparation | Repository work on a supported target |
| Local mount | Uses an existing host path | Local work where you want changes in that directory |

A local mount can expose an existing checkout to changes. A local-process session
runs as your OS user; calling its directory a workspace does not make it a
container or isolate the rest of the account.

## Ending work

Stop the session when execution should end. The local backend keeps its workspace
on disk, so you can inspect the result afterward. Archive changes how the session
is presented and retained in the platform; it should not be used as shorthand for
a filesystem backup. Before deleting workspace data, export the files or commit
the changes you need to keep.

The [quick start](../get-started/first-local-stack.md) verifies this lifecycle.
[Repository work](../get-started/configure-project.md) adds source control and
[review](git-and-review-flow.md).
