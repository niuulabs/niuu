# Session Lifecycle

## State Machine

```
CREATED --> STARTING --> PROVISIONING --> RUNNING --> STOPPING --> STOPPED --> ARCHIVED
                                            |                       |
                                          FAILED <------------------+
                                            |
                                          ARCHIVED

Restarts:
  STOPPED --> STARTING    (restart)
  FAILED  --> STARTING    (restart)

Restore:
  ARCHIVED --> STOPPED    (restore, then can restart)
```

### Transition Rules

| From | To | Trigger |
|------|----|---------|
| `CREATED` | `STARTING` | `start_session()` |
| `STOPPED` | `STARTING` | `start_session()` (restart) |
| `FAILED` | `STARTING` | `start_session()` (restart) |
| `STARTING` | `PROVISIONING` | Pod manager returns `PodStartResult` |
| `STARTING` | `FAILED` | Contributor pipeline or pod manager raises |
| `PROVISIONING` | `RUNNING` | Background readiness poll succeeds |
| `PROVISIONING` | `FAILED` | Readiness timeout or backend error |
| `RUNNING` | `STOPPING` | `stop_session()` |
| `PROVISIONING` | `STOPPING` | `stop_session()` (cancel provisioning) |
| `STOPPING` | `STOPPED` | Pod manager confirms stop, contributors cleaned up |
| `STOPPING` | `FAILED` | Pod stop or cleanup raises |
| `STOPPED` | `ARCHIVED` | `archive_session()` |
| `FAILED` | `ARCHIVED` | `archive_session()` |
| `CREATED` | `ARCHIVED` | `archive_session()` |
| `ARCHIVED` | `STOPPED` | `restore_session()` |

The `can_start()` method gates startability: only `CREATED`, `STOPPED`, and `FAILED` sessions can start. The `can_stop()` method gates stoppability: only `RUNNING` and `PROVISIONING` sessions can stop.

## Creation Flow

1. **API receives `SessionCreate` request.** Required: name. Optional: model, source (git or local_mount), template_name, preset_id.

2. **Resolve template defaults.** If `template_name` is set and a `TemplateProvider` is configured, the template's first repo and model fill in any unset fields. Templates are config-driven (YAML/CRD), not DB-stored.

3. **Validate repository.** If the source is git with a non-empty repo URL, and repo validation is enabled, the git registry finds the matching `GitProvider` and calls `validate_repo()`. Raises `RepoValidationError` if the repo doesn't exist or isn't accessible.

4. **Create session record.** A `Session` object is created with status `CREATED`, a generated UUID, and the resolved source/model. If a `Principal` is present, `owner_id` and `tenant_id` are set from the JWT claims.

5. **Persist and broadcast.** The session is persisted via `SessionRepository.create()`. An SSE event is published to connected clients.

At this point the session exists but has no running infrastructure. The caller (API route) immediately calls `start_session()`.

## Start Flow (Contributor Pipeline)

The start flow is the core orchestration logic. It takes a persisted session and turns it into a running pod group.

### Step 1: State Transition

Session transitions from its current status to `STARTING`. This is persisted and broadcast.

### Step 2: Build SessionContext

A read-only `SessionContext` is assembled from the start request:

```python
SessionContext(
    principal=principal,           # Authenticated identity (from JWT)
    template_name=template_name,   # Optional workspace template
    profile_name=profile_name,     # Optional forge profile (deprecated)
    terminal_restricted=...,       # Whether terminal is restricted
    credential_names=(...),        # Credentials to mount
    integration_ids=(...),         # Integration connections to enable
    integration_connections=(...), # Pre-fetched connection objects
    resource_config={...},         # CPU/memory/GPU config
)
```

If no integration IDs are specified, all enabled integrations for the user are auto-included.

### Step 3: Run Contributors

Contributors run sequentially. Each receives the `Session` and `SessionContext` and returns a `SessionContribution`:

```python
@dataclass(frozen=True)
class SessionContribution:
    values: dict[str, Any]          # Helm chart values
    pod_spec: PodSpecAdditions | None  # Pod spec fragments
```

