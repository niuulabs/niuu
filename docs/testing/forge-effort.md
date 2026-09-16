# Forge effort controls and Spark PI/Qwen

Verified against the installed runtimes on 2026-09-09. Effort names belong to the
harness and model together; they are not interchangeable token budgets.

| Harness / model | Offered levels | Preferred launch level |
| --- | --- | --- |
| Claude tmux / Fable 5.1, Opus 5 | low, medium, high, xhigh, max | xhigh |
| Codex / Astra, Sol | low, medium, high, xhigh, max, ultra | xhigh |
| Grok / 4.6 | low, medium, high, xhigh | xhigh |
| Grok / 4.5 | low, medium, high | high |
| Muse / Spark 1.3, 1.2 | none, minimal, low, medium, high, xhigh, ultra | xhigh |
| PI / Astra | minimal, low, medium, high, xhigh, max | xhigh |
| PI / Sol | off, minimal, low, medium, high, xhigh, max | xhigh |
| PI / Spark Qwen3.8 Flash Next | off, high (Thinking On) | high |

The PI runtime maps `minimal` to the OpenAI provider's `low` for Astra/Sol.
PI does not offer Codex's `ultra` orchestration tier. The Qwen vLLM server exposes
a thinking toggle, not graded reasoning effort; its PI model map disables the
other tiers rather than letting PI silently clamp a request.

## Launch and control contract

The host model catalog publishes `effort_levels`, `default_effort`, and
`effort_note`. Explicitly empty levels mean no advertised control. Host-specific
`additional_models` extend Bifrost's shared defaults without copying/fixing the
entire cloud catalog in an operator configuration.

Lexi sends the selected native value as `workload_config.reasoningEffort`.
PromptContributor carries it into `session.reasoningEffort`; local and Kubernetes
launchers export `SKULD__SESSION__REASONING_EFFORT`, while the existing resident and
OpenShell specs already consume that session value. A new broker passes it into
the configured adapter. Legacy callers omitting the field retain their previous
runtime defaults; the new app explicitly requests the user's preferred level.

`/effort` is always present in Forge command discovery. `/effort <level>` changes
the level; `extra`, `extra-high`, and `extra_high` mean `xhigh`. Unsupported levels
are rejected with available choices. The app sends `get_effort` / `set_effort`
WebSocket controls and waits for a correlated `effort_status` response. It never
sends these commands as model prompts, including when connected to an older
broker. The HTTP message and dedicated slash-command paths also intercept the
exact `/effort` token and use the same native control and delivery receipts.

Confirmed changes are recorded in Forge's event stream and an atomically written,
session-keyed effort file next to conversation history. A restarted broker uses
that value for the same model, ahead of the original launch value. Failed changes
do not overwrite the saved setting. A model change invalidates that saved override.

- Codex: the next `turn/start` carries the exact selected effort. The previous
  reduction of `xhigh`/`max` to `high` is removed.
- PI: `set_thinking_level`, followed by `get_state` verification; levels are read
  from the current model's native `thinkingLevelMap`.
- Grok: discover `thought_level` in ACP `configOptions`; set its actual option ID
  through `session/set_config_option`, then verify `currentValue`.
- Muse: subsequent fresh/steered submissions carry `reasoningEffort`. Its embedded
  MSP schema includes `none` and excludes `max`; invalid startup values now fail.
- Claude tmux: launch uses `--effort`; an idle change sends native `/effort <level>`
  and waits for the CLI acknowledgement. During a running Claude turn, Forge asks
  the user to change effort once that turn finishes. It does not interrupt work.
  Claude's own command also updates its CLI default, as documented by Claude Code.

Existing broker processes survive a Forge host rollout. They need a session
restart to load new controls; the app reports an unconfirmed control instead of
letting an old broker treat it as an instruction to the model.

## Spark local configuration

Spark already serves `qwen3.8-flash-next` at `http://127.0.0.1:8888/v1`, using
vLLM's Qwen reasoning/tool parsers. PI's `spark` provider uses that existing
endpoint, `openai-completions`, and `thinkingFormat: qwen-chat-template` with
`supportsReasoningEffort: false`. The actual switch is
`chat_template_kwargs.enable_thinking`. The existing local server is keyless;
PI's required nonempty OpenAI-client API-key field is only a no-auth sentinel,
not a credential or an authentication claim. No cloud credential is sent there.

Only Spark's operator config adds `spark/qwen3.8-flash-next` to the Forge catalog.
Thor's PI configuration has no local Qwen provider. The iOS offline model list
also omits Qwen; its availability comes from the selected host.

## Verification and repeatable acceptance

1. Run `test_effort.py`, all five transport suites, prompt/local-process launch
   tests, Bifrost catalog tests, and broker delivery/recovery regressions.
2. Probe native Codex `model/list`, PI `get_state`/`thinkingLevelMap`, Grok
   `initialize` and `session/new`, Muse's embedded `ReasoningEffort` schema,
   and Claude's `--help` / native command acknowledgement.
3. Launch real Codex and PI at xhigh. Change to low, complete a turn, change back
   to xhigh and complete another. Confirm exact values and error-free results.
4. Launch PI/Qwen on Spark, execute file/tool work, compare database and WebSocket
   replay, change thinking off/on, stop/resume and verify the saved effort.
5. On an iPhone 17 Pro simulator, verify all launch choices, Spark-only Qwen,
   `/effort` discovery/confirmation, and that a disconnected command never stages
   an ordinary user prompt. Test unsupported choices and preserved active turns.

Live evidence and deployment results are under
`~/.niuu/validation/forge-effort-20260909`; iOS build 2245 evidence is under
`~/.niuu/validation/lexi-ios-effort-2245`.

Sources: [Codex app-server model discovery](https://learn.chatgpt.com/docs/app-server#list-models-modellist),
[Claude effort](https://platform.claude.com/docs/en/build-with-claude/effort),
[Claude Code model configuration](https://code.claude.com/docs/en/model-config),
and the installed PI 0.85.1 `docs/models.md`, Grok ACP replies, and Muse MSP schema.
The native artifacts, rather than guessed generic provider levels, establish the
harness-specific matrix above.
