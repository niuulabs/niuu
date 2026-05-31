# Extended Thinking

Extended thinking enables Claude to reason through complex problems before
responding. When active, the model generates a "thinking" block with
step-by-step reasoning, followed by the actual response.

## How It Works

Extended thinking is a Claude-only feature. When enabled, the Anthropic
adapter sends the `thinking` parameter with a token budget. Claude uses
this budget for internal reasoning that is visible in the response but
not counted toward the output token limit.

## Auto-Trigger Conditions

When `auto_trigger` is enabled (default), thinking activates automatically on:

- **Planning tasks** — when the agent needs to design an approach
- **Ambiguous prompts** — when the user's intent is unclear
- **Complex reasoning** — multi-step logical problems

When `auto_trigger_on_retry` is enabled (default), thinking also activates
after the first tool failure in a turn, giving the agent deeper reasoning
for the retry.

## Budget

The thinking budget controls how many tokens the model can spend on reasoning:

```yaml
llm:
  extended_thinking:
    enabled: true
    budget_tokens: 8000
```

Higher budgets allow deeper reasoning but increase latency and cost.
Thinking tokens are priced at approximately 80% of output token cost.

## Tracking

Thinking tokens are tracked separately in the usage breakdown:

- `input_tokens` — prompt tokens
- `output_tokens` — response tokens
- `thinking_tokens` — reasoning tokens
- `cache_read_tokens` — prompt cache hits
- `cache_creation_tokens` — prompt cache writes

Use `--show-usage` with `ravn run` to see the breakdown after each turn.

## Fallback for Non-Anthropic Providers

When using the fallback adapter chain, thinking is automatically skipped
for non-Anthropic providers. The `FallbackLLMAdapter` detects provider
capabilities and only sends the thinking parameter to Anthropic.

This means you can configure a fallback chain like:

```yaml
llm:
  provider:
    adapter: ravn.adapters.llm.anthropic.AnthropicAdapter
  fallbacks:
    - adapter: ravn.adapters.llm.openai.OpenAIAdapter
      kwargs:
        model: gpt-4o
  extended_thinking:
    enabled: true
    budget_tokens: 8000
```

Thinking works on the primary (Anthropic) provider and is silently skipped
on the fallback (OpenAI) provider.

## Configuration

```yaml
llm:
  extended_thinking:
    enabled: false          # Allow thinking activation
    budget_tokens: 8000     # Max reasoning tokens
    auto_trigger: true      # Auto-activate on planning/ambiguous tasks
    auto_trigger_on_retry: true  # Auto-activate after tool failure
```

Personas can override thinking settings:

```yaml
# autonomous-agent persona
thinking:
  enabled: true
  budget_tokens: 10000
```
