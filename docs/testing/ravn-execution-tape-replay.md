# Ravn execution-tape replay corpus

The execution-tape corpus is a credential-free, deterministic harness check.
It is deliberately a test adapter: it never invokes a provider, starts a CLI,
or executes a Ravn tool. `ScriptedModel` consumes recorded model turns exactly
once and `ScriptedTransport` records dispatch identifiers only. This checks
harness-visible recovery semantics without replaying external writes.

## Fixture schema

Fixtures live at `tests/test_ravn/fixtures/execution_tapes/v1/`. Every JSON
file has `schema_version: 1`, a unique `case`, a declared capability list,
ordered `events`, and `expected` canonical invariants. Version the directory
when a non-backward-compatible schema change is needed; do not silently change
what version 1 means.

The supported event kinds are:

- `model` — scripted provider response, optional tool-call IDs, and token usage
  shape (`input_tokens` and `output_tokens`, not model prose);
- `dispatch` / `result` — a capability-gated tool call and its paired ledger
  result;
- `approval_pending`, `crash` (`before_dispatch` or `after_dispatch`), and
  `resume` — durable resume boundaries;
- `unknown_side_effect` and `provider_session_lost` — recovery classifications;
- `compaction` — protected and removed message IDs, which must be disjoint;
- `runtime_switch`, `oversized_tool_result`, and `final`.

Expected values may assert only canonical fields: event order, dispatch order,
result pairing, resume points, outcome category, usage shape, context safety,
capability violations, and unknown-side-effect classification. Response text,
tool arguments, results, credentials, provider IDs, and timestamps are never
asserted or captured.

## Capture and review workflow

1. Derive a candidate from a local, synthetic harness trace. Do not capture a
   live credential, prompt, tool payload/result, provider session ID, URL, or
   timestamp.
2. Replace provider output with structural events and stable opaque IDs.
   Preserve only ordering, capability names, outcome category, token-field
   presence, and context boundary facts.
3. Add the candidate under the current schema version, then run:

   ```bash
   uv run --extra dev pytest -q \
     tests/test_ravn/test_execution_tape_replay.py \
     tests/test_ravn/test_execution_tape_harness_paths.py
   ```

4. Review the JSON diff as code: every dispatch needs a preceding model call,
   every result needs a preceding dispatch, protected context may not be
   removed, and a crash needs a resume. Confirm the fixture is secret-free.
5. Update expected canonical facts only when the intended harness contract
   changed. Add a focused regression assertion first; never bless provider
   prose or an external side effect.

`test_execution_tape_harness_paths.py` also runs the compatible
`multiple_tool_calls` tape through the real `RavnAgent` native harness and the
real `CliTransportAgent` CLI-backed harness. Its scripted LLM and
`CLITransport` emit only tape events, so this remains credential-free and
cannot execute an external write. It compares tool order, tool/result pairing,
outcome category, and usage shape; a mismatch reports the precise canonical
field and both values. The focused mutations in that test exercise the actual
native fold, CLI result ledger, native permission check, and native compaction
methods rather than the test-only replay runner.

The test-only differential runner still calls `structural_diff` over its
provider-free replay result. An empty diff proves its canonical parity; a
non-empty mapping identifies the changed invariant without exposing model
output.

The golden corpus includes plain conversation, multiple tools, tool failure,
pending approval, crashes on both sides of dispatch, unknown side effects,
provider-session loss, compaction, runtime switch, and oversized tool results.
The regression tests deliberately break fold, ledger, capability, and
compaction behavior to prove the invariants are live.
