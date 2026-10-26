# No Hidden Limits — Every Cap Has a Reason and Every Hit Is Loud

## Rule

Do not add a token budget, turn/iteration/attempt count, retry count,
length or character cap, list limit, or truncation unless something real
requires it. When one is required, hitting it must be **loud**: an error (or
an error-level log for an operation that must continue) that names the knob
and the remedy, and a counter on the component's health surface. Hitting a
cap is never "no result", "nothing to do", an empty string, or `None`.

This is the companion to `no-fallbacks.md`: a silent cap *is* a fallback.
It turns "the work did not fit" into "there was no work".

```python
# ❌ FORBIDDEN: a guessed cap, and a hit that reads as "nothing learned"
response = await llm.generate(..., max_tokens=1024)
if not response.content:
    return None

# ❌ FORBIDDEN: a hard turn limit that quietly stops asking/trying
if case.turn >= 3:
    return  # give up

# ✅ REQUIRED: no cap where a real bound exists; a cap that is hit raises
response = await llm.generate(..., max_tokens=None)   # server uses remaining context
if response.stop_reason == StopReason.MAX_TOKENS:
    raise ReflectionError(
        "reflection was cut off at reflection.max_tokens=...; raise it"
    )
```

## Why this keeps mattering

Every one of these was found by an operator, weeks or hours late, never by
a signal:

- Reflection ran with a 1024-token default — a number from models that
  answered without reasoning. Qwen3.8 reasons for ~1.1k tokens first, so
  every reflection came back empty, was logged at info as "no learning
  extracted", and residents stopped learning while looking healthy.
- The lesson judge's 512-token default returned empty JSON the same way.
- A 3-turn cap on resident case continuations (`continuation_max_turns`)
  stopped a case on the turn it asked the operator a question, so the
  question was never saved. Telegram still showed it; the operator's reply
  had nothing to bind to and became an unrelated task, and a parallel turn
  hit the same cap and asked the same question again.
- A failed Mímir search returned `None`, which read as "no matching lesson",
  so the same lesson was recorded again and again and never promoted.
- The replacement 8192 for the reflection budget was itself a guess copied
  from another knob, not derived from anything.

## Deciding whether a cap may exist

Before adding (or keeping) a cap, write down **what really bounds the
resource**:

| Resource | Real bound | Consequence |
|---|---|---|
| Model output tokens | The model's context window and the request timeout | Omit `max_tokens` (send none) where the API allows it; where it is required (Anthropic), derive it from the model catalog's limit, never a literal |
| Turns / iterations / attempts | A wall-clock deadline, a budget the operator set, or a decision the agent makes | Bound by that; a count is only acceptable when retrying is pointless past it, and exhausting it raises |
| Retries | Whether a retry can change the outcome | Never retry an identical request that failed deterministically (truncation, validation); retry only transient failures |
| Lengths / excerpts | A downstream hard limit (prompt budget, column size, Telegram message size) | Use that limit; when you truncate, say so in the output and count it |

If you cannot name the real bound, the cap does not go in.

## Requirements for every cap that remains

1. **Config, not literal** — per `no-magic-numbers.md`, with the description
   stating what bounds it and what happens on a hit.
2. **A hit raises or errors loudly**, naming the knob and the remedy
   ("raise `reflection.max_tokens`", "set `x` to …").
3. **A hit is counted** on the component's health surface (resident durable
   health / scorecard, service metrics), so it is monitored, not discovered.
4. **Tests assert the hit is loud.** A test that asserts a capped path
   "returns None", "continues", or "does not raise" encodes this defect.

## Report through `ravn.cap_telemetry`

In Ravn, every hit goes through the shared helper in `src/ravn/cap_telemetry.py`
so it lands on one counter and one health surface. Do not add a parallel
counter, metric name or log format:

- **Model answers:** `require_complete(response, knob=…, budget=…, model=…,
  purpose=…)` after every `LLMPort.generate`/`stream` call. It raises
  `OutputTruncatedError` on `StopReason.MAX_TOKENS` and counts the hit.
- **Truncation:** `record_truncation(knob, what=…, kept=…, total=…)` wherever
  content is cut.
- **Any other cap:** `record_cap_hit(knob, message)` just before raising or
  holding work back (iterations, the daily budget, list limits).
- **A path that must continue** because raising would lose more, such as an
  operator reply that could not be saved: `record_degraded(path, message)`.

These log at error and count on `ravn.cap.hits` / `ravn.degraded`. The
resident health snapshot carries them as `cap_hits_total`, `cap_hits.<knob>`,
`degraded_total` and `degraded.<path>`. `knob` is the config path the
operator would change. Limits and decisions are listed in
`docs/operator/ravn-limits.md`; add a row when you add or keep a cap.

## In review — the patterns to reject

- A literal number for tokens, turns, attempts, retries, lengths, or items.
- `max_tokens=<small number>` on a reasoning model call.
- `if count >= N: return` / `break` with no error and no counter.
- `text[:N]` without marking the truncation and counting it.
- Retrying a request that failed deterministically at the same settings.
- Any of these combined with `except Exception: logger.warning(...)` —
  see `no-fallbacks.md`.
