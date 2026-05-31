# Context Management

Ravn manages the LLM context window to maximize useful information while
staying within token limits. This involves compression, prompt building,
and caching.

## Compression

When the conversation history approaches the context window limit, Ravn
automatically compresses older messages to make room.

### When Compression Fires

Compression activates when the conversation uses more than
`compression_threshold` (default: 80%) of the available context window.

### What Happens

1. **Protected messages** are preserved verbatim:
   - First N messages (default: 2) — system prompt and initial context
   - Last N messages (default: 6) — recent conversation
   - Last N turns (default: 3) — preserved verbatim
2. **Middle messages** are compressed into a summary document
3. The summary replaces the original messages, freeing tokens

### Compression Strategy

The compressor generates a condensed summary (max `compression_max_tokens`,
default: 1024) that captures:

- Key decisions made
- Important facts discovered
- Tool results that are still relevant
- Unresolved questions or pending work

## Prompt Builder

The prompt builder assembles the full prompt sent to the LLM on each turn:

1. **System prompt** — base persona instructions
2. **Project context** — from RAVN.md and context files
3. **Prefetched memory** — relevant past episodes
4. **Lessons learned** — from outcome reflection
5. **Conversation history** — (potentially compressed)
6. **Current user message**

### Two-Layer Caching

The prompt builder uses two cache layers:

| Layer | Scope | Config |
|-------|-------|--------|
| In-process LRU | Per-session, in memory | `prompt_cache_max_entries` (default: 16) |
| Persistent disk | Cross-session, on disk | `prompt_cache_dir` (default: `~/.ravn/prompt_cache`) |

### Anthropic Prompt Caching

When using the Anthropic adapter, Ravn leverages Anthropic's prompt caching
feature. Static sections of the prompt (system prompt, persona instructions)
are marked as cacheable, reducing input token costs on subsequent turns.

## Iteration Budget

The iteration budget limits total tool-call iterations across a session:

```yaml
iteration_budget:
  total: 90
  near_limit_threshold: 0.8
```

When the agent reaches 80% of the budget (72 iterations), it receives a
"near limit" warning in the system prompt. This encourages the agent to
wrap up or prioritize remaining work.

## Configuration

```yaml
context_management:
  compression_threshold: 0.8
  protect_first_messages: 2
  protect_last_messages: 6
  compact_recent_turns: 3
  compression_max_tokens: 1024
  prompt_cache_max_entries: 16
  prompt_cache_dir: "~/.ravn/prompt_cache"

iteration_budget:
  total: 90
  near_limit_threshold: 0.8
```

See the [Configuration Reference](../configuration/reference.md#context_management)
for all fields.

Related: [NIU-431](https://linear.app/niuulabs/issue/NIU-431)