The contributor order matters. Later contributors can depend on values set by earlier ones. The configured order:

1. **CoreSessionContributor** -- Session identity (ID, name, model), ingress host, terminal restriction flag. No port dependency.

2. **TemplateContributor** -- Resolves the workspace template. Adds repos, setup scripts, workspace layout, model, system prompt, MCP servers, env vars, and workload config from the template.

3. **GitContributor** -- Resolves the git clone URL using the `GitProvider` registry. Adds authenticated clone URL, branch, and git credentials to the spec.

4. **IntegrationContributor** -- Iterates enabled integration connections. For MCP-type integrations, adds MCP server configs with credential-mapped environment variables. For non-MCP integrations, adds env vars directly.

5. **StorageContributor** -- Provisions the workspace PVC (per-session) and home PVC (per-user) via `StoragePort`. Adds volume and mount specs. Respects admin setting for home directory enablement.

6. **GatewayContributor** -- Fetches gateway config from `GatewayPort`. Adds gateway name, namespace, and JWT/auth config for Skuld's HTTPRoute template.

7. **ResourceContributor** -- Translates user-friendly resource config (e.g., `{"cpu": "4", "memory": "8Gi", "gpu": "1"}`) to K8s-native primitives via `ResourceProvider`. Adds requests, limits, node selectors, tolerations, and runtime class.

8. **IsolationContributor** -- Adds namespace, security context, and network policy for tenant isolation.

9. **SecretInjectionContributor** -- Calls `SecretInjectionPort.pod_spec_additions()` to get CSI driver volumes, mounts, labels, and annotations. Volundr never sees secret values here.

10. **SecretsContributor** -- Adds K8s secret references as environment variables from `env_secret_refs` config.

Additionally, a **LocalMountContributor** is auto-wired for local development scenarios. It adds host path mounts with configurable allowed prefixes and root mount restrictions.

### Step 4: Merge Contributions

All `SessionContribution` objects are deep-merged into a single `SessionSpec`:

- `values` dicts are recursively merged (later values override earlier ones for the same key).
- `PodSpecAdditions` are concatenated: volumes, volume_mounts, and env are appended; labels and annotations are merged; service_account uses the last non-None value.

### Step 5: Start Pods

`PodManager.start(session, spec)` submits the merged spec to the backend:

- **Flux** -- creates a HelmRelease for the Skuld chart
- **Direct K8s** -- applies pod manifests directly via the Kubernetes API
- **Docker** -- runs containers via Docker (local development)

Returns a `PodStartResult` with `chat_endpoint`, `code_endpoint`, and `pod_name`.

### Step 6: Transition to PROVISIONING

Session is updated with endpoints, pod name, and status `PROVISIONING`. This is persisted and broadcast.

### Step 7: Background Readiness Poll

An `asyncio.Task` polls for readiness:

1. Initial delay (default 5 seconds, configurable via `provisioning.initial_delay_seconds`).
2. Calls `PodManager.wait_for_ready(session, timeout)` which blocks until the backend reports ready or the timeout expires (default 300 seconds, configurable via `provisioning.timeout_seconds`).
3. Re-fetches the session to verify it's still in `PROVISIONING` (could have been stopped/deleted concurrently).
4. On success: transitions to `RUNNING`.
5. On timeout or error: transitions to `FAILED` with error detail.

On application restart, `reconcile_provisioning_sessions()` re-launches polling for any sessions stuck in `PROVISIONING`.

## Pod Deployment

A running session pod group contains:

| Container | Image | Purpose |
|-----------|-------|---------|
| Skuld broker | `volundr/skuld` | WebSocket bridge between browser and AI CLI |
| code-server | `linuxserver/code-server` | VS Code in the browser |
| ttyd terminal | `tsl0922/ttyd` | Web terminal |
| Envoy sidecar (optional) | `envoyproxy/envoy` | JWT validation for incoming traffic |

All containers share:

