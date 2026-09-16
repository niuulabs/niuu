# Native question acceptance through Forge and iOS

This extends the [live agentic plan](forge-live-agentic-acceptance.md) after the
September 8 simulator campaign reproduced a false Claude custom-answer receipt.
Run it whenever the tmux question bridge, native CLI, answer protocol or iOS
question card changes. Claude tmux and Codex are the required active providers;
legacy Claude SDK/Scody remains outside new live investigation.

The owner accepted simulator-only iOS validation for this campaign. A simulator
warning is useful draft/cache evidence; it does not establish physical jetsam,
thermal behavior or survival of an app force-quit.

## Required evidence chain

A socket write, `accepted: true`, a replay match and an assistant's claim are
four different observations. None alone proves that the intended native answer
was consumed. Require the complete chain for a successful question:

1. A newly created, owned Forge session reports the intended release revision,
   source hash and native session ID. Record both platform and broker health.
2. The real native tool emits the requested question shape. Save its tool-use ID,
   exact question/options and Forge control ID before answering.
3. Save the actual client answer frame, including its control ID and structured
   `option_indexes` or `free_text`. Use the real iOS app for UI acceptance.
4. Find the matching native tool result. It must be successful and contain every
   intended answer, with exact custom text. Require the same native session and
   tool-use identity; an unrelated later result cannot confirm this action.
5. Confirm that the bridge's positive receipt follows native acceptance. A native
   rejection must never become `accepted: true`; missing proof stays unconfirmed
   or requires recovery. A retry must not enter text in an unrelated composer.
6. Independently read the fixture file written after the tool result. Compare its
   values with the submitted answers and the native result. Require the final
   assistant marker in the same parent turn.
7. Freeze the durable head, read all session-scoped PostgreSQL rows in a read-only
   transaction, and compare their public projection against log and replay in
   exact order and multiplicity. Inspect tool pairing and the stopped/full
   conversation as well as mechanical frame equality.

Retain failures unchanged. The September 8 Forge.2 custom-answer failure had
perfect public/replay parity for its captured frames but an incorrect positive
receipt and no expected output file. That is a failed semantic case.

## Fresh live cases

Use uniquely named disposable workspaces and record the returned session IDs
immediately. Prompt one case at a time; never queue another behind an unresolved
question. Use Fable 5.1/xhigh and Codex Astra when available, and record the actual
native model and effort rather than inferring execution from the requested name.

| Case | Prompt contract | Answer | Independent result |
|---|---|---|---|
| Claude non-default option | One single-select question, Blue then Amber | `option_indexes: [1]` | Native answer Amber and `accent.json` with the exact value |
| Claude Other | One single-select question with two normal choices | Custom `Copper café 東京 <unique-marker>` through `free_text` | Exact Unicode value in native answers and `label.json` |
| Claude multiple questions | One AskUserQuestion call with Tone and Texture | Amber on Tone; custom text on Texture | Both native answers and both corresponding JSON keys; no first-answer-only success |
| Claude multiple selections | One question with Blue, Amber and Violet; `multiSelect: true` | Blue and Violet | Exactly those two choices, no Amber; compare native selection order separately |
| Claude checkbox plus Other | One multi-select question with Auth and Search | `answer: ["Auth", "custom text"]`, `option_indexes: [0]`, exact `free_text` | Both selected values consumed; option indexes cover only the declared choice |
| Native cancellation | One fresh question, then a guarded native cancellation | Send a stale answer for the rejected native question | Correlated negative result, one captured native error, no positive receipt or stray keys; completed native turn does not keep spinning |
| Codex custom answer | Native request-user-input tool requesting a trace label | Unique Unicode custom text | Same native thread, exact tool response and `trace-label.txt` |
| Restored iOS Other draft | Open a fresh custom editor, type text, leave and reopen same question | Submit the restored text | Editor is expanded with exact draft; native result/file contains it |
| Memory-warning draft | Type an unsent composer draft with an attachment, then an Other draft | Inject a simulator warning and navigate | Exact draft and attachment identity retained; no claim about force-quit persistence |
| Recovered question | Crash only the verified owned broker while question is pending | Attempt stale answer through harness; inspect app | Same native identity after resume, recovered non-answerable card, draft retained, stale ID rejected without keys |

