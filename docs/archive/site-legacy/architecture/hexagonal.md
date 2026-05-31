# Hexagonal Architecture

Volundr uses hexagonal architecture (ports and adapters) to keep business logic independent of infrastructure. The domain layer defines abstract interfaces (ports). Infrastructure code implements them (adapters). The composition root wires everything together at startup.

## Directory Structure

```
src/volundr/
├── domain/
│   ├── models.py              # Domain models (Session, Chronicle, User, Tenant, etc.)
│   ├── ports.py               # All port interfaces (abstract base classes)
│   └── services/
│       ├── session.py          # Session lifecycle orchestration
│       ├── chronicle.py        # Chronicle management, reforge, timeline
│       ├── git_workflow.py     # PR creation, merge, CI status
│       ├── tenant.py           # Tenant hierarchy, default tenant
│       ├── token.py            # Token usage recording and cost
│       ├── stats.py            # Aggregate dashboard statistics
│       ├── repo.py             # Repository listing across providers
│       ├── credential.py       # Credential store operations
│       ├── preset.py           # User-created runtime config presets
│       ├── prompt.py           # Saved prompts
│       ├── tracker.py          # Issue tracker operations
│       ├── tracker_factory.py  # Per-user issue tracker instantiation
│       ├── event_ingestion.py  # Multi-sink event dispatch
│       ├── integration_registry.py  # Integration type catalog
│       ├── user_integration.py # Per-user provider factory
│       ├── mcp_injection.py    # MCP server configuration injection
│       ├── mount_strategies.py # Secret mount type strategies
│       ├── secret_mount.py     # Secret mount spec resolution
│       ├── profile.py          # Profile listing with session counts
│       ├── template.py         # Workspace template operations
│       └── workspace.py        # Workspace PVC management
├── adapters/
│   ├── inbound/               # REST API routes (FastAPI routers)
│   │   ├── rest.py            # Sessions, chronicles, timeline, stats, SSE
│   │   ├── rest_git.py        # Git workflow endpoints
│   │   ├── rest_profiles.py   # Profiles and templates
│   │   ├── rest_presets.py    # Presets
│   │   ├── rest_prompts.py    # Saved prompts
│   │   ├── rest_tenants.py    # Tenants and users
│   │   ├── rest_credentials.py # Credential management
│   │   ├── rest_secrets.py    # MCP servers, K8s secrets
│   │   ├── rest_events.py     # Event pipeline
│   │   ├── rest_integrations.py # Integration connections
│   │   ├── rest_tracker.py    # Issue tracker
│   │   ├── rest_resources.py  # Cluster resources
│   │   ├── rest_admin_settings.py # Admin settings
│   │   └── auth.py            # Auth dependency injection
│   └── outbound/              # Infrastructure adapters
│       ├── contributors/      # Session contributor pipeline
│       │   ├── core.py        # Session identity, ingress, terminal
│       │   ├── template.py    # Workspace template resolution
│       │   ├── git.py         # Git clone URL and credentials
│       │   ├── integrations.py # MCP servers and env from integrations
│       │   ├── storage.py     # PVC provisioning
│       │   ├── gateway.py     # Gateway API HTTPRoute config
│       │   ├── resource.py    # CPU/memory/GPU translation
│       │   ├── isolation.py   # Namespace, security context
│       │   ├── secrets.py     # K8s secret env refs
│       │   └── local_mount.py # Host path mounts (local dev)
│       ├── postgres.py        # PostgresSessionRepository
│       ├── postgres_chronicles.py
│       ├── postgres_timeline.py
│       ├── postgres_tokens.py
│       ├── postgres_stats.py
│       ├── postgres_presets.py
│       ├── postgres_prompts.py
│       ├── postgres_tenants.py
│       ├── postgres_users.py
│       ├── pg_event_sink.py   # PostgreSQL event sink
│       ├── rabbitmq_event_sink.py
│       ├── otel_event_sink.py
│       ├── broadcaster.py     # In-memory SSE broadcaster
│       ├── github.py          # GitHub GitProvider + GitWorkflowProvider
│       ├── gitlab.py          # GitLab GitProvider + GitWorkflowProvider
│       ├── git_registry.py    # Multi-provider git registry
│       ├── flux.py            # Flux-based PodManager (HelmRelease)
│       ├── direct_k8s_pod_manager.py  # Direct K8s PodManager
│       ├── k8s_storage.py     # K8s PVC StoragePort
│       ├── identity.py        # OIDC IdentityPort adapters
│       ├── authorization.py   # AuthorizationPort (Cerbos, allow-all)
│       ├── vault_credential_store.py
│       ├── infisical_credential_store.py
│       ├── file_credential_store.py
│       ├── memory_credential_store.py
│       ├── infisical_secret_injection.py
│       ├── memory_secret_injection.py
│       ├── static_resource_provider.py
│       ├── config_profiles.py  # YAML-driven ProfileProvider
│       ├── config_templates.py # YAML-driven TemplateProvider
│       ├── config_mcp_servers.py
│       ├── pricing.py         # Hardcoded model pricing
│       ├── memory_secrets.py
│       ├── memory_secret_repo.py
│       ├── memory_integrations.py
│       ├── jira.py            # Jira IssueTrackerProvider
│       └── linear.py          # Linear IssueTrackerProvider
├── skuld/                     # Skuld broker (separate FastAPI app)
│   ├── broker.py              # WebSocket broker, REST endpoints
│   ├── transport.py           # CLI transport abstractions
│   ├── channels.py            # Output channel registry
│   ├── service_manager.py     # Multi-session management
│   └── config.py              # Skuld-specific settings
├── config.py                  # Configuration classes (Settings, all sub-configs)
├── main.py                    # Composition root (wires everything)
├── utils.py                   # import_class() and shared utilities
└── infrastructure/
    └── database.py            # asyncpg pool management, schema creation
```