- **Workspace PVC** mounted at `/volundr/sessions/<session_id>/workspace` -- per-session, created by `StorageContributor`
- **Home PVC** mounted at `/volundr/home/<user_id>` -- per-user, persistent across sessions

The Skuld container runs the Claude Code CLI (or Codex CLI) as a child process.

## Stop Flow

1. **Transition to `STOPPING`.** Persisted and broadcast.
2. **Cancel provisioning task.** If a readiness poll is running, it's cancelled.
3. **Stop pods.** `PodManager.stop()` tells the backend to tear down the pod group. Logged but non-blocking if the pods are already gone.
4. **Run contributor cleanup in reverse order.** Each contributor's `cleanup()` method is called. Failures are logged but don't block other contributors. `StorageContributor.cleanup()` archives the workspace PVC (soft delete, PVC is relabeled not destroyed).
5. **Transition to `STOPPED`.** Endpoints are cleared. Persisted and broadcast.
6. **Auto-create chronicle** (triggered by Skuld's shutdown report or by the API on session stop).

## Chronicle Creation

Chronicles provide session continuity for reforge workflows.

### From Broker Report

When Skuld shuts down, it sends a POST to the Volundr API with:

```json
{
  "session_id": "...",
  "summary": "Implemented the user authentication flow...",
  "key_changes": ["Added JWT validation middleware", "Created login page"],
  "unfinished_work": "Error handling for expired tokens",
  "duration_seconds": 3600
}
```

`ChronicleService.create_or_update_from_broker()` is idempotent:

- If a `DRAFT` chronicle already exists for this session, it's enriched with the broker data.
- Otherwise, a new chronicle is created from the session's current state (project, repo, branch, model, config snapshot, token usage) and enriched with the broker data.

### Chronicle Data

```python
Chronicle(
    session_id=...,
    project="volundr",          # Derived from repo URL
    repo="github.com/org/repo",
    branch="feature/auth",
    model="claude-sonnet-4-20250514",
    config_snapshot={...},      # Session config at creation time
    summary="...",              # From Skuld broker report
    key_changes=[...],          # From Skuld broker report
    unfinished_work="...",      # From Skuld broker report
    token_usage=45000,
    cost=Decimal("0.45"),
    duration_seconds=3600,
    parent_chronicle_id=...,    # Set during reforge
)
```

### Timeline

Timeline events are collected throughout the session lifetime and stored via `TimelineRepository`:

- **Session events** -- start, stop
- **Message events** -- user/assistant messages with token counts
- **File events** -- created, modified, deleted with insertions/deletions
- **Git events** -- commits with hashes
- **Terminal events** -- commands with exit codes
- **Error events**

The timeline is aggregated into:

- `FileSummary` -- deduplicated file changes (new/modified/deleted, total insertions/deletions)
- `CommitSummary` -- commit hash, message, wall clock time
- `token_burn` -- token usage bucketed into 5-minute intervals

## Reforge

Reforge creates a new session from an existing chronicle, preserving the development context chain.

1. Fetch the chronicle by ID.
2. Extract `config_snapshot` (name, model, repo, branch).
3. Create a new session with `name = "{original_name} (reforged)"` and the same git source config.
4. The new session's chronicle will have `parent_chronicle_id` set, linking it to the parent chronicle.

The `get_chain()` method walks `parent_chronicle_id` links to build the full reforge chain from oldest ancestor to current.

## Archive and Restore

**Archive:** Stops the session if running, then sets status to `ARCHIVED` with a timestamp. Archived sessions are excluded from default list queries.

**Restore:** Moves an `ARCHIVED` session back to `STOPPED`. From there it can be restarted normally.

**Bulk archive:** `archive_stopped_sessions()` archives all sessions in `STOPPED` status.

## Delete

Deletion is aggressive but safe:

1. Cancel any active provisioning task.
2. If running or provisioning, attempt to stop pods. Pod stop failures are logged but don't prevent deletion.
3. Run contributor cleanup in reverse order.
4. Delete the session record.
5. Broadcast deletion event.
