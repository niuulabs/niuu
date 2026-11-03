# Control-plane goldens (vendored, read-only)

Copied verbatim from lexi-ios `plan/control-plane` @ `52569206`,
`docs/control-plane/platform/api/`:

- `derived-state.json` — the 20 derived-state goldens (G10, design §V9.2)
- `derived_state_reference.py` — `derived_state.py`, the reference algorithm

Forge's F12d work facts (`niuu.domain.work_facts`, the `session_event_log` ingest SQL)
must give the reference's `last_work_at`/`tool_open` for every golden, and the
fields-only path over Forge's served fields must give each golden's expected state.
Do not edit these files here; update them from the source when the contract moves.

`model/` (from `docs/control-plane/model/acceptance/` @ `c32c83e0`):

- `reference.py` — cp-model's executable reference (stdlib only)
- `asks.json` — the model's ask cases (A1–A27), including R7-2's producer rules

Every ask case's notices are sent through Forge's real notification path (draft → turn
→ projection → read wire) before the reference applies them, so the cases pass only if
Forge carries `question` and `about_question` intact (F11b).