## Layer Rules

Three rules, no exceptions:

1. **Domain imports nothing from adapters.** `domain/models.py`, `domain/ports.py`, and all files under `domain/services/` never import from `adapters/`. The domain layer defines the interfaces; it does not know what implements them.

2. **Adapters implement ports and import domain models.** An adapter like `postgres.py` imports `SessionRepository` (the port it implements) and `Session` (the domain model it persists). It never imports from other adapters.

3. **`main.py` is the composition root.** It imports from everywhere -- domain services, ports, and adapters -- and wires them together. This is the only place where concrete adapter classes are referenced by the application startup code.

## All Ports

Every port is an abstract base class defined in `domain/ports.py`. Here is the complete list:

| Port | Methods | Purpose |
|------|---------|---------|
| `SessionRepository` | create, get, list, update, delete | Session persistence |
| `ChronicleRepository` | create, get, get_by_session, list, update, delete, get_chain | Chronicle persistence and reforge chain traversal |
| `TimelineRepository` | add_event, get_events, get_events_by_session, delete_by_chronicle | Timeline event storage |
| `PodManager` | start, stop, status, wait_for_ready | Session pod lifecycle |
| `StatsRepository` | get_stats | Aggregate dashboard statistics |
| `TokenTracker` | record_usage, get_session_usage | Token usage recording |
| `PricingProvider` | get_price, list_models | Model pricing and metadata |
| `GitProvider` | provider_type, name, orgs, supports, validate_repo, parse_repo, get_clone_url, list_repos, list_branches | Git repository operations for a single provider endpoint |
| `GitWorkflowProvider` | create_branch, create_pull_request, get_pull_request, list_pull_requests, merge_pull_request, get_ci_status | PR and CI workflow operations |
| `EventBroadcaster` | publish, subscribe | SSE event fan-out |
| `EventSink` | emit, emit_batch, flush, close, sink_name, healthy | Event pipeline sink |
| `SessionEventRepository` | get_events, get_event_counts, get_token_timeline, delete_by_session | Event query port |
| `ProfileProvider` | get, list, get_default | Read-only forge profiles |
| `MutableProfileProvider` | (extends ProfileProvider) create, update, delete | Writable profiles |
| `TemplateProvider` | get, list | Read-only workspace templates |
| `SavedPromptRepository` | create, get, list, update, delete, search | Saved prompts |
| `PresetRepository` | create, get, get_by_name, list, update, delete, clear_default | Runtime presets |
| `MCPServerProvider` | list, get | MCP server configs |
| `SecretManager` | list, get, create | K8s secret management |
| `IssueTrackerProvider` | provider_name, check_connection, search_issues, get_recent_issues, get_issue, update_issue_status | External issue tracker |
| `IntegrationRepository` | list_connections, get_connection, save_connection, delete_connection | Integration connection storage |
| `ProjectMappingRepository` | create, list, get_by_repo, delete | Repo-to-tracker project mappings |
| `TenantRepository` | create, get, get_by_path, list, get_ancestors, update, delete | Tenant hierarchy |
| `UserRepository` | create, get, get_by_email, list, update, delete, add_membership, get_memberships, get_members, remove_membership | Users and tenant membership |
| `IdentityPort` | validate_token, get_or_provision_user | JWT validation and JIT provisioning |
| `AuthorizationPort` | is_allowed, filter_allowed | Action-level authorization |
| `SecretRepository` | store_credential, get_credential, delete_credential, list_credentials, provision_user, deprovision_user, create_session_secrets, delete_session_secrets | Vault/OpenBao operations |
| `StoragePort` | provision_user_storage, create_session_workspace, archive_session_workspace, delete_workspace, get_user_storage_usage, deprovision_user_storage, list_workspaces, list_all_workspaces, get_workspace_by_session | PVC lifecycle |
| `GatewayPort` | get_gateway_config | Gateway API routing config |
| `CredentialStorePort` | store, get, get_value, delete, list, health_check | Pluggable credential storage |
| `SecretMountStrategy` | secret_type, default_mount_spec, validate | Per-type secret mount logic |
| `SecretInjectionPort` | pod_spec_additions, provision_user, deprovision_user | CSI driver pod spec generation |
| `ResourceProvider` | discover, translate, validate | Cluster resource discovery and translation |
| `SessionContributor` | name, contribute, cleanup | Contributor pipeline element |

