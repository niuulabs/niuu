# Resident ravns

A **resident** is a long-lived, named ravn with a chat room — same format as
a Valkyrie, but conversational. You join and leave its chat; it keeps
working, remembers via Mimir, launches platform work (research campaigns,
spec stacks, planning), and reports back into the room when results land.

## Deploy

Residents are ordinary Forge sessions with `workload_type: "resident"` — a
*flock of one*: a Skuld broker in room mode plus one ravn daemon.

```bash
curl -X POST "$VOLUNDR/api/v1/forge/sessions" \
  -H "Authorization: Bearer $PAT" -H "Content-Type: application/json" \
  -d '{
    "name": "muninn",
    "workload_type": "resident",
    "workload_config": {
      "persona": "product-steward",
      "resident_name": "Muninn",
      "platform": {"enabled": true, "base_url": "http://volundr:8080"},
      "mimir": {"hosted_url": "http://mimir/api/v1"}
    }
  }'
# then start it
curl -X POST "$VOLUNDR/api/v1/forge/sessions/$ID/start" -H "Authorization: Bearer $PAT"
```

Key `workload_config` fields (see `ResidentContributor` for the full set):

| Field | Meaning |
|---|---|
| `persona` (required) | Ravn persona the resident runs (e.g. `product-steward`) |
| `resident_name` | Display identity in the room and fleet UI |
| `platform` | `gateway.platform` overlay — enables the Ting/Forge/tracker tools |
| `mimir` | Same shape flocks accept (hosted url / registry refs / instances) |
| `llm_config`, `daily_budget_usd`, `iteration_budget` | Runtime limits |

For a standalone (off-platform) deployment, use the agent Helm chart with
`charts/agent/values-resident-steward.yaml`.

## Discover and chat

Residents appear in the Ravn web UI fleet — `GET /api/v1/ravn/ravens` now
returns the caller's resident sessions (live from Forge, ownership-scoped;
no seed data). Each record carries `chat_endpoint`; the Ravn detail page
shows a **Chat** tab wired to the same Skuld room chat the Volundr session
view uses. Plain messages route to the resident automatically
(`room.default_target_peer_id`), so any chat client works.

Residents idle by design: the heartbeat liveness reaper exempts
`workload_type: resident` (`session_liveness.exempt_workload_types`);
pod-status reconcile remains the authoritative death check. Nothing
auto-stops a resident — stop it like any session when you're done with it.

## Ownership

Skuld enforces session ownership at WebSocket accept (`ws_auth` in the
broker config): browser and ravn connections must present an identity
matching the session owner (Envoy headers, dev params, or bearer token;
admin roles bypass; unauthenticated loopback stays open for in-pod peers).
A resident authenticates with its platform identity, so it can join
exactly the sessions its owner owns — nothing else.

## The loop back

Two mechanisms resume the chat when launched work lands:

- **Resident relay** (`resident_relay` in the broker config): platform
  events matching the resident's persona `consumes_event_types`
  declaration (research/spec/plan completions by default) become a
  directed turn plus a `room_notification` in the chat history.
- **Session join** (`session_join` tool): the resident joins the room of a
  session it launched (`{"action": "join", "session_id": ..., "chat_endpoint": ...}`),
  appears in that session's participant list, receives messages directed
  at it there (tagged perception, never confused with your chat), can
  `post` answers into that room, and leaves when the work completes.

## Driving pipelines from chat

The `product-steward` persona ships with the full chain:

| Tool | Purpose |
|---|---|
| `ting_workflow` | Launch Research Campaign / Specification Stack (supports `provenance` and `gate_auto_forward_after`; pass `""` to make gates wait for you — encoded as a very long duration so the downstream default never re-enables auto-forward) |
| `ting_spec` | Follow spec campaigns; approve / request changes on PRD/SRD/SDD gates |
| `ting_plan` | Spawn planning from an approved spec, read the draft breakdown, approve gates |
| `ting_saga` | `commit` the approved breakdown into tracker tickets; `dispatch` runs |
| `mimir` tools | Curate `projects/<slug>/` initiative pages between stages |
