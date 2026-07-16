# A2A workflow tasks

Ting exposes its workflows over the [A2A protocol](https://a2a-protocol.org)
(v1.0) so external agents — Valkyries, OpenClaw/Hermes controllers, or any
A2A client — can discover, launch, follow, and collect results from platform
workflows without knowing Niuu-specific APIs.

## Discovery: the agent card

`GET /.well-known/agent-card.json` serves a standard Agent Card at the origin
root. The card is rendered **per request** from the workflow store: every
system-scope workflow appears as a skill (skill id = workflow UUID) the moment
it exists — no restart or registration. The card carries an `ETag` derived
from its content and a configurable `Cache-Control: max-age` (`a2a.card_max_age_seconds`),
so clients revalidate cheaply with `If-None-Match`.

Workflow `description` and `tags` are projected verbatim onto the card —
they are the semantic interface an LLM consumer reads to pick a skill.
Treat them as agent-facing UI when authoring workflows.

## The task endpoint

`POST /api/v1/ting/a2a` is a JSON-RPC 2.0 endpoint implementing `SendMessage`,
`GetTask`, and `CancelTask`. Requests must carry the `A2A-Version: 1.0` header
and a platform bearer token (PAT or workload-identity exchange). Streaming and
push notifications are not supported — poll `GetTask`.

There is no A2A-specific state: the **task id is the workflow campaign slug**,
and task views are synthesized from the campaign record, which the campaign
projector keeps in sync with live session state.

### Launch

`SendMessage` with a new message (no `taskId`):

- text part → the workflow prompt
- `metadata.workflowId` (required) → the workflow UUID from the card
- `metadata.repo` / `branch` / `model` / `sessionName` / `connectionId` (optional)

Scoped `valkyrie_build` tokens must carry `ting:workflow:launch`; human tokens
are unaffected. Returns a Task in `SUBMITTED`.

### Follow

`GetTask {"id": <taskId>}` maps campaign state onto A2A states:

| Campaign | A2A task state |
|---|---|
| pending | `SUBMITTED` |
| running | `WORKING` |
| blocked (gate pending) | `INPUT_REQUIRED` |
| completed | `COMPLETED` |
| failed | `FAILED` |
| canceled via A2A | `CANCELED` |

### Gate replies

When a task is `INPUT_REQUIRED`, send a message **with the `taskId` set**:

- `metadata.gateDecision`: `"approve"` or `"request_changes"` (required)
- text part → reviewer comment (required for `request_changes`)
- `metadata.gateId` / `metadata.nodeId` (optional) to disambiguate when more
  than one gate is pending

The reply resolves the workflow gate and the task returns to `WORKING`.

### Artifacts

On `COMPLETED`, `GetTask` populates `task.artifacts[]` from the campaign's
Mimir pages under `research/campaigns/<slug>/`. Text at or under
`a2a.inline_artifact_max_chars` is inlined; larger content becomes a `url`
part pointing at the authenticated campaign-artifact route. Non-markdown
canonical artifacts (default `learned_tool.json`) are probed via
`a2a.extra_artifact_files`.

### Cancel

`CancelTask {"id": <taskId>}` stops the underlying session and reports the
task `CANCELED`. Terminal tasks are not cancelable.

## Configuration

The `a2a` block in ting settings (rendered by `charts/ting`):

| Key | Default | Purpose |
|---|---|---|
| `agent_name` | `Niuu Workflows` | Card agent name |
| `agent_description` | built-in | Card description (includes the reply convention) |
| `card_max_age_seconds` | `60` | Card `Cache-Control` max-age |
| `public_base_url` | request base URL | Origin used in card/artifact URLs |
| `inline_artifact_max_chars` | `65536` | Inline-vs-url artifact threshold |
| `extra_artifact_files` | `["learned_tool.json"]` | Non-.md artifacts probed per campaign |

## Minimal client walkthrough (curl)

```bash
BASE=https://niuu.example
AUTH="Authorization: Bearer $TOKEN"
V="A2A-Version: 1.0"

# 1. Discover
curl -s $BASE/.well-known/agent-card.json | jq '.skills[] | {id, name, tags}'

# 2. Launch
curl -s -X POST $BASE/api/v1/ting/a2a -H "$AUTH" -H "$V" -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 1, "method": "SendMessage",
  "params": {"message": {
    "messageId": "m1", "role": "ROLE_USER",
    "parts": [{"text": "Build a tool that lists stale feature flags"}],
    "metadata": {"workflowId": "<skill id from the card>"}
  }}}' | jq '.result.task.id'

# 3. Poll
curl -s -X POST $BASE/api/v1/ting/a2a -H "$AUTH" -H "$V" -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 2, "method": "GetTask",
  "params": {"id": "<taskId>"}}' | jq '.result.status.state, .result.artifacts'
```
