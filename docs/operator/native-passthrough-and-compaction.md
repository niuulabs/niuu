# Native passthrough and native compaction

A provider setting that lets a model run on its own provider's protocol, so a client
that speaks it can use that provider's own context compaction:

| Setting | Where | Choices | Default |
|---|---|---|---|
| `passthrough` | Bifröst, per provider | `true` / `false` | `false` (canonical translation) |

With it unset, every deployment behaves exactly as before.

## Bifröst: native passthrough

Bifröst's canonical path translates every request into the Anthropic Messages format and
back. For an OpenAI model served by the ChatGPT subscription backend that is four hops
(chat-completions or Responses → Anthropic → Responses → Anthropic → caller), and each hop
loses or reshapes something the provider has and Anthropic does not: encrypted reasoning,
`phase` on message items, hosted tools, `context_management`, compaction items.

A provider with `passthrough: true` is instead served in its **native protocol** when the
caller speaks it:

| Adapter | Native protocol | Inbound endpoint served natively |
|---|---|---|
| `bifrost.adapters.chatgpt_subscription.ChatGPTSubscriptionAdapter` | OpenAI Responses | `POST /v1/responses` |
| `bifrost.adapters.openai_compat.OpenAICompatAdapter` (and Ollama) | Chat Completions | `POST /v1/chat/completions` |

The request body goes to the provider verbatim — Bifröst rewrites only the model name (to
the routed one), and the adapter adds what its backend requires (`stream: true` for the
ChatGPT backend; `stream_options.include_usage` and the provider's `chat_template_kwargs`
for Chat Completions). The provider's stream comes back to the caller unchanged.

What Bifröst still does on the passthrough path, identically to the canonical path:
authentication, model access control, tenant/agent quotas, budget guardrails, routing
rules and model selection (evaluated on a canonical *view* of the request), usage records,
cost, Prometheus metrics, the audit event, and the Sleipnir cost events. Usage is read from
the native stream (`response.completed.usage` for Responses, the `include_usage` chunk for
Chat Completions) through the same mapping the canonical translation uses, so a request is
metered and priced the same on either path. A `strip_images` rule is applied to the native
payload.

Callers on other protocols keep the canonical translation, so both paths run side by side
against the same provider: with `passthrough: true` on `chatgpt`, a Responses client is
passed through while a chat-completions or Anthropic client of the same model is translated.

```yaml
providers:
  chatgpt:
    adapter: bifrost.adapters.chatgpt_subscription.ChatGPTSubscriptionAdapter
    models: [gpt-6-sol]
    passthrough: true
    kwargs: { ... }
```

### Startup errors

Bifröst refuses to start when:

- `passthrough: true` is set on a provider whose adapter has no native protocol (it does
  not implement `bifrost.ports.native.NativePassthroughPort`) — e.g. the Anthropic adapter,
  whose canonical path already *is* its native protocol;
- the providers of one model disagree on the path (the protocol of a request must not
  depend on which provider is selected);
- `cache.mode` is not `disabled` while any provider has passthrough (the response cache
  stores canonical responses only).

### What only the passthrough path can honour

On `/v1/responses`, `context_management`, `previous_response_id`, and input items other
than `message` / `function_call` / `function_call_output` / `reasoning` (for example
`compaction`, `compaction_trigger`, `item_reference`) are forwarded on the passthrough
path and refused with a 400 on the canonical one — never silently dropped. A passthrough
request that asks for compaction from a provider that cannot do it is also a 400.

On `/v1/messages`, Anthropic `context_management` and `compaction` blocks are forwarded by
the Anthropic adapter (with the `anthropic-beta` header each edit needs) and refused with a
400 for any translating provider. Anthropic compaction iterations (`usage.iterations`) are
added to the metered tokens, since the top-level counts exclude them.

## What the ChatGPT Codex backend accepts

Verified live against `https://chatgpt.com/backend-api/codex/responses` on 2026-10-24:

- `context_management: [{"type": "compaction", "compact_threshold": N}]` — accepted. Above
  the threshold the stream carries `compaction` output items (`id`, `encrypted_content`),
  before the answer and, if the window is still over the threshold, after it. A follow-up
  that sends only `[latest compaction item, new message]` keeps the context.
- A `{"type": "compaction_trigger"}` input item (what the Codex CLI sends for explicit
  compaction) — accepted; the response is a single compaction item.
- `POST /responses/compact` — **404**; not available on this backend.
- A `function_call_output` whose `function_call` was cut away returns an empty response, so
  a client must keep call/output pairs together when it cuts history at a compaction item.
