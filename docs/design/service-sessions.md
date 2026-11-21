# Service sessions, and native clients that reach them

Status: **proposal, revision 2, nothing built.** 2026-11-21. It needs approval before any code is written.

## The problem

Owners run services, such as an app's API server and a worker beside it, inside Forge agent
sessions today: an agent starts them as processes bound to `127.0.0.1`. That works while the
agent session lives, but it has three problems:

- nothing off the machine can reach the service, except a browser through live preview;
- the service lives and dies with the agent session that started it;
- only an agent knows how to restart it.

The owner then wants a **native app** (iOS, iPadOS or macOS) on their own devices to use that
service. The same app often also wants Forge's own chat APIs: conversation paging, the live
session WebSocket, sending messages and delivery status. A native client has no browser
session, so it needs a token story of its own. Today an authenticated Forge answers
`401 Jwt is missing` to a native client that has no OIDC login.

The design has two parts. They ship separately, and the second is useful on its own:

1. **Service sessions.** A Forge session that runs a service (one or more containers or
   commands, with ports) and no agent. Forge manages it like any session (create, start, stop,
   status and logs), places it on a Kubernetes cluster or on a registered host, and reaches it
   through the live-preview router.
2. **Native-client auth.** A standard OIDC login for native apps, so one token reaches the
   Forge API, the session WebSocket and service routes.

## Today: what exists and what it lacks

| Piece | What exists | Gap for this design |
|---|---|---|
| `sessions.workload_type`, `workload_config` (migrations 000035, 000054) | A free label, `session` by default. Its only uses: `REMOVED_WORKLOAD_TYPES` (`ravn_flock`), `storage.home.excludeWorkloadTypes` and `session_liveness.exempt_workload_types` | No typed workload; nothing launches a user image |
| Agentless Skuld | A workflow run's **root** is a Skuld with no agent or transport (`workflow_steps.root`, `skuld/broker.py` around line 1100). It hosts a room and its readiness | Tied to workflow runs. No general "no agent" mode |
| Containers beside Skuld | `PodSpecAdditions.extra_containers` → the skuld chart's `extraContainers` (Flux). VM and Linux host runtimes turn them into guest sidecars (`ssh_vm_runtime.py`, rootless Podman on hosts) | Only operator launch specs can set them: `WorkloadConfigContributor` passes four `broker.*` keys from a request and nothing else. `DirectK8sPodManager` ignores `extra_containers`. Macs run no containers at all |
| Readiness | HelmRelease `Ready`, through nginx `/ready` → broker `/ready` | "Running" means "Skuld is up". There is no service health |
| Logs | `GET /sessions/{id}/logs` proxies Skuld's ring buffer. devrunner services have `/api/services/{name}/logs` in Skuld | No container log route for a session |
| Placement | `PlacementPodManager`: the pool (`host_selector`, compute profile) or the cluster. `ResourceContributor` turns `gpu`/`gpu_type` into `nvidia.com/gpu`, a toleration, a runtimeClass and a product selector | It puts the GPU budget on devrunner, not on a service container. Hosts have no GPU request |
| Live preview (`docs/operator/live-preview.md`) | `https://<port>--<session-id>.<preview domain>/` → gateway OIDC → Forge Envoy (`keycloak_preview`) → `niuu.preview_proxy` (Cedar `attach` + room role `owner`) → Skuld `/preview/<port>` → loopback. WebSockets are bridged; `Authorization` and cookies are stripped | The gateway `SecurityPolicy` has only `oidc` (cookie session), so **a bearer-only request is redirected to the IdP**. Ports are whatever Skuld discovers, not declared |
| Native auth | Forge's Envoy verifies `Authorization: Bearer` (the configured IdP realm, audience `volundr-api`) on the REST API and the session gateway's `/s/{id}/…`. `GET /api/v1/identity/auth/config` advertises the CLI client (`authDiscovery.cliClientId`, `volundr-cli`) with the device flow | No public client for native apps, and no discovery field for one. A deployment that leaves `authDiscovery.issuer` unset answers `404 Auth discovery not configured` |
| Resident runtimes | A service-like lifecycle (deploy, restart, suspend, logs) for OpenClaw and Hermes | A separate table and API, and not a session. Not reused here, but its `ResidentContainerSpec` process model is the template for the config below |

## Decisions

