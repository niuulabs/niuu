# Claude terminal questions — future requirement

Status: deferred, requested September 19, 2026. The current change automatically
answers only the configured workspace's startup trust question and waits for the
chat prompt. This document does not introduce another API or automatic answers.

## Problem

Claude Code can wait inside a terminal choice menu without emitting the
`AskUserQuestion` or `PermissionRequest` hooks that Skuld already handles. Remote
clients then cannot tell that Claude needs an answer or choose an option through
the conversation's normal question workflow.

## Requested behavior

- Monitor the active Claude pane and distinguish chat readiness, active work,
  a recognized question awaiting an answer, and an unrecognized blocking screen.
- Surface recognized terminal-only questions through the existing question flow
  in web, iOS, and macOS, with the question text, available options, selection
  mode, and selected option. Deduplicate a menu that also has a structured hook.
- Let users answer through the existing question API where its contract fits.
  Identify any necessary contract extension before adding a parallel endpoint.
- Bind every request and answer to its session, native process, pane, and observed
  question/options. Revalidate the current menu before sending keys; reject stale
  answers when the menu changes, disappears, or belongs to a replacement process.
- Serialize answer keys with chat and other terminal input. Verify the observed
  selection before Enter and wait for the native terminal to acknowledge it.
  Repeated captures, client retries, or an uncertain key-send result must not
  duplicate confirmation. Keep pending questions visible across reconnects and
  resolve them when answered locally in the terminal.
- Expose unsupported screens as requiring terminal attention. Arbitrary choice,
  login, and permission prompts require the user's answer; workspace trust is
  the specifically authorized automatic response.

## Acceptance coverage for that future work

Use real tmux fixtures for numbered and unnumbered choices, arrow navigation,
multi-select, cancellation, delayed rendering, changed option order, stale replies,
concurrent chat, native-process replacement, and reconnect/replay. Verify that
hook-backed and terminal-only representations produce one question and one native
answer, and that no chat text is pasted into a blocking menu. Confirm the same
question/answer workflow from the web and Apple clients before claiming parity.

Current startup behavior and its tests are documented in the
[Claude interactive test plan](../testing/skuld-claude-interactive.md#workspace-trust-at-startup).
