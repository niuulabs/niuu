# Claude tmux prompt and key-control diagnostic

September 16, 2026. **One confirmed key-routing defect fixed in source; no service
rollout. Original `horde-2999` popup not identified or claimed fixed.**

## Confirmed defect and correction

A valid single-key WebSocket message, such as
`{"type":"terminal_key","key":"Enter","pane_id":"%0"}`, reaches the broker.
The broker supplies `keys=[]` when no sequence is present. The tmux transport
previously preferred any list, including that empty default, over `key`. It therefore
silently sent **no key** to tmux. Direct transport-only tests missed this because
they did not add the broker's default field.

The transport now lets only a **nonempty** key sequence override the single key.
Nine real-broker/real-adapter tests, with only tmux execution replaced, check Enter,
Escape, arrow aliases, explicit sequences, sequence precedence and empty inputs.
The three affected cases failed before the change and all nine pass afterward.
No default choice is submitted, no ordinary chat message is manufactured, and a
raw terminal key does not falsely mark a structured permission/question resolved.

Fresh Skuld suite: **2,520 passed, 22 skipped, 67 deselected, one expected failure**,
zero warnings with warnings-as-errors. The unchanged 85% gate passes at **87.75%**
for the complete tmux transport module with branch measurement enabled. This is
not whole-monorepo coverage. An earlier narrower sweep passed 473 tests but missed
the same coverage gate at 82.89%; that attempt remains recorded, not relabeled green.
Lint/format checks cover the changed source and test file.

## Actual Fable 5.1 session, not a synthetic startup claim

At the user's request, exactly one ordinary Forge diagnostic session was launched:

- Host/name: `thor`, `lexi-fable-51-tmux-prompt-diagnostic`.
- Session: `9d4f16bb-6341-534c-bce8-14de28420aee`.
- Parent: dedicated runtime runner `4ab99660-05e8-52c3-90d3-85d7463ce8a2`.
- Requested/observed model: `claude-fable-5-1`, xhigh, Claude Code **2.1.272**.
- Harness: `skuldClaudeInteractive`, actual tmux transport on the retained Thor
  `c6d80980` source. It does **not** contain the new key fix yet.
- Existing owned workspace:
  `/home/thor/repos/worktrees/niuu-forge-runtime-management-20260913/.local/horde-tmux-prompts-20260916/fable-workspace`.

Fresh native auth inspection used the same subscription child environment as
Skuld: logged in through `claude.ai`/Max, with platform API-key variables absent.
No authentication, billing mode or shared configuration was changed. The native
pane confirmed the requested model and completed `DIAGNOSTIC_READY`. **No blocking
startup popup appeared in this folder.** The definition's existing permission mode
was not altered; no new permission bypass or global auto-approval was added.

A harmless, owned AskUserQuestion test then asked for Blue versus Green. The exact
question appeared both in the native terminal menu and the gateway's structured
`/api/control-state`. On that live menu:

1. A single-key `key:"Down"` produced no terminal-key event and left Blue selected.
2. The existing sequence form `keys:["Down"]` produced the exact terminal-key event
   and moved the native selection to Green.
3. After independently confirming the same question and Green selection,
   `keys:["Enter"]` submitted it. Native output returned `DIAGNOSTIC_COLOR=Green`;
   both question and permission collections were empty afterward.

That proves the old runtime defect and an available explicit-control workaround.
It is not a live deployment test of the corrected adapter. Only this owned session
received diagnostic input; no other worker, Thor API or existing gateway was restarted.
The diagnostic session remains available; its completed native turn is not confused
with the Forge metadata's later `active` activity hint.

## Existing control contract and safe defaults

The authenticated session WebSocket already supports terminal keys and text;
`POST /sessions/{id}/messages` is ordinary conversation input, **not** a tmux command
endpoint. For an operator's confirmed target pane, the sequence form works on the
old runtime as well:

```json
{"type":"terminal_key","keys":["Enter"],"pane_id":"%0"}
```

This is a wire-shape example, **not an instruction to press Enter blindly**. Inspect
the exact current prompt and selection first. Raw terminal controls do not have the
ordinary message route's durable request-id/idempotency contract; an uncertain ACK
must not trigger a blind retry.

Hook-backed AskUserQuestion and permission prompts already become structured answer
cards. Prefer their request-bound `ask_user_answer` route for real questions so the
adapter can validate the live menu and native result. A work-choice question has no
universally safe default. Authentication, workspace trust, permission escalation,
spending and destructive-action confirmations must not silently become “yes”.

The automatic-default portion of the request is **not implemented** without knowing
the actual administrative popup. A safe follow-up needs the precise popup/layout,
its allowed non-escalating response and stale-menu/duplicate-delivery tests. An
unknown prompt should be exposed as attention requiring an explicit response, not
silently answered from a generic Enter/Yes rule. This diagnostic's ordinary native
question was already exposed correctly.

## Additional observed defects — still open

- A `discover_slash_commands` control appeared in the diagnostic gateway log before
  the first follow-up. The native CLI then interpreted that ordinary follow-up as
  `/Internal`, an unknown slash command; Forge still showed the message pending.
  The discovery implementation types `/` plus navigation keys and cleans up using
  Escape/Ctrl-U without verifying the input field. This is strong evidence of input
  interference, but the responsible client's intent is not inferred from the text.
  The owned composer was explicitly cleared once, and a **new** diagnostic step
  succeeded. The rejected request was not replayed under a new identity as if its
  delivery had been certain. Preventing destructive/interfering catalog discovery
  is a separate correction, not included in the one-line key fix.
- After completion, a dim native suggested prompt, `Diagnostic step 3`, appeared
  with the cursor still at column 2. It was not treated as user-authored input or
  submitted. Plain-text captures can conflate suggestions with real draft text;
  readiness/composer guards need to preserve that distinction. The observed native
  suggestion behavior is consistent with the [official interactive-mode reference](https://code.claude.com/docs/en/interactive-mode).

`horde-2999` was absent from both archived-inclusive Thor and Spark session lists;
the user confirmed it was elsewhere and requested this local reproduction instead.
No claim is made about that other host's session, prompt or resolution.

Private raw evidence (including exact screens, control-state reads, key events,
request identities and test results) is under `.local/horde-tmux-prompts-20260916/`
in the dedicated runtime checkout. No native remote-control URLs or credentials
are reproduced here. Existing deployment HOLD remains unchanged.
