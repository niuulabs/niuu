# Artifact publications and burst notices

Forge keeps a durable record of every artifact a session publishes, and announces each
burst of publishing once. This is the control plane's Slice 0 (F1, F1n, F1t) plus host
identity (F7). The rules are `docs/control-plane` `model/bursts.md` and platform design
§V1 and §5.2 (lexi-ios `plan/control-plane`).

## What is recorded

Forge reads publications from the **stored** session-log rows, at log ingest, before the
append is acknowledged. A producer retry re-runs it without duplicates.

| Source | Row | Provider | Bursts |
|---|---|---|---|
| `artifact_tool` | `claude_hook` PostToolUse of the claude.ai `Artifact` tool that publishes a page (no asset upload, failure or refused publish) | `claude_ai` | yes |
| `report_artifact` | the `report_artifact` MCP tool's turn | `claude_ai` | yes |
| `present_file` | a `present_file` part (every engine, Codex included) | `forge_file` | no |
| `notify_link` | a notification turn's link of kind `artifact` | `claude_ai` | no (an announcement) |

A row is `live` when ingest sees it within `artifacts.live_lag_seconds` (600) of its
timestamp, otherwise `backfill`. The internal backfill always writes `backfill`.

Each publication carries `type` and `marker` (F1t): `deck`, `design`, `doc`,
`design_system`, `page`, `file` or `unknown`, from the publish's Artifact type
(`artifacts.type_urls`, then the type's front-matter name in `artifacts.type_names`).
`marker` is set only for deck, design, doc and file.

## Bursts and the notice

- A burst groups publications by `(host, provider_key, origin)` with a sliding window of
  `artifacts.burst_window_seconds` (1800) on each side. Its `burst_id` is minted once from
  the seed publication and never recomputed. A late row joins without renaming; a row
  inside two windows joins the most recent and records the other as bridged; bursts never
  merge.
- A **live** burst is due `artifacts.hold_seconds` (120) after it opened. The hold loop
  (every `tick_interval_seconds`) emits it in one transaction: lock the burst, recheck it
  is open, recheck suppression, then insert the notice and its deliveries.
- **Suppression:** an `agent` notice from any session whose link names the artifact,
  created from the window's start up to emission, suppresses the notice
  (`suppressed_by`). Slice 0's own notices never count.
- **The notice:** `milestone`, `info`, `system`; title `<title> published`; body the
  session's name; one `artifact` link; `correlation_id = art-burst:<burst_id>`;
  `dedupe_key = artifact-burst:<burst_id>`. A restart during the hold emits once.
- **Backfill bursts are silent.** `artifacts.notices_enabled: false` records bursts without
  ever notifying.

## Reads

All under `/api/v1/forge`, owner-scoped like the notification feed (admins see their
tenant). On the gateway, add `scope=local` to pin the local node; responses carry
`instance_id`.

- `GET /artifact-publications?after=<seq>&limit=` — publications in commit order; each
  carries its `burst_id` and `occurrence` `[host, "log", session_id, seq]`.
- `GET /artifact-bursts?after=<rev>&limit=` — bursts in change order. `rev` advances on
  every change (a late join, the notice, a suppression), so a follower always holds each
  burst's current window and outcome.
- `GET /artifact-bursts/{burst_id}` — one burst with its publications.
- `POST /artifact-publications/backfill` (admin) — `{since?, until?, session_id?}`; a SQL
  replay of `session_event_log`, never the public `/log` route. Idempotent.
- `GET /instance` (F7) — `host`, `tailnet_name` and `instance_id`; the gateway adds the
  node's self `instance_id` and `slug`. The control plane's host map stays the authority.

## Configuration

```yaml
host_identity:
  host: thor-exp                       # the control plane's host key; empty: projects.instance_id
  tailnet_name: thor.tail737f2a.ts.net # empty: server_public_host
artifacts:
  enabled: true
  notices_enabled: true
  hold_seconds: 120
  burst_window_seconds: 1800
  live_lag_seconds: 600
  backfill_on_start: false             # replay the log at start (silent)
  backfill_since_days: null
```

## Presented files (F2)

`present-file` stages a copy next to the session's broker, which serves it only while
the session runs. With `presented_files.enabled`, the broker also uploads the bytes to
Forge (`PUT /api/v1/forge/sessions/{id}/files/presented/{file_id}`), and Forge keeps
its own copy. `GET` on the same route serves Forge's copy first and the live broker's
second, so the file still opens after the session stops, after the broker restarts,
and after Forge restarts. Every route that serves one, the Guild facade included,
sends `x-content-type-options: nosniff` and `content-security-policy: sandbox` (PDF is
exempt from the sandbox only).

### Links

A `present_file` publication links the relative route unless
`presented_files.public_base_url` is set. The URL in the session's transcript is
written by the session, so Forge never trusts it as a link: a publication keeps it
only when it is exactly this Forge's own URL for that very file
(`public_base_url` + the route). Anything else (another host, session or file, or an
origin this Forge is no longer configured with) becomes the relative route.

`public_access` says what controls access to `public_base_url`:

| `public_access` | `public_base_url` | Who can open it |
|---|---|---|
| `tailnet` (default) | `http(s)://` a `*.ts.net` name or a Tailscale address | Devices on the tailnet (a mini Forge such as Thor, with the allow-all identity) |
| `authenticated` | `https://` the Forge's own origin | Callers with a valid bearer at Envoy (the platform's OIDC). Forge refuses to start with this and the allow-all identity adapter. |

