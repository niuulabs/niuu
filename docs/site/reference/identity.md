# Identity Reference

Niuu is designed to support IDP-backed identity.

The platform can be configured with OIDC providers such as Keycloak, Entra ID, or Okta, depending on the deployment profile and adapters enabled.

## Operator concerns

- Who can create sessions
- Who can access credentials
- Which tenants and roles apply
- Whether a deployment is development-only or production-ready

Use allow-all or no-auth modes only for local development and isolated demos.