1. **A service session is an ordinary Forge session with `workload_type: service`.** It has a
   name, an owner, a project, a status and a stable id; it is listed, started and stopped like
   any session, and an agent with the lifecycle grant drives it through the Forge MCP
   (`create_session`, `start_session`, `stop_session`). It is **not** a resident runtime: those
   are agent engines with their own table.
2. **Skuld stays in the unit, with no agent.** The service runs beside an agentless Skuld, the
   way a workflow run's root does. Skuld is the session's only way in (preview forwarding, health,
   logs), so the preview router, the router credential, Envoy and the Forge registry are reused
   unchanged. A bare Deployment with no Skuld was considered and rejected (see *Alternatives*).
3. **The service and Skuld share one network namespace.** In a pod they are containers of one
   pod; on a Linux host they are rootless Podman containers in the slot's namespace; on a Mac
   they are processes of the slot user. Skuld forwards to `127.0.0.1:<port>`, as live preview
   already does. Service ports are never published on the node or the pod network.
4. **Ports are declared, not discovered.** Only a port the service config declares with
   `expose: true` gets a route. Live preview's scanning stays as it is for agent sessions.
5. **Exposure reuses the preview router and domain**: `https://<port>--<session-id>.<preview domain>/`.
   The session id is stable across stop, start and config updates, so a client can store the URL.
   Changing the image is an update and a restart, never a new session (see *Lifecycle*).
6. **Owner only, as live preview is.** Devices are the owner's logins (decision 8), so "which
   devices" is "which devices hold a login of the owner", and each one is revoked on its own.
7. **One login for every native need.** The app holds an OIDC access token from the configured
   IdP with audience `volundr-api`. That same token reaches the REST API, the session WebSocket
   and service routes. Nothing new is minted, and no custom token kind is added (architecture
   rule: delegate to OIDC).
8. **Native apps use a public client with Authorization Code + PKCE**, through
   `ASWebAuthenticationSession` on iOS and macOS, with the refresh token in the Keychain. The
   device flow is allowed on the same client for a device with no browser. No PATs on devices
   (see *Native-client auth*).
9. **No fallbacks.** A declared port that never listens, a health check that fails, an image
   the cluster cannot pull, a GPU the placement cannot give: each is a visible status with the
   reason, never a quiet "running".

## 1. The workload and its config

`POST /api/v1/forge/sessions` with `workload_type: "service"` and the config below in
`workload_config.service`. The same shape can be saved as a launch spec
(`launch_specs.workload_type = 'service'`), so a service is defined once and started by name.
A typed model (`ServiceWorkloadConfig`, pydantic) validates it at create time, and a new
`ServiceWorkloadContributor` turns it into the spec. `WorkloadConfigContributor` keeps refusing
raw chart values from requests: a request gets this schema only, never `extraContainers`.

The example is an API with a worker beside it, which the API calls on loopback:

```yaml
workload_type: service
workload_config:
  service:
    placement:
      target: cluster              # cluster | host
      host_selector: null          # with target: host, the existing HostSelector (tags, match)
    processes:
      - name: api
        image: registry.example.com/app-api:1a2b3c4   # required on cluster and Linux hosts; refused on a Mac
        command: ["app", "serve"]                       # optional; image entrypoint otherwise
        args: []
        workdir: null
        ports:
          - name: http
            port: 8000
            protocol: http                   # http (WebSockets included) | tcp (internal only)
            expose: true                     # gets a service route
        env:
          - name: APP_WORKER_URL
            value: http://127.0.0.1:8720
        secret_env:                          # owner credentials, through secret injection
          - name: APP_DATABASE_URL
            credential: app-database-url     # a credential the owner stored (OpenBao/Infisical)
            key: url
        volumes:
          - name: data
            mount_path: /data
        health:
          http: { port: 8000, path: /healthz }
          initial_delay_seconds: 5
          period_seconds: 10
          failure_threshold: 3
        resources:
          cpu: "1"
          memory: 2Gi
          gpu: 0
          gpu_type: null
      - name: worker
        image: registry.example.com/app-worker:1a2b3c4
        ports: [{ name: http, port: 8720, protocol: http, expose: false }]
        env:
          - { name: APP_API_URL, value: http://127.0.0.1:8000 }
        volumes: [{ name: worker-cache, mount_path: /cache }]
        health: { http: { port: 8720, path: /health } }
        resources: { cpu: "2", memory: 3Gi }
    volumes:
      - name: data
        size: 20Gi                 # Forge creates a PVC owned by this session
        retain: true               # kept on stop; deleted only on delete with cleanup=volumes
      - name: worker-cache
        size: 5Gi
        retain: true
    restart: always                # always | on-failure; see "Restarts"
    source: null                   # optional git source (repo, ref), checked out to the workspace
```