On a cluster the absolute URL needs the caller's bearer (`Authorization` or
`?token=`), like any Forge route; leaving `public_base_url` empty is equally valid,
since clients open the relative route on the origin they already talk to.

### The store and the quota

`max_total_bytes` is required when enabled. Forge refuses to start when the store's
free space plus what copies already use cannot hold it, or when the store root is not
writable. A copy that would pass it is refused with HTTP 507, `present-file` fails
with the remedy, and the refusal is counted in `GET /api/v1/forge/health`
(`presented_files`). Nothing is evicted; copies go with their session's delete.

| Store | Use | Exclusive section (quota admission and write) |
|---|---|---|
| `LocalPresentedFileStore` | One Forge process: a mini Forge | An in-process lock |
| `PostgresPresentedFileStore` | Any number of Forge processes: a cluster | A Postgres advisory lock. Bytes live under `root`, on a volume every replica mounts; the record of each copy is a `forge_presented_files` row (migration `000100`), and the quota is the sum of the rows |

The local store is wrong for a cluster even with `replicaCount: 1`: the chart's
rolling update starts the new pod before the old one stops, and two processes with
in-process locks can overrun the quota.

### Uploads authenticate with the session credential

The broker uploads with its session-bound credential (`forge_session`, scope
`forge:notify`, its own session only). A browser user's login expires and a headless
session has none, so behind an authenticating identity this credential is the only
bearer that always reaches Forge. Forge refuses to start with presented files on, an
authenticating identity, and no way to hand brokers their credential. The local
process pod manager delivers it in the broker's environment. Flux and Kubernetes
session pods get it through `forge_mcp.session_tokens.projection_adapter`: one Secret
per session (`forge-session-<session hex>`), mounted read-only into the session pod,
read once at broker start (`forge_mcp.token_file`; a missing or empty file is fatal),
re-written on every start (a restart revokes the previous launch's token) and deleted
when the session stops. The HelmRelease values carry only the file path. The OpenShell
pod manager writes it into the sandbox itself (`/run/secrets/niuu-forge-session/token`,
0600) before the broker starts, so an OpenShell Forge needs no projection.

The broker's `volundr_api_url` (the Skuld chart's `volundr.apiUrl`) must reach the
Forge that owns the session through Envoy. Without it the broker skips the upload, and
`present-file` answers `durable: false`.

### Cluster setup (Volundr chart)

```yaml
presentedFiles:
  enabled: true
  maxTotalBytes: 5368709120          # must fit persistence.size
  publicBaseUrl: ""                  # or https://forge.example.com (publicAccess: authenticated)
  publicAccess: authenticated
  persistence:
    storageClass: ""                 # a class that serves accessMode
    accessMode: ReadWriteMany        # ReadWriteOnce only with rollout.colocate: true
    size: 10Gi
sessionCredentialProjection:
  enabled: true
  namespace: ""                      # the session pods' namespace; empty: podManager.kwargs.namespace
workloadIdentity:
  enabled: true
  signingKey:
    existingSecret: volundr-workload-identity   # turns forgeMcp session_tokens on
```

The chart renders the Postgres store with `root` at `presentedFiles.mountPath`
(`/volundr/presented-files`), a PVC kept on uninstall, a Role in the session namespace
for Secret get/create/patch/delete, and the projection kwargs. It refuses to render
without `maxTotalBytes`, without session credentials, without the projection (unless
the pod manager delivers the credential itself: OpenShell, local process), or with
`ReadWriteOnce` and `rollout.colocate: false`.
