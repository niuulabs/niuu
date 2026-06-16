# Security And Permissions

Treat Niuu as a powerful automation platform.

Agents can read files, run commands, call tools, and use credentials according to the runtime and infrastructure you configure.

## Operator guidance

- Use throwaway repos for demos.
- Scope credentials tightly.
- Keep secrets out of prompts and knowledge pages.
- Review diffs before promoting work.
- Use OIDC and authorization in shared environments.
- Prefer dedicated machines, namespaces, or clusters for untrusted automation.

## Production baseline

Production deployments should use identity, authorization, secret management, TLS, backups, resource limits, and audit-friendly logs.
