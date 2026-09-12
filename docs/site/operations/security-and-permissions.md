# Execution boundaries and permissions

A Niuu session can run code, read files, and call services using the authority
available to its runtime. The execution backend determines the isolation boundary.

## Local processes

Mini mode starts processes as the host OS user. A workspace directory is not an
access-control boundary around that account. Local mounts can expose an existing
checkout, and the runtime may inherit host credentials or environment settings.
Use the local path for work whose code and tools you trust with that account.

## Remote runtimes

A Kubernetes pod or OpenShell sandbox has its own configured network, filesystem,
identity, and credential paths. Verify the actual mounts and runtime policy.
Do not assume that moving a process to a cluster automatically limits the
credentials attached to it.

OpenShell's provider-grant path is distinct from mounting a home directory with
agent login files. Use the supported [OpenShell credential flow](openshell-runtime.md)
for that backend.

## People, workloads, and providers

Operator login controls access to Niuu. Workload identity identifies a running
session or sandbox. Provider authentication allows inference or another external
operation. Test each independently and scope grants to the intended caller and
operation. See [identity](../reference/identity.md) and
[credentials](../reference/credentials-and-secrets.md).

## Before promoting output

Inspect the diff and run the project's checks. A generated instruction or tool
result can be untrusted input, including content retrieved from a repository or
knowledge source. Preserve human or agent approval policies at the point where
work is published, deployed, or sent to another system.
