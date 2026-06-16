# Credentials And Secrets

Configure credentials carefully before using real repositories or cloud providers.

Niuu supports pluggable credential and secret backends. The platform should not require operators to paste raw secrets into session prompts or knowledge pages.

## Guidance

- Scope credentials to the minimum required access.
- Prefer external secret systems such as OpenBao, Vault, or Infisical for production.
- Avoid broad home-directory mounts in demo or shared environments.
- Review session logs and screenshots before publishing them.

## Related

- [Security and permissions](../operations/security-and-permissions.md)
- [Configuration](configuration.md)
