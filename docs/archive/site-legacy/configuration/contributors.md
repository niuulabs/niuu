# Session Contributors

The session contributor pipeline assembles the full pod spec for a session. Contributors run sequentially in config order. Each wraps one or more ports and produces Helm values and/or pod spec additions. Cleanup runs in reverse order on stop/delete.

## Default Pipeline

| # | Contributor | What it does | Key kwargs |
|---|-------------|-------------|------------|
| 1 | `CoreSessionContributor` | Session identity, ingress host, terminal flags | `base_domain`, `ingress_enabled` |
| 2 | `TemplateContributor` | Workspace template, repos, setup scripts, env vars | (uses TemplateProvider, ProfileProvider) |
| 3 | `GitContributor` | Authenticated git clone URL | (uses GitProviderRegistry) |
| 4 | `IntegrationContributor` | MCP servers and env vars from integrations | (uses IntegrationRegistry) |
| 5 | `StorageContributor` | PVC provisioning (workspace + home) | `home_enabled` |
| 6 | `GatewayContributor` | Gateway API config (HTTPRoute, JWT) | (uses GatewayPort) |
| 7 | `ResourceContributor` | CPU/memory/GPU translation to K8s primitives | (uses ResourceProvider) |
| 8 | `IsolationContributor` | Network policies, security context | -- |
| 9 | `SecretInjectionContributor` | CSI volumes for secret mounting | (uses SecretInjectionPort, CredentialStorePort) |
| 10 | `SecretsContributor` | Ephemeral session secrets via SecretRepository | (uses SecretRepository) |

## Helm Configuration

```yaml
sessionContributors:
  - adapter: "volundr.adapters.outbound.contributors.core.CoreSessionContributor"
    kwargs:
      base_domain: "sessions.example.com"
      ingress_enabled: true
  - adapter: "volundr.adapters.outbound.contributors.template.TemplateContributor"
    kwargs: {}
  - adapter: "volundr.adapters.outbound.contributors.git.GitContributor"
    kwargs: {}
  - adapter: "volundr.adapters.outbound.contributors.integration.IntegrationContributor"
    kwargs: {}
  - adapter: "volundr.adapters.outbound.contributors.storage.StorageContributor"
    kwargs:
      home_enabled: true
  - adapter: "volundr.adapters.outbound.contributors.gateway.GatewayContributor"
    kwargs: {}
  - adapter: "volundr.adapters.outbound.contributors.resource.ResourceContributor"
    kwargs: {}
  - adapter: "volundr.adapters.outbound.contributors.isolation.IsolationContributor"
    kwargs: {}
  - adapter: "volundr.adapters.outbound.contributors.secret_injection.SecretInjectionContributor"
    kwargs: {}
  - adapter: "volundr.adapters.outbound.contributors.secrets.SecretsContributor"
    kwargs: {}
```

## How Contributors Work

Each contributor implements two methods:

- `contribute(session, context) -> SessionContribution` — returns Helm values and pod spec additions
- `cleanup(session, context) -> None` — cleanup on stop/delete (optional)

The SessionContext carries request-level metadata: principal, template name, profile name, credential names, integration IDs, resource config.

All contributions are deep-merged in order. Later contributors can override earlier ones.

## Writing a Custom Contributor

1. Implement the `SessionContributor` ABC.
2. Define a `name` property.
3. Implement `contribute()` returning `SessionContribution(values={...})`.
4. Accept needed ports via constructor kwargs. Use `**_extra` for unused injected kwargs.
5. Add to `sessionContributors` list in config.

```python
from volundr.ports.session_contributor import SessionContributor, SessionContribution

class MyContributor(SessionContributor):
    def __init__(self, my_setting: str = "default", **_extra):
        self._setting = my_setting

    @property
    def name(self) -> str:
        return "my-contributor"

    async def contribute(self, session, context) -> SessionContribution:
        return SessionContribution(values={
            "extraEnv": [{"name": "MY_VAR", "value": self._setting}]
        })
```
