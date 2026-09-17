# Ravn turn corpus

Captured with `tests/test_ravn/capture_turn.py` against the **dev** gateway on
`127.0.0.1:7478` (`~/.ravn/config-lexi-dev.yaml`, persona `travis`, Telegram
disabled, workspace pinned to `~/.ravn-dev/workspace`). Never captured against
`:7477` — that is the live Travis wired to a real Telegram thread.

These exist because the OpenClaw adapter's `TurnTranslator` was designed against
frame shapes *read from `ravn.domain.events`*, not observed. This corpus is what
turns that design from inference into fact.

| Fixture | Frames | What it proves |
|---|---|---|
| `ravn_turn_plain.jsonl` | 3 | The delta inversion, on real bytes |
| `ravn_turn_tools.jsonl` | 6 | `tool_start` / `tool_result` payload keys; no call id |
| `ravn_turn_edit.jsonl` | 12 | The `diff` payload; a real `is_error: true` result |
| `ravn_turn_thinking_synthetic.jsonl` | 6 | **SYNTHETIC** — shape only, see below |
| `ravn_turn_sonnet45_anomaly.jsonl` | 2 | A model-path bug found while probing |

## What the corpus established

**The delta inversion is real.** `ravn_turn_plain.jsonl` streams `"p"` then
`"ong"` as separate incremental `thought` frames, then a terminal `response`
carrying the whole `"pong"` again. A client that appends every text frame
renders **"pongpong"**. OpenClaw's `chat` deltas are cumulative snapshots that
`ChatScreenModel` *replaces* with, so the translator must accumulate Ravn's
increments and emit the running total — never pass fragments through.

**Tool frames carry no call id.** Observed keys are exactly
`tool_start {tool_name, input}` and `tool_result {tool_name, result, is_error}`.
`ToolCall.id` exists at the emission site (`agent.py:1352` constructs a
`ToolResult(tool_call_id=tool_call.id, …)` two lines above the `tool_start`
emit) and is simply not passed into the payload. Until that 4-line upstream fix
lands, pairing a result to its start is FIFO-by-name — correct only because the
per-session `asyncio.Lock` serialises turns.

**`diff` appears only on mutating tools.** Absent on `read_file`, `glob_search`
and `bash`; present on `edit_file` as a unified diff:

```
--- scratch.txt
+++ scratch.txt
@@ -1,3 +1,3 @@
 alpha
-beta
+BETA
 gamma
```

It is computed by `tool.diff_preview(...)` *before* execution, so it is emitted
even when the tool then fails — which makes it safe to render optimistically.

**A turn can open with a tool.** `ravn_turn_tools.jsonl` begins with
`tool_start`, not text. A translator that assumes text-then-tools will mis-order
the first block.

**There is no terminator.** After `response` the stream simply ends. No
end-of-turn frame, no `[DONE]`, no `turn_id`. "Closed without a `response`" and
"still streaming" are indistinguishable at the transport layer, which is why the
translator must synthesize the terminal itself.

## What could NOT be reproduced

**Extended thinking never fired.** Ravn requests it correctly —
`_resolve_thinking` returns `{"type": "enabled", "budget_tokens": 8000}` with
`auto_trigger` on and persona `travis` setting `llm.thinking_enabled: true` —
but `AnthropicAdapter._thinking_for_model` translates that to
`{"type": "adaptive"}` for `claude-opus-5`, and no captured turn emitted a
`thinking_delta`. Four attempts across two models and two prompt styles, one of
them a deliberately hard reasoning problem, produced zero frames with
`payload.thinking == true`.

The consequence for the adapter: the thinking branch must exist and be tolerant,
because the shape is certain and a config or model change could switch it on —
but **no test may assert that live Ravn produces thinking frames**, and the
feature must not be advertised to the client as available. The synthetic fixture
is labelled as such in its own `__meta__` line.

## An unrelated bug found while probing

`ravn_turn_sonnet45_anomaly.jsonl` was captured from a throwaway gateway running
`claude-sonnet-4-5` (chosen because it takes the legacy fixed-budget thinking
shape). It still emitted no thinking — but it echoed **the entire persona system
prompt** into both the `thought` and the `response`, and did so as a single
non-incremental frame rather than a token stream.

That is a real defect on that model path: a user asking a trivial question gets
Travis's full system prompt back. It is out of scope for the adapter and is
recorded here so it is not lost.

## Re-capturing

```bash
./.venv/bin/python tests/test_ravn/capture_turn.py \
  --session capture-plain \
  --message "Reply with exactly: pong. Do not use any tools." \
  --out tests/test_ravn/fixtures/ravn_turn_plain.jsonl
```

The tool refuses a `--base-url` containing `:7477`.
