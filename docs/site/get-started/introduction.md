# What is Niuu?

Niuu is a self-hosted platform for agent work. It gives agents managed workspaces,
shared services, and ways to collaborate with people and other agents. You can
use its browser interface or call its APIs from your own tools and agents.

Start with [one local session](first-local-stack.md). You will authenticate Claude
Code, start Niuu, launch an empty workspace, and verify a file created by the agent.
You do not need to configure every service to complete that exercise.

## Three useful starting points

| You want to… | Start here |
| --- | --- |
| Run a coding agent and inspect its output | [Quick start](first-local-stack.md) |
| Understand how Niuu, Skuld, and Ravn fit together | [Architecture](../concepts/platform-model.md) |
| Operate services on shared infrastructure | [Deployment](../operations/kubernetes-deployment.md) |

After the first session, [attach a repository](configure-project.md). Add memory,
model routing, workflows, or residents when those capabilities serve the work.
[Choose your next step](path-from-small-to-autonomous.md) by the result you need.

## Know which version you are using

The docs describe the current source tree. Published binaries can lag behind it;
[installation](install.md) lists known release issues, and the
[verification record](../operations/quickstart-verification.md) separates tests
that passed from paths that still need live validation.
