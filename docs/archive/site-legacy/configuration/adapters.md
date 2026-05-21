# Dynamic Adapters

Volundr uses a dynamic adapter pattern for most infrastructure components. Config specifies what class to load and what arguments to pass. No factory functions, no match/case chains.

## How It Works

1. Config specifies a fully-qualified class path in the `adapter` key.
2. Remaining keys under `kwargs` are passed as `**kwargs` to the constructor.
3. Some adapters accept injected ports (e.g., StoragePort, CredentialStorePort). These are wired automatically by the application at startup.
4. Sensitive kwargs can come from Kubernetes Secrets via `secretKwargs` (Helm) or `secret_kwargs_env` (config.yaml).

## Import Mechanism

The container resolves the class at startup:

```python
module_path, class_name = dotted_path.rsplit(".", 1)
module = importlib.import_module(module_path)
cls = getattr(module, class_name)
instance = cls(**kwargs)
```

No registration step. If the class exists on the Python path, it works.

## Writing Your Own Adapter

1. Implement the relevant port interface (e.g., `PodManager`, `CredentialStorePort`).
2. Accept your config as constructor kwargs.
3. Use `**_extra` to ignore injected kwargs you don't need.
4. Set the `adapter` key in config to your class path.

```python
from volundr.ports.credential_store import CredentialStorePort

class MyCredentialStore(CredentialStorePort):
    def __init__(self, api_url: str, api_key: str, **_extra):
        self._url = api_url
        self._key = api_key

    async def get_credential(self, user_id: str, name: str) -> str | None:
        ...
```

```yaml
credentialStore:
  adapter: "mypackage.creds.MyCredentialStore"
  kwargs:
    api_url: "https://secrets.internal"
  secretKwargs:
    api_key:
      secretName: my-creds
      key: api-key
```

## All Adapter-Driven Components

| Component | Port | Default Adapter |
|-----------|------|-----------------|
| Pod manager | PodManager | FluxPodManager |
| Identity | IdentityPort | AllowAllIdentityAdapter |
| Authorization | AuthorizationPort | AllowAllAuthorizationAdapter |
| Credential store | CredentialStorePort | MemoryCredentialStore |
| Secret injection | SecretInjectionPort | InMemorySecretInjectionAdapter |
| Storage | StoragePort | InMemoryStorageAdapter |
| Gateway | GatewayPort | InMemoryGatewayAdapter |
| Resource provider | ResourceProvider | StaticResourceProvider |
| Session contributors | SessionContributor | (10 default contributors) |

Each component page in this section documents the available adapters and their configuration.
