# Integrations and credentials

An integration describes how Niuu reaches another system. A credential supplies
the authentication material for that connection. Attaching an integration to a
session and granting its credential are related but distinct operations.

## Choose the connection by the task

| Task | Required connection | Verification |
| --- | --- | --- |
| Clone or review a hosted repository | Configured Git provider and repository access | List/read the intended repository, then test the permitted write operation separately |
| Import external work | Configured tracker | Read the intended project and inspect imported scope |
| Call a hosted model | Runtime login or provider API credential | Send a small real inference request |
| Read shared knowledge | Mímir endpoint or local adapter and mount | Retrieve a known page |
| Receive events | Configured event transport | Observe a known event at the consumer |

## Grant only the connection needed

Configure the provider in the owning service, store its credential through the
configured secret backend, and select it in the launch when required. A provider
appearing in Settings does not prove that the session can access it.

Test from the environment that actually runs the agent. A host's Git or Claude
login may work locally while a remote sandbox has no equivalent identity.
[Credentials and secrets](credentials-and-secrets.md) covers the supported local
Claude path and remote login limitations.

When a connection fails, separate DNS/network errors, expired authentication,
insufficient permissions, and unsupported runtime behavior before changing it.
