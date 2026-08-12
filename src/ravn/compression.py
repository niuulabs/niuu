"""Context compression for long Ravn sessions.

When a session's conservative estimated token count exceeds a threshold
(default 80% of the configured safe prompt budget), the ContextCompressor performs
*intelligent compaction*: rather than summarising the full conversation, it
produces a structured state document that preserves decision-relevant information
while discarding redundant content.

Compaction strategy
-------------------
1. **Identifies**: decisions made, tools called and their outcomes, open
   questions, active todos.
2. **Preserves verbatim**: the most recent ``protect_last`` messages
   (configurable via ``compact_recent_turns``); any DECISION events; any
   ERROR events.
3. **Summarises**: resolved tool call chains where the result was clean.
4. **Drops**: redundant back-and-forth and successfully-completed uncontested
   actions.
5. **Injects**: the active todo list and current episodic memory summary as
   anchors (when supplied by the caller).

The compacted context is not the conversation — it is a structured state
document::

    ## Active task
    ...

    ## Decisions made
    - ...

    ## Errors encountered
    - ...

    ## Actions completed
    - ...

    ## Open questions
    - ...

Protected regions
-----------------
- First ``protect_first`` messages are never touched (system context, initial
  instructions).
- Last ``protect_last`` messages are never touched (recent work in progress —
  the verbatim recent turns).

Trigger
-------
- Automatic: when estimated prompt use exceeds 80% of the safe prompt budget
  (``compression_threshold`` defaults to 0.8).
- Manual: callers may call ``maybe_compress`` at any time.

Multiple passes are allowed: compaction repeats until the session falls below
the threshold or no further reduction is possible.

Compression statistics are returned in a ``CompressionResult`` so callers can
log, expose via events, or surface to the user.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ravn.budget import TokenEstimator
from ravn.domain.models import LLMResponse, Message, TodoItem
from ravn.ports.llm import LLMPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM prompt constants
# ---------------------------------------------------------------------------

_COMPACT_SYSTEM = (
    "You are an intelligent context compactor for an AI agent session.  "
    "Analyse the conversation segment and produce a concise structured state "
    "document.  Preserve all decision-relevant information; discard redundant "
    "back-and-forth and successfully-completed uncontested actions.\n\n"
    "Output ONLY the structured document — no preamble, no extra commentary.\n\n"
    "Required sections (use exactly these headings):\n"
    "  ## Active task\n"
    "  ## Decisions made\n"
    "  ## Errors encountered\n"
    "  ## Actions completed\n"
    "  ## Open questions"
)

_COMPACT_USER_TEMPLATE = (
    "{todo_anchor}{memory_anchor}Conversation segment to compact:\n\n{transcript}"
)

_TODO_ANCHOR_TEMPLATE = "Active todos (preserve as context anchors):\n{todos}\n\n"

_MEMORY_ANCHOR_TEMPLATE = (
    "Episodic memory summary (use as background context):\n{memory_summary}\n\n"
)

_COMPACTED_PLACEHOLDER = "[Compacted context]\n\n{document}"

_FALLBACK_DOCUMENT = (
    "## Active task\n"
    "Unknown\n\n"
    "## Decisions made\n"
    "- [summary unavailable]\n\n"
    "## Errors encountered\n"
    "- None\n\n"
    "## Actions completed\n"
    "- None\n\n"
    "## Open questions\n"
    "- None"
)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class CompressionResult:
    """Describes what happened during a compression pass."""

    original_count: int
    final_count: int
    compression_count: int = 0
    removed_message_count: int = 0

    @property
    def was_compressed(self) -> bool:
        return self.compression_count > 0


# ---------------------------------------------------------------------------
# ContextCompressor
# ---------------------------------------------------------------------------


class ContextCompressor:
    """Compresses message history when it approaches the context window limit.

    Rather than a simple summarisation, this compactor produces a structured
    state document capturing decisions, errors, completed actions, and open
    questions — so the agent retains decision-relevant context even after
    aggressive compaction.

    Parameters
    ----------
    llm:
        LLM port used to generate compaction documents.
    model:
        Model identifier used for generating compaction documents.
    max_tokens:
        Max tokens for compaction document generation (default 1024).
    protect_first:
        Number of messages at the start of history to preserve unchanged.
    protect_last:
        Number of messages at the end of history to preserve unchanged
        (the verbatim recent turns).  Defaults to 6 (≈ 3 turns).
    compression_threshold:
        Fraction of the safe prompt budget that triggers compaction.
    context_window:
        Configured context-window size. Zero means unknown.
    output_reserve:
        Tokens reserved for model output when deriving a prompt budget from
        ``context_window``.
    """

    def __init__(
        self,
        llm: LLMPort,
        *,
        model: str,
        max_tokens: int = 1024,
        protect_first: int = 2,
        protect_last: int = 6,
        compression_threshold: float = 0.8,
        context_window: int = 0,
        output_reserve: int = 0,
        token_estimate_safety_factor: float = 1.0,
    ) -> None:
        self._llm = llm
        self._model = model
        self._max_tokens = max_tokens
        self._protect_first = protect_first
        self._protect_last = protect_last
        self._threshold = compression_threshold
        self._context_window = max(0, context_window)
        self._output_reserve = max(0, output_reserve)
        self._token_estimate_safety_factor = max(1.0, token_estimate_safety_factor)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def protect_last(self) -> int:
        """How many trailing messages compaction will never touch.

        Callers that must keep the prompt inside a hard budget need this: a
        message in the protected tail cannot be shrunk later, so whatever
        enters it has to fit on the way in.
        """
        return self._protect_last

    async def maybe_compress(
        self,
        messages: list[Message],
        *,
        system_tokens: int = 0,
        tool_tokens: int = 0,
        prompt_budget_tokens: int = 0,
        todos: list[TodoItem] | None = None,
        memory_summary: str | None = None,
    ) -> tuple[list[Message], CompressionResult]:
        """Compact *messages* if estimated tokens exceed the threshold.

        Parameters
        ----------
        messages:
            The current conversation history.
        system_tokens:
            Estimated token count for the system prompt (added to the
            per-message estimate before threshold comparison).
        todos:
            Active todo items injected as anchors in the compacted document.
        memory_summary:
            Episodic memory summary injected as background context.

        Returns the (possibly compacted) message list and a
        ``CompressionResult``.  When no compaction was needed, the original
        list is returned unchanged.
        """
        original_count = len(messages)
        configured_budget = max(0, self._context_window - self._output_reserve)
        budget = prompt_budget_tokens or configured_budget
        if budget <= 0:
            return messages, CompressionResult(
                original_count=original_count, final_count=original_count
            )
        threshold_tokens = int(budget * self._threshold)

        estimated = self._estimate(
            TokenEstimator.rough_messages(messages) + system_tokens + tool_tokens
        )
        if estimated <= threshold_tokens:
            return messages, CompressionResult(
                original_count=original_count, final_count=original_count
            )

        result_messages = messages
        compression_count = 0
        removed_count = 0

        while True:
            new_messages, removed = await self._compact_once(
                result_messages, todos=todos, memory_summary=memory_summary
            )
            if removed == 0:
                # Cannot compact further (protected zones cover everything).
                break
            compression_count += 1
            removed_count += removed
            result_messages = new_messages
            new_estimated = self._estimate(
                TokenEstimator.rough_messages(result_messages) + system_tokens + tool_tokens
            )
            if new_estimated <= threshold_tokens:
                break

        return result_messages, CompressionResult(
            original_count=original_count,
            final_count=len(result_messages),
            compression_count=compression_count,
            removed_message_count=removed_count,
        )

    def _estimate(self, tokens: int) -> int:
        return TokenEstimator.conservative(tokens, self._token_estimate_safety_factor)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _compact_once(
        self,
        messages: list[Message],
        *,
        todos: list[TodoItem] | None = None,
        memory_summary: str | None = None,
    ) -> tuple[list[Message], int]:
        """Run a single compaction pass.

        Returns ``(new_messages, removed_count)``.  ``removed_count`` is 0
        when the protected zones leave no compressible middle section.
        """
        total = len(messages)
        protect_first = min(self._protect_first, total)
        protect_last = min(self._protect_last, max(0, total - protect_first))

        middle_start = protect_first
        middle_end = total - protect_last if protect_last > 0 else total
        middle = messages[middle_start:middle_end]

        if not middle:
            return messages, 0

        document = await self._build_state_document(
            middle, todos=todos, memory_summary=memory_summary
        )
        compacted_msg = Message(
            role="user",
            content=_COMPACTED_PLACEHOLDER.format(document=document),
        )

        head = messages[:protect_first]
        tail = messages[total - protect_last :] if protect_last > 0 else []
        # middle (len M) is replaced by 1 compacted message → removed = M - 1
        removed = len(middle) - 1
        return head + [compacted_msg] + tail, removed

    async def _build_state_document(
        self,
        messages: list[Message],
        *,
        todos: list[TodoItem] | None = None,
        memory_summary: str | None = None,
    ) -> str:
        """Ask the LLM to produce a structured state document from *messages*."""
        transcript = _format_transcript(messages)
        todo_anchor = _format_todo_anchor(todos)
        memory_anchor = _format_memory_anchor(memory_summary)
        user_text = _COMPACT_USER_TEMPLATE.format(
            todo_anchor=todo_anchor,
            memory_anchor=memory_anchor,
            transcript=transcript,
        )
        try:
            response: LLMResponse = await self._llm.generate(
                [{"role": "user", "content": user_text}],
                tools=[],
                system=_COMPACT_SYSTEM,
                model=self._model,
                max_tokens=self._max_tokens,
            )
            return response.content.strip() or _FALLBACK_DOCUMENT
        except Exception as exc:
            logger.warning("Compaction document generation failed: %s", exc)
            return _FALLBACK_DOCUMENT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_content_text(content: str | list[dict]) -> str:
    """Extract plain text from a message content value."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type", "")
        match block_type:
            case "text":
                if block.get("text"):
                    parts.append(block["text"])
            case "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                parts.append(f"[tool_call:{name} input={inp}]")
            case "tool_result":
                c = block.get("content", "")
                parts.append(f"[tool_result: {c}]")
            case _:
                pass
    return " ".join(parts)


def _format_transcript(messages: list[Message]) -> str:
    """Render messages to a plain-text transcript for the compaction prompt."""
    lines: list[str] = []
    for msg in messages:
        text = _extract_content_text(msg.content)
        if text:
            lines.append(f"{msg.role.upper()}: {text}")
    return "\n\n".join(lines)


def _format_todo_anchor(todos: list[TodoItem] | None) -> str:
    """Format the todo list as an anchor section, or return empty string."""
    if not todos:
        return ""
    lines = [f"  [{todo.status}] {todo.content}" for todo in todos]
    return _TODO_ANCHOR_TEMPLATE.format(todos="\n".join(lines))


def _format_memory_anchor(memory_summary: str | None) -> str:
    """Format the memory summary as an anchor section, or return empty string."""
    if not memory_summary:
        return ""
    stripped = memory_summary.strip()
    if not stripped:
        return ""
    return _MEMORY_ANCHOR_TEMPLATE.format(memory_summary=stripped)
