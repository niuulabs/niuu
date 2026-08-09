# No Fallbacks — Fail Hard

## Rule

When something configured cannot be done, **raise**. Never substitute a
degraded path, never log-and-continue, never return an empty result that reads
as success.

```python
# ❌ FORBIDDEN
try:
    port = build_embedding_adapter(settings)
except Exception as exc:
    logger.warning("embedding unavailable: %s — falling back to FTS-only", exc)
    port = None

# ❌ FORBIDDEN
try:
    await memory.record_episode(episode)
except Exception:
    logger.warning("recording failed; continuing.", exc_info=True)

# ✅ REQUIRED
port = build_embedding_adapter(settings)   # raises, with the remedy in the message
await memory.record_episode(episode)       # raises
```

## Why this keeps mattering

Every incident in this codebase traced back to a fallback, not to the original
fault:

- `embedding.enabled: true` with no embedding library in the image logged one
  warning and ran keyword-only. Retrieval quality collapsed and no signal said
  so.
- A bare `Bearer ` header made every embed call fail. `record_episode` raised,
  the agent caught it and continued, and the resident **silently stopped
  recording episodes** while every other indicator looked healthy.
- The preferred resident-state adapter was unreachable on all nine residents.
  Each quietly demoted to its local store. Nobody knew until a counter was
  added months later.

A fallback converts a loud, one-line-to-diagnose failure into a silent,
weeks-to-discover data problem. The system keeps *appearing* to work, which is
strictly worse than stopping.

## The only legitimate escapes

1. **The operator asked to go without it.** `memory.backend: none` is a
   decision. `embedding.enabled: false` is a decision. Configuration that says
   "do without this" is honoured; configuration that asks for something and
   cannot get it is fatal.
2. **A protocol-level optional read** whose absence is the expected steady
   state and is reported — e.g. "no checkpoint exists yet". Returning `None`
   here is an answer, not a downgrade.

That is the whole list. In particular, **"the operator configured a second
implementation" is not an escape.** A declared fallback list is still a
fallback: it runs the system on something other than what the primary
configuration names, decided at runtime, usually without anyone noticing which
one served the request. If the primary cannot serve, stop. Do not try the next
one. Changing provider is a deploy, not a runtime branch.

## Applying it

- Put the remedy in the exception message ("set `x` to disable, or fix `y`").
- Never widen a `try` to cover more than the operation whose failure you are
  deliberately typing.
- `except Exception: logger.warning(...)` around anything stateful is the
  pattern to look for in review; it is almost always this bug.
- Tests that assert "does not crash", "continues", or "falls back" are
  encoding the defect. Rewrite them to assert the raise — several such tests
  are why these bugs survived for months.
