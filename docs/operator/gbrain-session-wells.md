# Attach gbrain memory wells to Ting sessions

In Ting's workflow builder, add a registered well from the memory resource library,
then attach it to the workflow, stage, or persona. gbrain uses the same resource
node and binding controls as Mimir. The saved workflow retains the backend adapter
and its constructor settings through drag/drop, editing, serialization, and dispatch.
Ting refreshes backend settings from its configured registry when that entry exists.

Ravn sessions receive the existing `mimir_search`, `mimir_read`, `mimir_write`,
`mimir_query`, and ingestion tools backed by the selected adapter. Generated session
instructions explain search-before-write, named write targets, source attribution,
and backend limits. gbrain does not implement Mimir raw-source retrieval or Mimir
lint; its native dream scheduler belongs to the gbrain deployment. Query synthesis
requires a model configured on gbrain.

Operator-managed gbrain entries use the existing dynamic adapter configuration:

```yaml
name: team-brain
kind: remote
role: shared
adapter: ravn.adapters.mimir.gbrain.GBrainMimirAdapter
kwargs:
  mcp_url: https://brain.example.com/mcp
  query_expansion: false
auth_ref: team-brain-token
```

`auth_ref` names a credential in the launching user's existing credential store
with a `token` field, or an attached `integration:<slug>`. Session secret injection
mounts it at `/run/secrets/mimir/<normalized-reference>/token`; the gbrain adapter
reads that file. Tokens are not copied into workflow graphs or session YAML.
Missing credentials, missing integrations, or failed injection fail the launch.
The Registry service itself needs its own configured credential access (for example
`kwargs.api_token_file` or `secret_kwargs_env.api_token`) to inspect a protected well;
that host-side reference is replaced by the session credential file when `auth_ref`
is supplied. Do not put literal tokens in registry kwargs.

Managed local instances are also included in the picker. Their connection descriptors
use private token files and are usable on the deployment host. A cluster or sandbox
session needs a reachable shared endpoint and credentials available in that runtime;
loopback addresses and host file paths do not become portable by attaching a well.
This change does not publish a local instance to the network or deploy updated cluster
images. Existing running sessions retain their original configuration.
