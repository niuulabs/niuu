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
