# Frequently asked questions

## Do I have to install every service?

No. Start with the local host and one session. Add services for the capabilities
you need. [Choose your next step](../get-started/path-from-small-to-autonomous.md)
by the result you want, rather than following a mandatory platform ladder.

## Can agents use the same interfaces as humans?

Yes. APIs and channels can be used by agents as well as people. Skuld adapts
runtime communication to those interfaces; shared Niuu libraries provide room
and mesh mechanics. See [architecture](../concepts/platform-model.md).

## Is Ravn required to run Claude Code or Codex?

Ravn is one agent runtime. Skuld supports other runtimes, including Claude Code
and Codex. Ordinary sessions do not need to become Ravn residents. Ravn adds its
own judgment, learning, and resident behavior when you choose to use it.

## Does self-hosted mean no data leaves my machine?

No. Niuu runs on your infrastructure, but a configured cloud model or external
tool sends requests to that service. Check the endpoints and credentials used by
the actual runtime. Bifröst affects clients configured to call it.

## Is a local workspace a sandbox?

A directory alone is not a sandbox. Local-process sessions run under the host
account. OpenShell and Kubernetes have separate configured execution boundaries.
See [execution permissions](../operations/security-and-permissions.md).

## Does stopping a session preserve its files?

The local-process backend retains the workspace. OpenShell cleanup and Kubernetes
storage behavior differ. Verify the selected backend's contract and preserve
required output before deleting or stopping resources that own its storage.

## Has every deployment path been tested?

No. The [verification record](../operations/quickstart-verification.md) states
what ran successfully, what is covered by CI, and what still needs live proof.
A successful docs build or local session does not certify a cluster deployment.
