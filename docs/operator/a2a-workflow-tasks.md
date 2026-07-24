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

The public card lists **system-scope workflows only** — it is served
unauthenticated, so it advertises the platform catalog, not private
libraries. User-scope workflows are still launchable (SendMessage checks
per-principal visibility); to *discover* them, authenticated callers use
the `GetExtendedAgentCard` JSON-RPC method on the task endpoint, which
returns the same card plus the caller's own workflows as skills.

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
canonical artifact envelopes (default `learned_tool.json`) are probed via
`a2a.extra_artifact_files`.

#### Code outputs

For code workflows the durable artifact is the **git branch the session
pushes**, not a Mimir page. When a launch carries `repo`/`branch` metadata,
both are echoed on the task's metadata so consumers know where the code
landed (`task.metadata.repo` / `task.metadata.branch`); `sessionId` is
always present for follow-up via the Forge session APIs (files, diff,
transcript). Surfacing curated session deliverables (present-file cards)
as first-class A2A artifacts needs a presented-files list API first —
tracked as follow-up work, not silently absent.

### Cancel

`CancelTask {"id": <taskId>}` stops the underlying session and reports the
task `CANCELED`. Terminal tasks are not cancelable.

## Bubbling gates to humans

The A2A layer does not swallow gates — the gate lives in the workflow
session exactly as before, so every existing surface still fires: the
campaign projector emits `workflow.campaign.updated` when the run blocks,
the review inbox and operator console show the pending gate, and a human
can approve there. `INPUT_REQUIRED` on the task is an **additional**
resolution channel, not a replacement.

Consumer contract by agent type:

- **Autonomous Valkyries** may reply with `gateDecision` themselves only
  within their realm's autonomy grant; otherwise they route the decision
  through ODIN court as usual. The ravn `A2AToolBuildBackend` implements
  this: on `INPUT_REQUIRED` it reads the pending gate context that `GetTask`
  attaches (`metadata.pendingGates`: gateId, label, condition, instructions),
  has the resident's own LLM decide approve/request_changes against the
  commissioned build request, sends the reply message, and records the full
  question/answer exchange in the build evidence. Rounds are bounded
  (`max_gate_rounds`, default 3) and a gate with no configured reviewer fails
  the build loudly — never a silent auto-approve.
- **Interactive agents** (resident ravns with a human in the chat,
  OpenClaw/Hermes controllers) must surface `INPUT_REQUIRED` to their
  human — show the gate, collect the verdict, then send the reply message.
  An agent must never auto-approve a gate on behalf of a human it is
  fronting.

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
