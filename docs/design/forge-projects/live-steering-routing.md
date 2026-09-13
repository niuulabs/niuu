# Live steering is not implicit cancellation

## Reproduced source defect

The generic broker recomputed busy/idle delivery state correctly but then used `send_control("redirect")` for **every** steer-capable busy transport. Codex advertises `steering_mode="live"`, yet its redirect path deliberately calls `turn/interrupt` and composes a replacement prompt. Ordinary native composer messages use REST content/request_id → broker user input, so this routing converted normal steering into cancellation.

The correction respects the declared transport capability, not an engine/model name:
- Busy `live`: `steer`, appending to the current turn.
- `native`: retain the transport's native input/redirect route, even idle.
- Busy `interrupt_resume`: retain its declared interruption semantics. This patch does not falsely promise live steering for every engine.
- Idle/non-steering: ordinary `send_message`.
- Explicit Stop: unchanged interrupt control.

## Exact identity and uncertainty

Codex's `turn/steer` response carries the accepted `turnId`, and does not emit another `turn/started`. The adapter validates that response against the targeted turn before emitting the existing correlated input-active event using the original message/request IDs. It does not enqueue that correlation into a future turn/start FIFO. “Active” means input was appended to the active turn, not that the model completed an objective.

If the turn finished before the adapter call, the input has not crossed the provider boundary; use the ordinary start path with the same IDs. If the steer RPC acknowledgement is missing, malformed, or targets a different turn, do not fabricate acceptance or restart. Existing durable claims leave ambiguous delivery pending and prevent duplicate dispatch on the same-ID retry/restart.

The existing explicit redirect operation is still distinct and retains replacement semantics; ordinary live input no longer selects it. No custom coordinator workflow, provider call or runtime change is involved.

## Evidence limits

Hermetic tests exercise the real broker plus Codex adapter, repeated updates, existing turn/prompt preservation, explicit Stop, idle-boundary routing, exact durable user history/reducer replay, duplicate delivery and recreated-broker no-resend. Tests mock the native RPC boundary. They prove the requested wire methods and persistence behavior, **not** continued execution of a real Codex-owned tool or deployed conversation behavior. A provider-backed held-tool test requires separate authorization; do not substitute a static UI screenshot or a detached task for that proof.

[Official app-server contract](https://learn.chatgpt.com/docs/app-server) was read September13,2026. Local `codex app-server generate-json-schema` independently produced `v2/TurnSteerParams.json`/`TurnSteerResponse.json` with required expectedTurnId/turnId; schema generation creates no agent session and calls no provider.

Tests: `tests/test_skuld/test_live_steering_routing.py`, existing broker/review/delivery/transport suites. This is isolated source work, not a live host upgrade.