## Dynamic Adapter Loading

Adapters are loaded at runtime from fully-qualified class paths specified in YAML config. This is the `import_class()` mechanism:

```python
# src/volundr/utils.py
def import_class(dotted_path: str) -> type:
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)
```

Configuration specifies the adapter class and any kwargs:

```yaml
pod_manager:
  adapter: "volundr.adapters.outbound.flux.FluxPodManager"
  kwargs:
    namespace: "volundr-sessions"
    chart_name: "skuld"

identity:
  adapter: "volundr.adapters.outbound.identity.OIDCIdentityAdapter"
  kwargs:
    issuer_url: "https://keycloak.example.com/realms/volundr"
    audience: "volundr-api"
  secret_kwargs_env:
    client_secret: "OIDC_CLIENT_SECRET"
```

The composition root (`main.py`) uses this pattern for every infrastructure adapter:

```python
def _create_pod_manager(settings: Settings) -> PodManager:
    pm_cfg = settings.pod_manager
    cls = import_class(pm_cfg.adapter)
    kwargs = _resolve_secret_kwargs(pm_cfg.kwargs, pm_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    return instance
```

`secret_kwargs_env` maps kwarg names to environment variable names. This lets sensitive values (API keys, client secrets) come from the environment rather than config files.

### Contributor Wiring

Contributors are slightly different. They receive both config kwargs and injected port instances, because they need to call other ports (storage, git, gateway, etc.):

```python
def _create_contributors(settings: Settings, **ports: object) -> list[SessionContributor]:
    contributors = []
    for cfg in settings.session_contributors:
        cls = import_class(cfg.adapter)
        resolved_kwargs = _resolve_secret_kwargs(cfg.kwargs, cfg.secret_kwargs_env)
        # Merge config kwargs with injected ports
        kwargs = {**resolved_kwargs, **ports}
        instance = cls(**kwargs)
        contributors.append(instance)
    return contributors
```

Each contributor constructor accepts the ports it needs by name and ignores the rest via `**_extra`:

```python
class StorageContributor(SessionContributor):
    def __init__(
        self,
        *,
        storage: StoragePort,
        admin_settings: dict,
        **_extra: object,  # Ignore ports this contributor doesn't need
    ):
        self._storage = storage
        self._admin_settings = admin_settings
```

This means adding a new contributor is:

1. Write the class, accepting its ports by name.
2. Add the class path to the `session_contributors` list in YAML config.
3. If the contributor needs a new port, pass it in `_create_contributors()`.

No match/case chains. No if/else adapter selection. The config declares what to load; `import_class()` loads it.

### Adding a New Adapter

To swap out an infrastructure backend (say, replacing Vault with a new secret store):

1. Write a new class that implements the port (`CredentialStorePort`).
2. Put it anywhere in the `adapters/outbound/` directory.
3. Update the YAML config to point to the new class:

```yaml
credential_store:
  adapter: "volundr.adapters.outbound.my_new_store.MyNewCredentialStore"
  kwargs:
    endpoint: "https://newsecrets.example.com"
```

No code changes to `main.py`, no changes to the domain layer, no changes to any other adapter.
