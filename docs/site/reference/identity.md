# Identity and authorization

Authentication establishes who is calling. Authorization decides what that
identity may access. Provider authentication establishes a runtime's right to
call a model. These are separate checks.

## Local and shared deployments

Local mini mode is intended for a single OS user on loopback. Its convenient
local access is not a shared-deployment identity policy. Binding the host to a
network interface does not add OIDC, tenancy, or credential isolation.

Shared deployments use the configured identity and authorization adapters and
their gateway/IDP integration. Configure the actual issuer, client, audience,
redirect URLs, and role mapping for that deployment; there is no universal set
of identity values to copy from another installation.

## Verify access from two identities

After configuring login, create a session under one account. Under a second
account, verify that session and credential access match the intended policy.
Repeat against the API, not just the UI. Hiding a control in the browser does not
establish server-side authorization.

Workload identity is another boundary: an OpenShell sandbox can use a SPIFFE
identity to request a scoped provider grant. It is not an operator's browser
login. See [OpenShell](../operations/openshell-runtime.md) and
[credentials](credentials-and-secrets.md).