### Fields and their rules

- **`processes`**: one or more. Each becomes a container (cluster, Linux host) or a supervised
  process (Mac). On every placement they share one network namespace, so they reach each other
  on `127.0.0.1`. A process with no `image` is allowed only on a Mac or with a `source`; it runs
  the command in the workspace as the slot user.
- **`ports`**: port numbers are unique across the session and must not be one of Skuld's
  (`skuld.previewExcludedPorts`: the service port, 8080, 8081, Envoy, authz, terminal, mesh).
  A clash is refused at create time, naming the port. `tcp` ports are never exposed. They exist
  for processes in the same session (a database, for example).
- **`env` / `secret_env`**: `secret_env` resolves only the **owner's** stored credentials through
  the configured `SecretInjectionPort` (OpenBao, Infisical or file), exactly as agent sessions
  get theirs. It is never a `secretKeyRef` into the session namespace: that would let any user
  read any Secret in the namespace. A missing credential fails the start, naming it.
- **`volumes`**: each is either Forge-made (`size`; a PVC named after the session and the volume,
  in the session namespace, on the pool's storage class) or `existing_claim: <name>`. An
  existing claim is admitted only if the operator lists it in `service_sessions.claims` with
  the owners who may mount it. A user cannot mount an arbitrary PVC. On a host, a volume is a
  directory under the slot's durable data dir, kept across restarts of the same allocation.
  An NFS share the operator already serves is an `existing_claim` they list.
- **`health`**: an HTTP or TCP check per process. On a cluster it renders a Kubernetes
  `readinessProbe` and `livenessProbe` on the container. On a host Skuld runs the same check
  (see *Status*). A process with exposed ports and no `health` is refused: without one, "running"
  would mean nothing.
- **`resources`**: the existing resource model (`cpu`, `memory`, `gpu`, `gpu_type`), validated by
  the resource provider as today. The difference is that the budget lands on the **service
  container**, not on devrunner. `gpu > 0` adds `nvidia.com/gpu`, the GPU toleration, the
  runtimeClass and `nvidia.com/gpu.product` for `gpu_type`, as `translate_resource_config`
  already does. A GPU on a host is refused until hosts have GPU requests. A pool that cannot
  give the GPU fails the start; it never runs on CPU instead.
- **`restart`**: see *Restarts*.
- **`source`**: optional. Reuses the session git source (clone into the workspace). It is for
  command services on a Mac, which have no images. Otherwise unset.
- **Images** must be pinned (a tag is resolved to a digest at start and recorded on the launch,
  as compute hosts already do with session images). The pull secrets are the operator's
  (`service_sessions.image_pull_secrets`); a request cannot name one.

### What a service session does not get

The contributors that exist for agents are skipped when `workload_type == service`: CLI and model
auth, Bifröst, MCP servers, persona, memory and Mímir, the home PVC, the chronicle, voice, the
devrunner and terminal sidecars, and the mesh. The `forge_session` credential is still minted,
for Skuld's own record (activity, durable log), with no `forge:session:message` grant. Skuld runs
with `cli_type: none`, a new value that generalises the run root's "no transport" path, and
refuses chat input with an error that says the session runs no agent.

### Placement

- **`target: cluster`**: the Forge's Kubernetes pod manager (Flux) renders the skuld chart's
  HelmRelease in its session namespace, with the processes as `extraContainers`. Placement inside
  the cluster uses the existing `nodeSelector`, `tolerations`, `affinity` and `runtimeClassName`
  from the resource provider. A rule such as "keep GPU nodes free for GPU work" is set for
  `gpu: 0` services by operator config (`service_sessions.default_affinity`), never hard-coded.
  A service that must run on a cluster other than the Forge's own runs on a Guild node there;
  this design adds no cross-cluster pod manager.
- **`target: host`**: `PlacementPodManager` routes to the pool through `host_selector`, as agent
  sessions do. On Linux, `ssh_vm_runtime`'s guest sidecars run the images as rootless Podman in
  the slot, under the rules in `architecture.md`: no daemon, no root, the slot's namespace and
  cgroup. On a Mac, processes are commands only. One process per allocation is not enough here,
  so the Darwin runtime supervises each process as its own launchd job of the slot user.
- **`DirectK8sPodManager`** gets `extra_containers` support, or refuses `workload_type: service`
  with the reason. Either is acceptable; silently dropping the containers, as it does today, is
  not.

## 2. Lifecycle in Forge

| Action | Effect |
|---|---|
| Create | Validates the config (ports, credentials, claims, GPU, images). Stores `workload_type` and `workload_config`. Nothing runs yet. |
| Start | The usual pipeline: placement, capacity, contributors, `pod_manager.start`. Images are pinned to digests on the launch. Forge-made PVCs are created the first time and reused after that. |
| Running | Skuld is ready **and** every process with a health check passes it (see *Status*). |
| Update | `PATCH /sessions/{id}/service`: a new image tag, env or resources, as one change. It is validated as at create, stored, and applied with a restart. The session id, and with it the URL, stays the same. |
| Stop | `pod_manager.stop`. The pod or the slot goes; `retain: true` volumes stay. |
| Delete | With `cleanup=volumes` the session's own PVCs go too. Without it they stay and are listed on the session as orphaned, never deleted on a guess. |

### Status

`status` keeps its meaning (`starting`, `running`, `stopped`, `failed`). A new `service` block on
the session (`GET /sessions/{id}` and the session list) reports each process:

```json
"service": {
  "processes": [
    {"name": "api", "state": "healthy", "restarts": 0, "image": "registry.example.com/app-api@sha256:…",
     "ports": [{"name": "http", "port": 8000, "url": "https://8000--<id>.<preview domain>/"}]},
    {"name": "worker", "state": "unhealthy", "restarts": 3,
     "reason": "GET /health → 503", "since": "…"}
  ]
}
```

- **States**: `starting`, `healthy`, `unhealthy` (the check fails), `crashing` (exited and being
  restarted), `exited` (with `restart: on-failure` after a clean exit), and `image_error`.
- **Sources**: on a cluster, the pod's container statuses and probe results, read by the pod
  manager (`status_detail`) on the existing reconcile loop. On a host, Skuld runs the checks and
  reports them on its activity stream.
- **Activity** for a service session is `idle` once healthy and `active` while starting. The
  agent-only states never apply. `service` joins `session_liveness.exempt_workload_types` by
  default: a quiet service is not a dead one. The pod-status reconcile remains the death check.
- **Notifications**: a process going `unhealthy` or `crashing` notifies the owner once per
  episode (the existing notification feed, `kind: error`), and again on recovery.

### Restarts

Kubernetes pods have one `restartPolicy`, and a Deployment needs `Always`, so on a cluster every
process restarts with the kubelet's backoff. `restart: on-failure` is honoured on hosts only and
refused on a cluster, naming the reason. It is not quietly treated as `always`. Every restart is
counted (`volundr.service.process.restarts`, by session and process) and shown as `restarts`.
There is no Forge-side retry cap: the kubelet backoff is the bound, and `crashing` is the loud
signal.

### Logs

- `GET /api/v1/forge/sessions/{id}/services/{process}/logs?since=…&follow=true` streams the
  process's stdout and stderr.
  - Cluster: the pod log API.
  - Linux host: `podman logs` over Forge's SSH connection to the host.
  - Mac: the launchd job's log file.
  - `previous=true` gives the last crashed instance.
- A new optional `PodManager.service_logs(session, process, …)` serves it. A backend without it
  answers `501`, naming the backend.
- A matching Forge MCP tool (`session_service_logs`, scope `forge:session:read`, own sessions
  only) lets the agent developing the service read why a deploy failed.
- There is no default tail length: `since` or `tail_lines` is required, so nothing is cut short
  silently.

## 3. Exposure: service routes

A service route **is** the live-preview route, restricted to declared ports:

```
https://<port>--<service-session-id>.<preview domain>/
```

What changes, and nothing else:

1. **Forge's preview router** (`niuu.preview_proxy`): for a `service` session it admits only a
   port declared with `expose: true`, and answers `404` naming the session's exposed ports for
   any other. Agent sessions keep discovered ports.
2. **Skuld** (`skuld/preview.py`): in a service session the port watcher is replaced by the
   declared list. A declared port that is not listening yet is `503 not listening yet`, not
   `404`. Exposure stays loopback-only.
3. **The session API** lists each exposed port's URL (`service.processes[].ports[].url`), so
   clients and the web UI never build hostnames themselves.
4. **Nothing reaches the service from the platform.** The router keeps stripping
   `Authorization`, `Cookie` and `x-auth-*` and keeps removing `Domain=` from `Set-Cookie`. A
   service with no auth of its own is protected by the route's owner check alone, which is
   enough for a single-owner service and is the point of routing it through Forge.
5. **Native clients**: the gateway accepts a bearer token (section 4). WebSockets need nothing
   new, since they are already bridged both ways.

Not in this revision: a stable vanity hostname (`<name>.svc.<domain>`). The session id is
stable across restarts and updates, so a client can store the URL. Add an alias table later if a
session ever needs replacing.

A service that wants to know who is calling is out of scope. Today the route forwards no
identity. If one is needed, the router would forward a short-lived Forge-signed caller
assertion with the service session as its audience: a follow-up design, not a header the
client could forge.

## 4. Native-client auth

### The flow

The app logs in once per device through the IdP, and that one token reaches every native need:

| Need | Path | Accepts the token today? |
|---|---|---|
| Sessions, history, sending, delivery status | `https://<forge>/api/v1/forge/…` | **Yes**: Forge's Envoy `jwt_authn`, the IdP issuer, audience `volundr-api` |
| Live session WebSocket and broker routes | `wss://<session gateway>/s/{id}/session`, `/s/{id}/api/…` | **Yes**: the skuld chart's `SecurityPolicy` `jwt` (`Authorization` header, or `?access_token` where a header cannot be set) |
| Service routes and live preview | `https://<port>--<id>.<preview domain>/` | **No**: the gateway's `oidc` redirects anything without its cookie. Fixed below |

So an app's agent-chat half needs **no Niuu code change**, only a client to log in with. The
`401 Jwt is missing` a native client sees is that same Envoy asking for exactly this token.

### The IdP client

One public client per app, so each app shows up, and can be revoked, on its own. For Keycloak:

| Setting | Value |
|---|---|
| Realm | The realm Forge's Envoy trusts (`volundr` in the chart's examples) |
| Client id | One per app, e.g. `<app>-apple` |
| Type | Public (no secret), standard flow + **PKCE S256 required** |
| Redirect URIs | `com.example.app:/oauth2/callback` (RFC 8252 private-use scheme from the app's bundle id). If the app has an associated domain: `https://app.example.com/oauth2/callback` as a claimed HTTPS redirect |
| Post-logout redirect | `com.example.app:/oauth2/logout` |
| Device flow | Enabled (OAuth 2.0 Device Authorization Grant), for a device with no usable browser |
| Scopes | `openid profile email offline_access` |
| Mappers | Audience `volundr-api`; the tenant claim (`tenantClaim`, `tenant_id`) and roles claim (`rolesClaim`, `resource_access.volundr.roles`) Forge reads, the same as the web client's |
| Tokens | Short-lived access tokens (minutes); refresh token rotation on (each refresh invalidates the previous one); offline session idle timeout an operator setting |

Creating the client is an IdP change, made where the deployment manages its realm (for
example next to the preview client's setup script, `docs/operator/live-preview.md`), not by Forge.

### In the app

- `ASWebAuthenticationSession` with `prefersEphemeralWebBrowserSession = false`, so the owner
  stays signed in to the IdP across apps. Use AppAuth-iOS or an equivalent PKCE implementation;
  do not hand-roll it.
- Discovery: Forge's `GET /api/v1/identity/auth/config` gains `native_clients`
  (`{"<app>-apple": {...}}`), with values from `authDiscovery.nativeClients` in the volundr
  chart. The app reads the issuer and its client id from there, so they are never compiled in.
  `authDiscovery.issuer` (or the gateway's issuer) must be set for the route to answer.
- Store the refresh token in the Keychain with
  `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`. It never syncs to another device, which is
  what makes each device its own revocable login. Keep the access token in memory.
- Send `Authorization: Bearer <access token>` on every REST call, on the WebSocket upgrade and
  on service routes. Refresh before expiry and on a `401`. A failed refresh signs the device out
  and says so; it never retries the same refresh token.
- A shared Swift `ForgeAuth` module (login, refresh, Keychain) would serve every native Forge
  client.

### Who and which devices

- **Owner only.** A service route admits the session's owner (Cedar `attach` plus room role
  `owner`), as live preview does. Project members and room participants are refused, and so are
  other agent sessions. Sharing a service is a later design.
- **Named devices = IdP sessions.** Each device's login is its own offline session, with the
  client, the device's user agent and its last use. The owner lists and revokes them in the
  IdP's account console, and each refresh token is bound to one device's Keychain. Forge does
  not keep a device list of its own; that would be a second credential system.
- **Not PATs.** A PAT is long-lived (365 days by default), copied by hand, and not bound to a
  device. PAT handling on preview hosts also needs a review before any PAT is used there: Phase 1
  checks that revocation is enforced on the preview path, with a test, and fixes it if not.

### The gateway change (volundr chart)

`charts/volundr/templates/preview-route.yaml`, `SecurityPolicy`:

```yaml
spec:
  jwt:
    providers:
      - name: keycloak
        issuer: {{ .Values.sessionPreview.oidc.issuer }}
        audiences: {{ .Values.sessionPreview.bearer.audiences }}   # ["volundr-api"]
        remoteJWKS: { uri: {{ $jwksUri }} }
  oidc:
    # ... unchanged ...
    passThroughAuthHeader: true     # a request with a valid bearer skips the redirect
```

- Behind `sessionPreview.bearer.enabled` (default `false`, so a current deployment renders
  exactly as today).
- A request with a valid bearer skips the redirect. A request with an invalid one is refused with
  `401`, not redirected, so an app sees an auth error rather than a login page.
- Forge's Envoy (`keycloak_preview`) and the router's owner check are unchanged, and still run.
- Needs an Envoy Gateway whose `SecurityPolicy` OIDC API has `passThroughAuthHeader` (added in
  the 1.x line; the exact minimum is to be confirmed against the deployed version). The chart
  refuses to render `bearer.enabled` without `oidc.issuer`.
- With `passThroughAuthHeader`, Envoy Gateway's `jwt` also accepts a token from any client of the
  realm with audience `volundr-api`. That matches the platform's other routes; the owner check is
  still the authorisation.

## 5. Example: an API plus a worker

One service session with two processes: `api` (port 8000, exposed) and `worker` (port 8720, not
exposed, or exposed only if the app shows its health). They share the session's network
namespace, so each reaches the other on `127.0.0.1`. Two separate sessions would need
session-to-session networking, which this design does not add.

- **Migrations**: run by the image's own start command. Service sessions do not take init
  containers in this revision.
- **Database**, two options:
  - **A, recommended: a database managed outside Forge** (for example a CloudNativePG `Cluster`
    deployed by the cluster's GitOps), with backups. Its connection URL is stored once as the
    owner's credential and reaches the API through `secret_env`. The database's lifecycle stays
    outside Forge, which is right for the one stateful thing worth backing up.
  - **B: a third process.** A `postgres` image with a retained volume and a `tcp` port, never
    exposed. It is simpler, but has no backups or failover, and the data's life is tied to the
    session's volumes. Fine for a scratch copy, not for records that matter.
- **Files**: a Forge-made retained volume, or a share the operator lists in
  `service_sessions.claims`.
- **A worker that needs reference data** (a model, a gallery): a retained volume filled once.
  Until it is there the worker's health check fails, the process is `unhealthy`, and the client
  can show why.
- **GPU**: `resources.gpu: 1` on the worker when it needs one. Sharing a GPU (time-slicing) is the
  cluster's business, not Forge's.
- **Deploys**: the agent developing the service, with the lifecycle grant, calls
  `PATCH /sessions/{id}/service` with the new image tag after CI pushes it, and reads
  `session_service_logs` if the health check fails.
- **The app**: one login. The API base URL comes from the session's
  `service.processes[api].ports[http].url`, found once by the session's name.

## 6. Interim: what works today with no Niuu change

1. **In a browser.** Live preview already serves a server an agent session started, at
   `https://<port>--<agent-session-id>.<preview domain>/`, after IdP sign-in. Limits: the service
   lives and dies with that agent session; native clients cannot use it; owner only.
2. **An app's agent chat.** Bearer tokens already work on the REST API and the session gateway.
   Until a native client exists, an app can be tested with a token from the CLI login
   (`niuu login`, the CLI client's device flow). Do not ship that, and do not use a PAT on preview
   hosts until the Phase 1 check is done.
3. **A native app reaching a service.** There is no clean way without the gateway change. A
   `WKWebView` login on the preview domain, copying its cookies into `URLSession`, would work. It
   is fragile (the cookies are the gateway's and HttpOnly by intent), so it is listed here only to
   reject it.
4. **Rejected**: binding the service to `0.0.0.0` on a shared host and reaching it over a VPN. It
   skips Forge, gives a service with no auth of its own none at all, and exposes it from a shared
   slot.

The smallest change that unblocks native apps is **Phase 1 below**: the IdP client, the gateway
pass-through and discovery. With it, the interim browser route in item 1 also works natively,
before any service-session code exists.

## 7. Alternatives considered

- **A bare Deployment and Service with no Skuld.** It needs a second readiness path, a second
  forwarder target type in the preview router (a ClusterIP, not a Skuld), and a second credential
  check at the target (the router credential is verified by Skuld). It would also not work on
  hosts, which have no Kubernetes Service. Skuld costs one small container and keeps one path.
- **Resident runtimes.** They have the right lifecycle, but they are agent engines with their own
  table, API and UI, and are not sessions. Sessions are what clients, the web UI and agents already
  handle.
- **Plain GitOps (a Helm chart with an ingress).** No Forge involvement, and fine for a released
  service. But a service with no auth of its own then needs its own OIDC first. Deploys go through
  the GitOps repository, not through the agent building the service, and there are no Forge logs
  or status. Service sessions give owner-only access and agent-driven deploys for free. The two
  can coexist: build in a service session, ship through GitOps once the service has its own auth.
- **Forge-issued device tokens.** Rejected: a custom token layer, against `architecture.md`. OIDC
  already gives per-device refresh tokens with revocation.

## 8. What's missing in Niuu, and the effort

Estimates are in agent-days, including tests to the 85% gate and the operator docs.

| Phase | Work | Effort |
|---|---|---|
| **1. Native auth** (useful on its own) | Chart: `sessionPreview.bearer` (`jwt` + `passThroughAuthHeader`), render tests. `authDiscovery.nativeClients` and the `auth/config` field. A test, and a fix if needed, that PAT revocation holds on the preview path. Docs: `docs/operator/native-clients.md`. Creating the IdP client is a separate change to the deployment's realm | 2 |
| **2. Service sessions on a cluster** | `ServiceWorkloadConfig` and validation; `ServiceWorkloadContributor` (containers, ports, env, `secret_env`, volumes and PVCs, probes, resources and GPU on the container); skipping agent contributors; `cli_type: none` in Skuld (generalising the run root); declared ports in the router and Skuld; the `service` status block from container statuses; `PATCH …/service`; liveness exemption and counters; `service_logs` for Flux; the logs route and MCP tool; `DirectK8s` support or refusal; migration (only if the launch-spec table needs a column; `workload_config` is JSONB already) | 9–11 |
| **3. Web UI** | Create from a launch spec; the session page shows processes, health, restarts, URLs and logs instead of a chat | 3 |
| **4. Hosts** | Linux: map processes to guest sidecars, Skuld-run health checks, `podman logs`. Mac: command processes as launchd jobs, `source`, log files | 4–5 |

A cluster-only service reachable from native apps is Phases 1 and 2: about **11–13
agent-days**. Everything is about 18–21.

## 9. Open questions

1. **Placement target.** `target: cluster` is the Forge's own session namespace. A service on a
   different cluster runs on a Guild node there. Is that enough, or is a cross-cluster pod manager
   wanted?
2. **Database A (managed outside Forge, recommended) or B (a process)** as the documented default?
3. **Phase 1 first?** It unblocks native apps' chat and preview access in about 2 days, before any
   service-session code. Recommended.
4. **Can agent sessions call a service?** This design says no (owner only). Allowing a
   `forge_session` credential of the same owner and project on a service route would be a new
   scope (`forge:session:service`) and a separate decision.
