# Choose your next step

Niuu is composable: begin with the part of the ecosystem your work needs.
The [quick start](first-local-stack.md) offers a small verified coding session;
a direct agent, shared knowledge store, or workflow can also be your entry point.

For the larger picture, read [What is Niuu?](introduction.md) and the
[operating-system direction](../concepts/operating-system.md).

| Desired result | Guide | What you need first |
| --- | --- | --- |
| Change files in a repository | [Repository work](configure-project.md) | A working session runtime and repository access |
| Understand model choices and provider health | [Model routing](model-routing-step.md) | A configured provider for inference |
| Preserve and retrieve a useful fact | [Durable knowledge](durable-memory.md) | A writable knowledge store |
| Understand staged work and blocked runs | [Workflows](workflows-and-teams-step.md) | The workflow service and a configured executor for live work |
| Run a direct agent or a resident | [Agents and residents](direct-and-resident-assistants.md) | Agent runtime configuration and model access |
| Find service instances and trace relationships | [Discovery and topology](shared-discovery-and-topology.md) | Registered instances with reachable endpoints |
| Operate shared infrastructure | [Kubernetes](../operations/kubernetes-deployment.md) | Cluster, database, identity, storage, and secrets |

Local processes, OpenShell, and Kubernetes describe execution and deployment
choices. They are not maturity levels: a local setup can be the right long-term
choice, and Kubernetes does not by itself configure resident behavior.