For a multiple-question prompt, specify a single AskUserQuestion invocation with
exactly two questions, each with a distinct question string and header. Ask the
model to write the answers to JSON only after the native tool resolves. A model
that asks separate ordinary chat questions did not exercise this case.

For checkbox selection, native result ordering can follow selection order. The
observed test selected Blue, then Violet, deselected Blue and reselected it; the
native value was `Violet, Blue`. Record that ordering. Do not treat arbitrary
string reordering as proof that only the intended options were consumed.

## Native UI transitions observed on Claude Code 2.1.263

These observations guide regression fixtures; they are not timeless keyboard
assumptions. Inspect the current menu before each mutation and wait for the
expected next state within a bounded deadline.

The bridge's native-result wait defaults to five seconds and its transcript
proof read is bounded to 1 MiB. Exhausting a proof budget does not establish
rejection or acceptance. Preserve the control as requiring recovery. Cancellation
completion uses the matching native result's descendant interruption and
`turn_duration` records; an empty composer alone does not prove that a model is idle.

| Native state | Observed transition |
|---|---|
| Single-select normal option | Its digit selects immediately; with multiple questions it advances to the next page |
| Other row | Its digit opens the inline editor; the `ctrl+g to edit in Vim` hint appears |
| Single-select Other editor | Literal bracketed paste displays the text; one Enter submits that editor |
| Multiple-question last answer | Advances to Review your answers; the numbered Submit answers action is separate |
| Checkbox question | Digits toggle choices without advancing or moving the focused row |
| Checkbox row focused | Enter toggles that row; it is not a submit action |
| Checkbox Other editor | Navigate focus to Other, then paste; typing automatically checks the custom row. Do not press Enter there, because it deselects that row |
| Checkbox completion | Navigate to the unnumbered highlighted Submit row, Enter to reach review, then choose Submit answers |

The former sequence digit → Enter → paste → Enter could cancel Other by
submitting an empty editor before pasting. Fixed sleeps or an extra Enter on
timeout are not valid repairs. If the menu changes unexpectedly, preserve the
draft/evidence and surface recovery rather than trying the same keys elsewhere.

## Deterministic regressions and live boundaries

Keep sanitized fixtures for the actual menu transitions in
`tests/support/forge/screens`; omit private paths, remote-control URLs and prior
conversation. The question bridge tests should cover successful native proof,
native rejection, absent or mismatched proof, delayed/duplicate result delivery,
double answers, transcript identity changes, malformed/partial JSONL, and all
supported structured answer shapes. Ordinary tests must not invoke paid CLIs.

Run the comprehensive Forge gate with its unchanged coverage threshold after the
focused tests pass:

```bash
uv run python scripts/verify_forge.py unit --coverage \
  --artifacts .forge-results/native-question-release
```

Run native acceptance separately through an authenticated deployment and owned
canaries. The generic live catalog autoanswers only declared question options;
it must not silently broaden that policy to permission cards or arbitrary
custom answers. Rich answer cases above require an explicitly scoped controller
or the actual app. Label direct tmux navigation probes as native UX evidence,
not a pass for the Forge adapter or iOS submission path.

After each case, harvest evidence before stopping or deleting the owned session.
Before a fault, validate session ID, name, workspace, native ID, broker PID and
process start identity. Verify owned broker/native/tmux cleanup afterward.
Never use retained user sessions for prompts, answers or destructive faults.

## Review and corpus promotion

An agent review should compare the intended task, actual native actions, durable
capture and visible UI state, then classify each assertion as passed, failed or
not exercised. Review against a frozen capture hash. Promote only sanitized,
attributed traces through `scripts.forge_corpus`; preserve the original failed
trace and its semantic findings beside the corrected run.

For iOS, keep screenshots before/after draft restoration and acceptance, actual
outbound/inbound control frames, native result and file proof. For replay, retain
full and cursor comparisons and the canonical conversation. A fixture rendering
correctly is useful regression evidence but cannot replace real native control
acceptance. Record simulator/device, app build, server identity and evidence
manifest hash so the same campaign can be repeated after a future CLI update.
