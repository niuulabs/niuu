"""Core Ravn agent loop."""

from __future__ import annotations

import itertools
import logging
import re
import time
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from niuu.observability import get_observability
from niuu.ports.mimir import MimirPort
from ravn.budget import IterationBudget, TokenEstimator
from ravn.compression import CompressionResult, ContextCompressor
from ravn.config import ExtendedThinkingConfig
from ravn.domain.budget import compute_cost as _compute_cost_usd
from ravn.domain.checkpoint import (
    DESTRUCTIVE_TOOL_NAMES,
    Checkpoint,
    InterruptReason,
)
from ravn.domain.events import RavnEvent
from ravn.domain.exceptions import (
    MaxIterationsError,
    PermissionDeniedError,
    PromptBudgetExceededError,
)
from ravn.domain.models import (
    Episode,
    LLMResponse,
    Message,
    Outcome,
    Session,
    StopReason,
    StreamEventType,
    TokenUsage,
    ToolCall,
    ToolResult,
    TurnResult,
)
from ravn.ports.channel import ChannelPort
from ravn.ports.checkpoint import CheckpointPort
from ravn.ports.llm import LLMPort, SystemPrompt
from ravn.ports.memory import MemoryPort
from ravn.ports.permission import PermissionPort
from ravn.ports.tool import ToolPort
from ravn.prompt_builder import PromptBuilder

if TYPE_CHECKING:
    from niuu.domain.outcome import ParsedOutcome
    from ravn.adapters.personas.loader import PersonaConfig

try:
    from sleipnir.domain.catalog import ravn_session_ended, ravn_session_started
except ImportError:  # sleipnir not available in all environments
    ravn_session_started = None  # type: ignore[assignment]
    ravn_session_ended = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Hook type: async callable receiving the tool call and (for post) the result.
PreToolHook = Callable[[ToolCall], Coroutine[Any, Any, None]]
PostToolHook = Callable[[ToolCall, ToolResult], Coroutine[Any, Any, None]]

# Async callable that receives a question string and returns the user's answer.
UserInputFn = Callable[[str], Coroutine[Any, Any, str]]

_ASK_USER_TOOL_NAME = "ask_user"


class RavnAgent:
    """Turn-based agent that converses with an LLM and executes tools.

    The agent maintains a session (conversation history) and runs a loop
    for each user turn:
    1. Append the user message to history.
    2. Call the LLM (streaming).
    3. If the LLM requests tool calls, execute them (with permission check and hooks).
    4. Feed tool results back to the LLM and repeat from step 2.
    5. When the LLM returns a final response (stop_reason != tool_use), return.

    The ``ask_user`` tool is agent-intercepted: when the LLM calls it, the
    agent pauses the loop, emits the question to the channel, waits for the
    caller-supplied ``user_input_fn``, and injects the answer as a tool result.
    """

    def __init__(
        self,
        llm: LLMPort,
        tools: list[ToolPort],
        channel: ChannelPort,
        permission: PermissionPort,
        *,
        system_prompt: str,
        model: str,
        max_tokens: int,
        max_iterations: int,
        pre_tool_hooks: list[PreToolHook] | None = None,
        post_tool_hooks: list[PostToolHook] | None = None,
        user_input_fn: UserInputFn | None = None,
        memory: MemoryPort | None = None,
        episode_summary_max_chars: int = 500,
        episode_task_max_chars: int = 200,
        iteration_budget: IterationBudget | None = None,
        compressor: ContextCompressor | None = None,
        prompt_builder: PromptBuilder | None = None,
        reflection_model: str = "claude-haiku-4-5-20251001",
        reflection_max_tokens: int = 512,
        task_summary_max_chars: int = 200,
        input_token_cost_per_million: float = 3.0,
        output_token_cost_per_million: float = 15.0,
        extended_thinking: ExtendedThinkingConfig | None = None,
        session: Session | None = None,
        checkpoint_port: CheckpointPort | None = None,
        task_id: str | None = None,
        # NIU-537: named snapshot trigger configuration
        checkpoint_every_n_tools: int = 0,
        auto_checkpoint_before_destructive: bool = False,
        budget_milestone_fractions: list[float] | None = None,
        # NIU-582: Sleipnir lifecycle events
        sleipnir_publisher: object | None = None,
        persona: str = "",
        repo_slug: str = "",
        # NIU-588: learnings injection at session start
        mimir: MimirPort | None = None,
        # NIU-594: persona config for outcome block parsing
        persona_config: PersonaConfig | None = None,
        # Retained for constructor compatibility. A tool-use response is never
        # final, even when its text already contains an outcome block.
        stop_on_outcome: bool = False,
        # NIU-1118: hard per-call prompt budget; 0 disables
        max_prompt_tokens: int = 0,
        context_window_tokens: int = 0,
        token_estimate_safety_factor: float = 1.0,
        # NIU-1118: cap one tool result's contribution to history; 0 disables
        max_tool_result_chars: int = 0,
    ) -> None:
        self._llm = llm
        self._tools = {t.name: t for t in tools}
        self._channel = channel
        self._permission = permission
        self._system_prompt = system_prompt
        self._model = model
        self._max_tokens = max_tokens
        self._max_iterations = max_iterations
        self._pre_tool_hooks: list[PreToolHook] = pre_tool_hooks or []
        self._post_tool_hooks: list[PostToolHook] = post_tool_hooks or []
        self._user_input_fn = user_input_fn
        self._memory = memory
        self._mimir = mimir
        self._episode_summary_max_chars = episode_summary_max_chars
        self._episode_task_max_chars = episode_task_max_chars
        self._iteration_budget = iteration_budget
        self._compressor = compressor
        self._prompt_builder = prompt_builder
        # Set identity on prompt_builder if provided — this was missing and caused
        # empty system prompts when using PromptBuilder with personas.
        if prompt_builder is not None:
            prompt_builder.set_identity(system_prompt)
        self._reflection_model = reflection_model
        self._reflection_max_tokens = reflection_max_tokens
        self._task_summary_max_chars = task_summary_max_chars
        self._input_token_cost_per_million = input_token_cost_per_million
        self._output_token_cost_per_million = output_token_cost_per_million
        self._extended_thinking = extended_thinking
        self._session = session or Session()
        self._source_id = f"ravn-{uuid.uuid4().hex[:8]}"
        self._last_compression_result: CompressionResult | None = None
        self._trace_iteration = 0
        self._checkpoint_port = checkpoint_port
        self._task_id = task_id or str(self._session.id)
        self._interrupt_reason: InterruptReason | None = None
        # NIU-537: named snapshot trigger state
        self._checkpoint_every_n_tools = checkpoint_every_n_tools
        self._auto_checkpoint_before_destructive = auto_checkpoint_before_destructive
        self._budget_milestones: list[float] = budget_milestone_fractions or []
        self._tool_call_count: int = 0
        self._fired_milestones: set[float] = set()
        # NIU-582: Sleipnir lifecycle event support
        self._sleipnir_publisher = sleipnir_publisher
        self._persona = persona
        self._repo_slug = repo_slug
        self._turn_count: int = 0
        self._session_wall_start: float = time.monotonic()
        self._session_ended_emitted: bool = False
        # NIU-588: learnings injection at session start
        self._mimir = mimir
        # NIU-594: persona config for outcome block parsing
        self._persona_config = persona_config
        self._context_window_tokens = max(0, context_window_tokens)
        self._token_estimate_safety_factor = max(1.0, token_estimate_safety_factor)
        if self._context_window_tokens and self._max_tokens >= self._context_window_tokens:
            raise ValueError("max_tokens must be smaller than context_window_tokens")
        prompt_ceilings = [
            value
            for value in (
                max(0, max_prompt_tokens),
                max(0, self._context_window_tokens - self._max_tokens),
            )
            if value > 0
        ]
        self._max_prompt_tokens = min(prompt_ceilings) if prompt_ceilings else 0
        self._last_prompt_budget_status: dict[str, int | float | bool] = {}
        # NIU-1118: cap one tool result's contribution to history; 0 disables
        self._max_tool_result_chars = max_tool_result_chars

    @property
    def session(self) -> Session:
        return self._session

    @property
    def tools(self) -> list[ToolPort]:
        """Return all registered tools in registration order."""
        return list(self._tools.values())

    def register_tool(self, tool: ToolPort, *, replace: bool = False) -> None:
        """Register a tool for subsequent LLM iterations in this session."""
        if tool.name in self._tools and not replace:
            raise ValueError(f"Tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    @property
    def max_iterations(self) -> int:
        """Maximum tool-call iterations allowed per turn."""
        return self._max_iterations

    @property
    def iteration_budget(self) -> IterationBudget | None:
        """The iteration budget shared with this agent, or None."""
        return self._iteration_budget

    @property
    def last_compression_result(self) -> CompressionResult | None:
        """Compression result from the most recent turn, or None."""
        return self._last_compression_result

    @property
    def prompt_budget_status(self) -> dict[str, int | float | bool]:
        """Latest preflight prompt-budget facts for telemetry and the resident HUD."""
        return dict(self._last_prompt_budget_status)

    @property
    def task_id(self) -> str:
        """Stable task identifier used for checkpointing."""
        return self._task_id

    @property
    def checkpoint_port(self) -> CheckpointPort | None:
        """The checkpoint port, or None if checkpointing is disabled."""
        return self._checkpoint_port

    def interrupt(self, reason: InterruptReason) -> None:
        """Signal the agent to stop at the next iteration boundary.

        Thread-safe: may be called from a signal handler or another coroutine.
        Subsequent calls are ignored (first reason wins).
        """
        if self._interrupt_reason is None:
            self._interrupt_reason = reason

    @property
    def llm(self) -> LLMPort:
        """The active LLM adapter (read-only, for composition wiring)."""
        return self._llm

    @property
    def llm_adapter_name(self) -> str:
        """Class name of the active LLM adapter."""
        return type(self._llm).__name__

    def _tool_defs(self) -> list[dict]:
        return [t.to_api_dict() for t in self._tools.values()]

    def _build_api_messages(self) -> list[dict]:
        """Convert session messages to the API format."""
        return [message.to_api_dict() for message in self._session.messages]

    async def _emit_session_started(self) -> None:
        """Publish ravn.session.started to Sleipnir (no-op when publisher absent)."""
        if self._sleipnir_publisher is None or ravn_session_started is None:
            return
        try:
            event = ravn_session_started(
                session_id=str(self._session.id),
                persona=self._persona,
                repo_slug=self._repo_slug,
                source=self._source_id,
                correlation_id=self._task_id,
            )
            await self._sleipnir_publisher.publish(event)
        except Exception:
            logger.warning("Failed to emit ravn.session.started; continuing.", exc_info=True)

    def _build_session_ended_event(self, outcome: str) -> object:
        """Build a ravn.session.ended SleipnirEvent with the given outcome string.

        Centralises the token/duration computation and ravn_session_ended() call
        so both emit_session_ended() and _emit_session_ended_with_outcome() stay DRY.
        """
        total_tokens = self._session.total_usage.total_tokens
        duration_s = round(time.monotonic() - self._session_wall_start, 3)
        return ravn_session_ended(
            session_id=str(self._session.id),
            persona=self._persona,
            outcome=outcome,
            token_count=total_tokens,
            duration_s=duration_s,
            source=self._source_id,
            repo_slug=self._repo_slug,
            correlation_id=self._task_id,
        )

    async def emit_session_ended(self, outcome: str) -> None:
        """Publish ravn.session.ended to Sleipnir.

        Call this once the entire session is complete (all turns done, normal
        exit, interrupt, or error).  No-op when no publisher is configured or
        when the event was already emitted (e.g. by outcome block capture).

        :param outcome: One of ``"success"``, ``"interrupted"``, or ``"error"``.
        """
        if self._sleipnir_publisher is None or ravn_session_ended is None:
            return
        if self._session_ended_emitted:
            return
        try:
            event = self._build_session_ended_event(outcome)
            await self._sleipnir_publisher.publish(event)
            self._session_ended_emitted = True
        except Exception:
            logger.warning("Failed to emit ravn.session.ended; continuing.", exc_info=True)

    async def _emit_session_ended_with_outcome(self, parsed_outcome: ParsedOutcome) -> None:
        """Publish ravn.session.ended enriched with structured outcome fields.

        Called from run_turn() when the agent produces a valid outcome block.
        Sets ``_session_ended_emitted`` so the external emit_session_ended()
        becomes a no-op and no double-emission occurs.
        """
        if self._sleipnir_publisher is None or ravn_session_ended is None:
            return
        try:
            outcome_str = "success" if parsed_outcome.valid else "partial"
            event = self._build_session_ended_event(outcome_str)
            event.payload["structured_outcome"] = parsed_outcome.fields
            event.payload["outcome_valid"] = parsed_outcome.valid
            if self._persona_config is not None:
                event.payload["outcome_event_type"] = self._persona_config.produces.event_type
            await self._sleipnir_publisher.publish(event)
            self._session_ended_emitted = True
        except Exception:
            logger.warning(
                "Failed to emit ravn.session.ended with outcome; continuing.", exc_info=True
            )

    async def run_turn(self, user_input: str) -> TurnResult:
        """Process one user turn and return the result.

        Runs the full tool-call loop until the LLM produces a final response
        or the maximum number of iterations is reached.

        If a memory adapter is configured:
        - Relevant past context is prefetched and appended to the system prompt.
        - A new episode is recorded after the turn completes.

        If an iteration_budget is configured:
        - Each LLM call consumes one budget unit.
        - Budget warnings are appended to tool result content.
        - When the budget is exhausted, MaxIterationsError is raised.

        If a compressor is configured:
        - Context is compressed before each LLM call when the estimated token
          count exceeds the compression threshold.

        If an outcome port is configured:
        - Past lessons learned are injected into the system prompt.
        - An episode (with LLM reflection) is recorded after the turn.
        """
        start_time = time.monotonic()

        # NIU-582: emit session.started on the first turn only.
        if self._turn_count == 0:
            await self._emit_session_started()
        self._turn_count += 1

        # Check budget before starting the turn.
        if self._iteration_budget is not None and self._iteration_budget.exhausted:
            raise MaxIterationsError(self._max_iterations)

        # Build the effective system prompt. When using a prompt_builder,
        # memory context is handled internally as a named section.
        # For the legacy path, prefetch memory here so memory_ctx is available
        # for outcome recording.
        memory_ctx = ""
        if self._prompt_builder is not None:
            effective_system: SystemPrompt = await self._build_effective_system(user_input)
        else:
            effective_system = self._system_prompt
            if self._memory is not None:
                try:
                    memory_ctx = await self._memory.prefetch(user_input)
                    if memory_ctx:
                        effective_system = f"{effective_system}\n\n{memory_ctx}"
                except Exception:
                    logger.warning(
                        "Memory prefetch failed; continuing without context.", exc_info=True
                    )

        # Determine whether explicit thinking was requested for this turn.
        explicit_thinking, user_input = _parse_think_flag(user_input)

        # One unconditional call, as MemoryPort.process_inline_facts has always
        # documented. Which store the facts land in — Mímir, the episodic
        # backend, or nowhere — is composition's decision, not the agent's.
        if self._memory is not None:
            try:
                await self._memory.process_inline_facts(str(self._session.id), user_input)
            except Exception:
                logger.warning("Inline fact detection failed; continuing.", exc_info=True)

        self._session.add_message(Message(role="user", content=user_input))

        turn_tool_calls: list[ToolCall] = []
        turn_tool_results: list[ToolResult] = []
        cumulative_usage = TokenUsage(input_tokens=0, output_tokens=0)
        final_response = ""
        self._last_compression_result = None
        last_had_tool_error = False

        # Log system prompt on first iteration for debugging
        if isinstance(effective_system, str):
            logger.debug("system_prompt (first 2000 chars): %s", effective_system[:2000])
        else:
            # Anthropic blocks format - log full content
            logger.debug("system_prompt_blocks: %d blocks", len(effective_system))
            for i, block in enumerate(effective_system):
                text = block.get("text", "")
                logger.debug("system_prompt_block[%d] (first 2000 chars): %s", i, text[:2000])

        iteration_indices = (
            itertools.count() if self._max_iterations <= 0 else range(self._max_iterations)
        )
        for iteration in iteration_indices:
            self._trace_iteration = iteration
            # Check external interruption (SIGINT/SIGTERM/Ting cancel via interrupt()).
            if self._interrupt_reason is not None:
                await self._write_checkpoint(
                    user_input=user_input,
                    partial_response=final_response,
                    last_tool_call=turn_tool_calls[-1] if turn_tool_calls else None,
                    last_tool_result=turn_tool_results[-1] if turn_tool_results else None,
                    interrupted_by=self._interrupt_reason,
                )
                raise MaxIterationsError(self._max_iterations)

            # Enforce iteration budget.
            if self._iteration_budget is not None and self._iteration_budget.exhausted:
                await self._write_checkpoint(
                    user_input=user_input,
                    partial_response=final_response,
                    last_tool_call=turn_tool_calls[-1] if turn_tool_calls else None,
                    last_tool_result=turn_tool_results[-1] if turn_tool_results else None,
                    interrupted_by=InterruptReason.BUDGET_EXHAUSTED,
                )
                raise MaxIterationsError(self._max_iterations)

            # Optionally compress context before calling the LLM.
            messages_for_llm = await self._maybe_compress(
                effective_system, memory_summary=memory_ctx
            )

            # NIU-1118: prompt-composition audit + hard budget. Runs after
            # compression so the budget judges what would actually be sent.
            prompt_sections = self._prompt_section_estimates(effective_system, messages_for_llm)
            if iteration == 0:
                self._log_prompt_composition(prompt_sections)
            self._record_prompt_budget(
                prompt_sections,
                compressed=bool(
                    self._last_compression_result and self._last_compression_result.was_compressed
                ),
            )
            self._enforce_prompt_budget(prompt_sections)

            thinking_param = self._resolve_thinking(
                iteration=iteration,
                explicit=explicit_thinking,
                last_had_tool_error=last_had_tool_error,
            )

            llm_response = await self._call_llm_streaming(
                system_prompt=effective_system,
                messages=messages_for_llm,
                thinking=thinking_param,
            )

            # Consume one iteration from the budget.
            if self._iteration_budget is not None:
                self._iteration_budget.consume()

            cumulative_usage = cumulative_usage + llm_response.usage

            if llm_response.stop_reason != StopReason.TOOL_USE:
                if llm_response.content:
                    await self._channel.emit(
                        RavnEvent.response(
                            source=self._source_id,
                            text=llm_response.content,
                            correlation_id=self._session.id,
                            session_id=self._session.id,
                        )
                    )
                final_response = llm_response.content
                self._session.add_message(
                    Message(
                        role="assistant",
                        content=llm_response.content,
                        reasoning=llm_response.reasoning,
                    )
                )
                break

            # Track partial response for checkpointing during tool-call iterations.
            if llm_response.content:
                final_response = llm_response.content

            # Append the assistant message (with tool calls) to history.
            assistant_content = _build_assistant_content(llm_response)
            self._session.messages.append(
                Message(
                    role="assistant",
                    content=assistant_content,
                    reasoning=llm_response.reasoning,
                )
            )

            # Execute all tool calls sequentially and collect results.
            tool_results_content = []
            last_had_tool_error = False
            for tool_call in llm_response.tool_calls:
                turn_tool_calls.append(tool_call)

                # NIU-537: auto-snapshot before destructive operations.
                if (
                    self._auto_checkpoint_before_destructive
                    and tool_call.name in DESTRUCTIVE_TOOL_NAMES
                ):
                    await self._maybe_save_snapshot(label=f"auto: before {tool_call.name}")

                result = await self._execute_tool(tool_call)

                # NIU-1118: cap the result BEFORE it enters history — one
                # oversized result lands in the compression-protected tail
                # and would make the rest of the turn unrecoverable.
                result = self._truncate_oversized_tool_result(tool_call, result)

                # Inject budget warning into the tool result content.
                result = _maybe_append_budget_warning(result, self._iteration_budget)

                if result.is_error:
                    last_had_tool_error = True
                turn_tool_results.append(result)
                tool_results_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call.id,
                        "name": tool_call.name,
                        "content": result.content,
                        "is_error": result.is_error,
                    }
                )

                # Write a crash-safe checkpoint after every tool call.
                await self._write_checkpoint(
                    user_input=user_input,
                    partial_response=final_response,
                    last_tool_call=tool_call,
                    last_tool_result=result,
                    interrupted_by=None,
                )

                # NIU-537: increment tool counter and check cadence/budget triggers.
                self._tool_call_count += 1
                await self._check_auto_snapshot_triggers(tool_call.name)

            # Append tool results as a user message.
            self._session.messages.append(Message(role="user", content=tool_results_content))
        else:
            await self._write_checkpoint(
                user_input=user_input,
                partial_response=final_response,
                last_tool_call=turn_tool_calls[-1] if turn_tool_calls else None,
                last_tool_result=turn_tool_results[-1] if turn_tool_results else None,
                interrupted_by=InterruptReason.BUDGET_EXHAUSTED,
            )
            raise MaxIterationsError(self._max_iterations)

        self._session.record_turn(cumulative_usage)
        duration_seconds = time.monotonic() - start_time

        # NIU-594: build a partial result to pass into episode extraction
        partial_result = TurnResult(
            response=final_response,
            tool_calls=turn_tool_calls,
            tool_results=turn_tool_results,
            usage=cumulative_usage,
        )

        # NIU-594: parse ---outcome--- block from final response when persona declares a schema
        parsed_outcome = _parse_outcome_block_for_persona(final_response, self._persona_config)
        if parsed_outcome is not None:
            parsed_outcome = await _validate_mimir_outcome_for_persona(
                parsed_outcome,
                persona_config=self._persona_config,
                mimir=self._mimir,
            )
            logger.debug(
                "final_outcome_block: valid=%s fields=%s errors=%s",
                parsed_outcome.valid,
                parsed_outcome.fields,
                parsed_outcome.errors,
            )

        # Extract episode (always, so TurnResult.episode is populated for outcome capture)
        recorded_episode: Episode | None = None
        try:
            episode = _extract_episode(
                session_id=str(self._session.id),
                user_input=user_input,
                turn_result=partial_result,
                summary_max_chars=self._episode_summary_max_chars,
                task_max_chars=self._episode_task_max_chars,
            )
            # Attach structured outcome to episode before enrichment/recording
            if parsed_outcome is not None:
                episode.structured_outcome = parsed_outcome.fields
                episode.outcome_valid = parsed_outcome.valid

            if self._memory is not None:
                episode = await self._enrich_episode(
                    episode=episode,
                    turn_result=partial_result,
                    duration_seconds=duration_seconds,
                    past_context=memory_ctx,
                )
                await self._memory.record_episode(episode)
            recorded_episode = episode
        except Exception:
            logger.warning("Episode extraction/recording failed; continuing.", exc_info=True)

        if self._memory is not None:
            try:
                await self._memory.on_turn_complete(
                    session_id=str(self._session.id),
                    user_input=user_input,
                    response_summary=final_response,
                )
            except Exception:
                logger.warning("Memory on_turn_complete failed; continuing.", exc_info=True)

        # NIU-594: emit ravn.session.ended enriched with structured outcome
        if parsed_outcome is not None:
            await self._emit_session_ended_with_outcome(parsed_outcome)

        return TurnResult(
            response=final_response,
            tool_calls=turn_tool_calls,
            tool_results=turn_tool_results,
            usage=cumulative_usage,
            episode=recorded_episode,
        )

    async def _build_effective_system(self, user_input: str) -> SystemPrompt:
        """Build the effective system prompt for this turn.

        When a PromptBuilder is configured, it handles memory context as a
        section and returns Anthropic-format blocks.  Otherwise falls back to
        the legacy string concatenation approach.
        """
        if self._prompt_builder is not None:
            if self._memory is not None:
                try:
                    memory_ctx = await self._memory.prefetch(user_input)
                    self._prompt_builder.set_memory_context(memory_ctx or "")
                except Exception:
                    logger.warning(
                        "Memory prefetch failed; continuing without context.", exc_info=True
                    )
            return self._prompt_builder.render_blocks()

        # Legacy: plain-string system prompt with optional memory suffix.
        effective: str = self._system_prompt
        if self._memory is not None:
            try:
                memory_ctx = await self._memory.prefetch(user_input)
                if memory_ctx:
                    effective = f"{self._system_prompt}\n\n{memory_ctx}"
            except Exception:
                logger.warning("Memory prefetch failed; continuing without context.", exc_info=True)
        return effective

    def _prompt_section_estimates(
        self,
        effective_system: SystemPrompt,
        messages: list[Message],
    ) -> dict[str, int]:
        """Estimate this call's prompt tokens, attributed per section.

        Sections: the tool schemas sent with every request, each system-prompt
        section (per PromptBuilder section when one is configured, flat
        otherwise), and the message history. This is the prompt-composition
        audit NIU-1118 asks for — the data that shows WHERE an oversized
        drive-loop prompt comes from.
        """
        rough_sections: dict[str, int] = {}
        tool_defs = self._tool_defs()
        rough_sections["tool_schemas"] = TokenEstimator.rough_structured(tool_defs)
        if self._prompt_builder is not None:
            for name, text in self._prompt_builder.section_texts().items():
                rough_sections[f"system:{name}"] = TokenEstimator.rough(text)
        elif isinstance(effective_system, str):
            rough_sections["system"] = TokenEstimator.rough(effective_system)
        else:
            rough_sections["system"] = TokenEstimator.rough_blocks(effective_system)
        rough_sections["history"] = TokenEstimator.rough_messages(messages)
        return {
            name: TokenEstimator.conservative(
                tokens,
                self._token_estimate_safety_factor,
            )
            for name, tokens in rough_sections.items()
        }

    def _log_prompt_composition(self, sections: dict[str, int]) -> None:
        total = sum(sections.values())
        breakdown = ", ".join(f"{name}≈{count}" for name, count in sections.items())
        logger.info(
            "prompt_composition: total≈%d tokens (%s; tools=%d, messages=%d)",
            total,
            breakdown,
            len(self._tools),
            len(self._session.messages),
        )

    def _record_prompt_budget(
        self,
        sections: dict[str, int],
        *,
        compressed: bool,
    ) -> None:
        estimated = sum(sections.values())
        status: dict[str, int | float | bool] = {
            "estimated_prompt_tokens": estimated,
            "prompt_budget_tokens": self._max_prompt_tokens,
            "output_reserve_tokens": self._max_tokens,
            "context_window_tokens": self._context_window_tokens,
            "token_estimate_safety_factor": self._token_estimate_safety_factor,
            "compressed": compressed,
        }
        self._last_prompt_budget_status = status
        attributes = {
            "ravn.prompt.estimated_tokens": estimated,
            "ravn.prompt.budget_tokens": self._max_prompt_tokens,
            "ravn.prompt.output_reserve_tokens": self._max_tokens,
            "ravn.prompt.context_window_tokens": self._context_window_tokens,
            "ravn.prompt.estimate_safety_factor": self._token_estimate_safety_factor,
            "ravn.prompt.compressed": compressed,
        }
        telemetry = get_observability()
        telemetry.set_attributes(attributes)
        telemetry.event("ravn.prompt.budget", attributes=attributes, content=sections)
        telemetry.gauge(
            "ravn.prompt.estimated_tokens",
            estimated,
            attributes={
                "gen_ai.request.model": self._model,
                "gen_ai.agent.name": self._persona or "ravn",
            },
            description="Conservative preflight estimate of prompt tokens.",
        )

    def _enforce_prompt_budget(self, sections: dict[str, int]) -> None:
        """Refuse an LLM call whose estimated prompt exceeds the hard budget.

        Only active when ``max_prompt_tokens`` is configured (> 0). Runs after
        context compression had its chance, so hitting it means a genuinely
        oversized prompt — fail loudly with the per-section breakdown instead
        of letting the provider reject (or silently accept) an overflowing
        request.
        """
        if self._max_prompt_tokens <= 0:
            return
        total = sum(sections.values())
        if total <= self._max_prompt_tokens:
            return
        error = PromptBudgetExceededError(
            estimated_tokens=total,
            budget_tokens=self._max_prompt_tokens,
            sections=sections,
        )
        logger.error("%s", error)
        raise error

    def _truncate_oversized_tool_result(
        self,
        tool_call: ToolCall,
        result: ToolResult,
    ) -> ToolResult:
        """Bound a single tool result's contribution to the conversation.

        Observed in production (NIU-1118): one mimir tool result injected
        ~229MB into history; compression protects the most recent messages,
        so nothing downstream could shrink it and the turn died on the prompt
        budget. Truncate here with an explicit marker so the model knows the
        result is partial and the turn can continue.
        """
        limit = self._max_tool_result_chars
        content = result.content
        if limit <= 0 or not isinstance(content, str) or len(content) <= limit:
            return result
        dropped = len(content) - limit
        logger.warning(
            "tool result for %r truncated: %d of %d chars dropped (max_tool_result_chars=%d)",
            tool_call.name,
            dropped,
            len(content),
            limit,
        )
        marker = (
            f"\n\n[tool result truncated: {dropped} characters beyond the "
            f"{limit}-character limit were dropped — narrow the query or "
            "paginate instead of repeating the same call]"
        )
        return ToolResult(
            tool_call_id=result.tool_call_id,
            content=content[:limit] + marker,
            is_error=result.is_error,
        )

    async def _maybe_compress(
        self,
        effective_system: SystemPrompt,
        *,
        memory_summary: str = "",
    ) -> list[Message]:
        """Return (possibly compressed) session messages.

        When no compressor is configured, the session messages are returned
        unchanged.  Compression results are stored in
        ``self._last_compression_result``.

        Parameters
        ----------
        effective_system:
            The rendered system prompt used to estimate token overhead.
        memory_summary:
            The episodic memory context string fetched for this turn.  Passed
            to the compactor as an anchor so the structured state document can
            reference relevant past context.
        """
        if self._compressor is None:
            return self._session.messages

        system_tokens = (
            TokenEstimator.rough_blocks(effective_system)
            if isinstance(effective_system, list)
            else TokenEstimator.rough(effective_system)
        )
        tool_tokens = TokenEstimator.rough_structured(self._tool_defs())
        messages, result = await self._compressor.maybe_compress(
            self._session.messages,
            system_tokens=system_tokens,
            tool_tokens=tool_tokens,
            prompt_budget_tokens=self._max_prompt_tokens,
            todos=self._session.todos or None,
            memory_summary=memory_summary or None,
        )
        if result.was_compressed:
            self._session.messages.clear()
            self._session.messages.extend(messages)
            self._last_compression_result = result
            logger.info(
                "Context compressed: %d → %d messages (%d pass(es), %d removed)",
                result.original_count,
                result.final_count,
                result.compression_count,
                result.removed_message_count,
            )
        return messages

    async def _maybe_save_snapshot(self, *, label: str = "") -> None:
        """Save a named snapshot if a checkpoint port is configured.

        Failures are logged and never propagated — losing a snapshot is
        preferable to crashing the task.
        """
        if self._checkpoint_port is None:
            return

        from datetime import UTC, datetime

        from ravn.domain.checkpoint import Checkpoint

        messages = [message.to_api_dict() for message in self._session.messages]
        todos: list[dict] = [
            {"id": t.id, "content": t.content, "status": str(t.status), "priority": t.priority}
            for t in self._session.todos
        ]
        consumed = self._iteration_budget.consumed if self._iteration_budget is not None else 0
        total = self._iteration_budget.total if self._iteration_budget is not None else 0

        checkpoint = Checkpoint(
            task_id=self._task_id,
            user_input="",
            messages=messages,
            todos=todos,
            iteration_budget_consumed=consumed,
            iteration_budget_total=total,
            last_tool_call=None,
            last_tool_result=None,
            partial_response="",
            interrupted_by=None,
            created_at=datetime.now(UTC),
            label=label,
        )
        try:
            await self._checkpoint_port.save_snapshot(checkpoint)
            logger.debug("Named snapshot saved for task %r (label=%r)", self._task_id, label)
        except Exception as exc:
            logger.warning("Named snapshot save failed for task %r: %s", self._task_id, exc)

    async def _check_auto_snapshot_triggers(self, tool_name: str) -> None:
        """Evaluate and fire automatic named-snapshot triggers.

        Called after each tool call completes.  Checks:
        1. N-tools cadence trigger (checkpoint_every_n_tools)
        2. Budget milestone trigger (budget_milestone_fractions)
        """
        if self._checkpoint_port is None:
            return

        # N-tools cadence
        if self._checkpoint_every_n_tools > 0:
            if self._tool_call_count % self._checkpoint_every_n_tools == 0:
                await self._maybe_save_snapshot(label=f"auto: after {self._tool_call_count} tools")
                return  # Only one trigger per call

        # Budget milestone
        if self._budget_milestones and self._iteration_budget is not None:
            total = self._iteration_budget.total
            if total > 0:
                fraction = self._iteration_budget.consumed / total
                for milestone in sorted(self._budget_milestones):
                    if fraction >= milestone and milestone not in self._fired_milestones:
                        self._fired_milestones.add(milestone)
                        pct = int(milestone * 100)
                        await self._maybe_save_snapshot(label=f"auto: {pct}% budget consumed")
                        break  # One milestone per call

    async def _write_checkpoint(
        self,
        *,
        user_input: str,
        partial_response: str,
        last_tool_call: ToolCall | None,
        last_tool_result: ToolResult | None,
        interrupted_by: InterruptReason | None,
    ) -> None:
        """Persist a checkpoint for the current session state.

        No-ops when no checkpoint port is configured.  Failures are
        logged at WARNING level and never propagate — losing a checkpoint
        is preferable to crashing the task.
        """
        if self._checkpoint_port is None:
            return

        # Serialise messages to plain dicts for storage.
        messages = [message.to_api_dict() for message in self._session.messages]
        todos: list[dict] = [
            {"id": t.id, "content": t.content, "status": str(t.status), "priority": t.priority}
            for t in self._session.todos
        ]

        consumed = self._iteration_budget.consumed if self._iteration_budget is not None else 0
        total = self._iteration_budget.total if self._iteration_budget is not None else 0

        last_call_dict: dict | None = None
        if last_tool_call is not None:
            last_call_dict = {
                "id": last_tool_call.id,
                "name": last_tool_call.name,
                "input": last_tool_call.input,
            }

        last_result_dict: dict | None = None
        if last_tool_result is not None:
            last_result_dict = {
                "tool_call_id": last_tool_result.tool_call_id,
                "content": last_tool_result.content,
                "is_error": last_tool_result.is_error,
            }

        checkpoint = Checkpoint(
            task_id=self._task_id,
            user_input=user_input,
            messages=messages,
            todos=todos,
            iteration_budget_consumed=consumed,
            iteration_budget_total=total,
            last_tool_call=last_call_dict,
            last_tool_result=last_result_dict,
            partial_response=partial_response,
            interrupted_by=interrupted_by,
        )

        try:
            await self._checkpoint_port.save(checkpoint)
        except Exception as exc:
            logger.warning("Checkpoint save failed for task %r: %s", self._task_id, exc)

    async def _enrich_episode(
        self,
        episode: Episode,
        turn_result: TurnResult,
        duration_seconds: float,
        past_context: str,
    ) -> Episode:
        """Populate cost, errors, and reflection fields on an episode in-place.

        Generates a compact LLM reflection when a reflection model is configured.
        Returns the enriched episode (same object, mutated).
        """
        errors = [r.content for r in turn_result.tool_results if r.is_error]
        cost_usd = _compute_cost_usd(
            turn_result.usage.input_tokens,
            turn_result.usage.output_tokens,
            self._input_token_cost_per_million,
            self._output_token_cost_per_million,
        )

        reflection = await self._run_reflection(
            task_summary=episode.task_description,
            outcome=episode.outcome,
            tools_used=episode.tools_used,
            errors=errors,
            past_context=past_context,
        )

        episode.errors = errors
        episode.cost_usd = cost_usd
        episode.duration_seconds = duration_seconds
        episode.reflection = reflection
        return episode

    async def _run_reflection(
        self,
        task_summary: str,
        outcome: Outcome,
        tools_used: list[str],
        errors: list[str],
        past_context: str,
    ) -> str:
        """Call the fast LLM to generate a compact post-task reflection."""
        if self._reflection_model.strip().lower() in {"off", "disabled", "none"}:
            return ""
        tools_str = ", ".join(tools_used) if tools_used else "none"
        errors_str = "; ".join(errors[:5]) if errors else "none"
        past_str = past_context[:800] if past_context else "none"

        prompt = (
            f"Task: {task_summary}\n"
            f"Outcome: {outcome}\n"
            f"Tools used: {tools_str}\n"
            f"Errors: {errors_str}\n"
            f"\nPast context:\n{past_str}\n"
            "\nIn 3-5 sentences, briefly reflect:\n"
            "1. What went well?\n"
            "2. What would you do differently?\n"
            "3. What patterns from previous episodes are confirmed or contradicted?"
        )

        try:
            response = await self._llm.generate(
                [{"role": "user", "content": prompt}],
                tools=[],
                system=(
                    "You are Ravn reflecting on a completed task. "
                    "Be concise, factual, and specific. No preamble."
                ),
                model=self._reflection_model,
                max_tokens=self._reflection_max_tokens,
            )
            return response.content
        except Exception as exc:
            logger.warning("Reflection LLM call failed: %s", exc)
            return f"Reflection unavailable: {exc}"

    def _resolve_thinking(
        self,
        *,
        iteration: int,
        explicit: bool,
        last_had_tool_error: bool,
    ) -> dict | None:
        """Return the thinking parameter dict for this LLM call, or None.

        Extended thinking is activated when:
        - Explicitly requested (``think:`` prefix / ``--think`` flag).
        - Auto-trigger is on for the persona.
        - Auto-trigger-on-retry is on AND a tool failed on the previous iteration.
        """
        et = self._extended_thinking
        if et is None or not et.enabled:
            return None

        if explicit:
            return {"type": "enabled", "budget_tokens": et.budget_tokens}

        if et.auto_trigger_on_retry and iteration > 0 and last_had_tool_error:
            return {"type": "enabled", "budget_tokens": et.budget_tokens}

        if et.auto_trigger:
            return {"type": "enabled", "budget_tokens": et.budget_tokens}

        return None

    async def _call_llm_streaming(
        self,
        system_prompt: SystemPrompt | None = None,
        messages: list[Message] | None = None,
        thinking: dict | None = None,
    ) -> LLMResponse:
        telemetry = get_observability()
        attributes = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": type(self._llm).__name__,
            "gen_ai.request.model": self._model,
            "gen_ai.conversation.id": str(self._session.id),
            "gen_ai.agent.name": self._persona or "ravn",
            "gen_ai.agent.id": self._source_id,
            "ravn.task.id": self._task_id,
            "ravn.agent.iteration": self._trace_iteration,
        }
        metric_attributes = {
            key: attributes[key]
            for key in (
                "gen_ai.operation.name",
                "gen_ai.provider.name",
                "gen_ai.request.model",
                "gen_ai.agent.name",
            )
        }
        started = time.monotonic()
        with telemetry.span(f"chat {self._model}", attributes=attributes) as span:
            telemetry.event(
                "gen_ai.request",
                attributes={"gen_ai.request.message_count": len(messages or [])},
                content={
                    "system_prompt": system_prompt,
                    "messages": [
                        {"role": str(message.role), "content": message.content}
                        for message in (messages or [])
                    ],
                    "thinking": thinking,
                },
            )
            try:
                response = await self._call_llm_streaming_observed(
                    system_prompt=system_prompt,
                    messages=messages,
                    thinking=thinking,
                )
            except Exception:
                telemetry.count(
                    "ravn.agent.llm.calls",
                    attributes={**metric_attributes, "error.type": "exception"},
                )
                telemetry.duration(
                    "gen_ai.client.operation.duration",
                    time.monotonic() - started,
                    attributes={**metric_attributes, "error.type": "exception"},
                )
                raise
            duration = time.monotonic() - started
            span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens)
            span.set_attribute("ravn.llm.tool_call_count", len(response.tool_calls))
            span.set_attribute("ravn.llm.stop_reason", str(response.stop_reason))
            telemetry.event(
                "gen_ai.response",
                attributes={
                    "gen_ai.response.stop_reason": str(response.stop_reason),
                    "gen_ai.response.tool_call_count": len(response.tool_calls),
                    "gen_ai.response.tool_names": [call.name for call in response.tool_calls],
                },
                content={
                    "content": response.content,
                    "reasoning": response.reasoning,
                    "tool_calls": [
                        {"id": call.id, "name": call.name, "input": call.input}
                        for call in response.tool_calls
                    ],
                },
            )
            telemetry.count("ravn.agent.llm.calls", attributes=metric_attributes)
            telemetry.duration(
                "gen_ai.client.operation.duration",
                duration,
                attributes=metric_attributes,
                description="Duration of a generative AI operation.",
            )
            for token_type, value in (
                ("input", response.usage.input_tokens),
                ("output", response.usage.output_tokens),
            ):
                telemetry.record(
                    "gen_ai.client.token.usage",
                    value,
                    unit="{token}",
                    attributes={**metric_attributes, "gen_ai.token.type": token_type},
                    description="Number of input and output tokens used.",
                )
            return response

    async def _call_llm_streaming_observed(
        self,
        system_prompt: SystemPrompt | None = None,
        messages: list[Message] | None = None,
        thinking: dict | None = None,
    ) -> LLMResponse:
        """Call the LLM with streaming and accumulate into an LLMResponse."""
        accumulated_text = ""
        accumulated_reasoning = ""
        tool_calls: list[ToolCall] = []
        final_usage = TokenUsage(input_tokens=0, output_tokens=0)
        stop_reason = StopReason.END_TURN
        effective: SystemPrompt = (
            system_prompt if system_prompt is not None else self._system_prompt
        )
        api_messages = (
            [message.to_api_dict() for message in messages]
            if messages is not None
            else self._build_api_messages()
        )

        async for event in self._llm.stream(
            api_messages,
            tools=self._tool_defs(),
            system=effective,
            model=self._model,
            max_tokens=self._max_tokens,
            thinking=thinking,
        ):
            match event.type:
                case StreamEventType.TEXT_DELTA:
                    if event.text:
                        accumulated_text += event.text
                        await self._channel.emit(
                            RavnEvent.thought(
                                self._source_id,
                                event.text,
                                self._session.id,
                                self._session.id,
                            )
                        )
                case StreamEventType.THINKING:
                    if event.text:
                        accumulated_reasoning += event.text
                        await self._channel.emit(
                            RavnEvent.thinking(
                                self._source_id,
                                event.text,
                                self._session.id,
                                self._session.id,
                            )
                        )
                case StreamEventType.TOOL_CALL:
                    if event.tool_call:
                        tool_calls.append(event.tool_call)
                case StreamEventType.MESSAGE_DONE:
                    if event.usage:
                        final_usage = event.usage

        # Decided after the stream rather than inside MESSAGE_DONE, because
        # MESSAGE_DONE is not the last event. Servers that send a usage chunk
        # (stream_options.include_usage) emit it mid-stream, so a tool call
        # recovered at end of stream arrived after the only place that set
        # TOOL_USE — and the turn ended holding a tool call it never ran.
        # Whether the model asked for tools does not depend on event order.
        if tool_calls:
            stop_reason = StopReason.TOOL_USE

        return LLMResponse(
            content=accumulated_text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=final_usage,
            reasoning=accumulated_reasoning,
        )

    async def _execute_tool(self, tool_call: ToolCall) -> ToolResult:
        from ravn.tool_observability import execute_observed_tool

        return await execute_observed_tool(
            name=tool_call.name,
            arguments=tool_call.input,
            execute=lambda: self._execute_tool_observed(tool_call),
            call_id=tool_call.id,
            agent_name=self._persona or "ravn",
            conversation_id=str(self._session.id),
            task_id=self._task_id,
            iteration=self._trace_iteration,
        )

    async def _execute_tool_observed(self, tool_call: ToolCall) -> ToolResult:
        """Execute a single tool call, enforcing permissions and running hooks.

        The ``ask_user`` tool is intercepted before regular dispatch and
        handled by ``_intercept_ask_user`` regardless of whether it is
        present in the tools registry.
        """
        if tool_call.name == _ASK_USER_TOOL_NAME:
            return await self._intercept_ask_user(tool_call)

        tool = self._tools.get(tool_call.name)

        if tool is None:
            result = ToolResult(
                tool_call_id=tool_call.id,
                content=f"Unknown tool: {tool_call.name}",
                is_error=True,
            )
            await self._channel.emit(
                RavnEvent.tool_result(
                    self._source_id,
                    tool_call.name,
                    result.content,
                    self._session.id,
                    self._session.id,
                    is_error=True,
                )
            )
            return result

        diff = tool.diff_preview(tool_call.input)
        await self._channel.emit(
            RavnEvent.tool_start(
                self._source_id,
                tool_call.name,
                tool_call.input,
                self._session.id,
                self._session.id,
                diff=diff,
            )
        )

        granted = await self._permission.check(tool.required_permission)
        if not granted:
            error = PermissionDeniedError(tool_call.name, tool.required_permission)
            result = ToolResult(
                tool_call_id=tool_call.id,
                content=str(error),
                is_error=True,
            )
            await self._channel.emit(
                RavnEvent.tool_result(
                    self._source_id,
                    tool_call.name,
                    result.content,
                    self._session.id,
                    self._session.id,
                    is_error=True,
                )
            )
            return result

        for hook in self._pre_tool_hooks:
            try:
                await hook(tool_call)
            except Exception as exc:
                logger.warning("Pre-tool hook failed for '%s': %s", tool_call.name, exc)

        try:
            result = await tool.execute(tool_call.input)
        except Exception as exc:
            logger.warning("Tool '%s' raised: %s", tool_call.name, exc)
            result = ToolResult(
                tool_call_id=tool_call.id,
                content=f"Tool error: {exc}",
                is_error=True,
            )

        for hook in self._post_tool_hooks:
            try:
                await hook(tool_call, result)
            except Exception as exc:
                logger.warning("Post-tool hook failed for '%s': %s", tool_call.name, exc)

        await self._channel.emit(
            RavnEvent.tool_result(
                self._source_id,
                tool_call.name,
                result.content,
                self._session.id,
                self._session.id,
                is_error=result.is_error,
            )
        )
        return result

    async def _intercept_ask_user(self, tool_call: ToolCall) -> ToolResult:
        """Handle an ask_user tool call by collecting input from the user.

        Emits a TOOL_START event so channels can render the question, then
        calls ``user_input_fn`` if configured.  Returns an error result when
        no input function is available.
        """
        question = tool_call.input.get("question", "")
        await self._channel.emit(
            RavnEvent.tool_start(
                self._source_id,
                _ASK_USER_TOOL_NAME,
                tool_call.input,
                self._session.id,
                self._session.id,
            )
        )

        if self._user_input_fn is None:
            result = ToolResult(
                tool_call_id=tool_call.id,
                content="ask_user is not available in this session (no user_input_fn configured)",
                is_error=True,
            )
            await self._channel.emit(
                RavnEvent.tool_result(
                    self._source_id,
                    _ASK_USER_TOOL_NAME,
                    result.content,
                    self._session.id,
                    self._session.id,
                    is_error=True,
                )
            )
            return result

        answer = await self._user_input_fn(question)
        result = ToolResult(tool_call_id=tool_call.id, content=answer)
        await self._channel.emit(
            RavnEvent.tool_result(
                self._source_id,
                _ASK_USER_TOOL_NAME,
                answer,
                self._session.id,
                self._session.id,
            )
        )
        return result


def _maybe_append_budget_warning(
    result: ToolResult,
    budget: IterationBudget | None,
) -> ToolResult:
    """Return a new ToolResult with a budget warning appended when near limit.

    Budget warnings are injected into tool result content (not separate
    messages) so the model is informed without disrupting conversation flow.
    """
    if budget is None:
        return result
    suffix = budget.warning_suffix()
    if suffix is None:
        return result
    return ToolResult(
        tool_call_id=result.tool_call_id,
        content=result.content + suffix,
        is_error=result.is_error,
    )


_TAG_MAP: dict[str, list[str]] = {
    "file": ["file_operations"],
    "write_file": ["file_operations"],
    "edit_file": ["file_operations"],
    "read_file": ["file_operations"],
    "search_files": ["file_operations"],
    "git": ["git"],
    "bash": ["shell"],
    "terminal": ["shell"],
    "web_search": ["web"],
    "web_fetch": ["web"],
    "session_search": ["memory"],
    "todo": ["task_management"],
}


def _infer_tags(tool_names: list[str]) -> list[str]:
    """Heuristically infer episode tags from the tools that were used."""
    tags: list[str] = []
    for name in tool_names:
        for key, tag_list in _TAG_MAP.items():
            if key in name.lower():
                for t in tag_list:
                    if t not in tags:
                        tags.append(t)
    if not tags:
        tags.append("general")
    return tags


def _determine_outcome(tool_results: list[ToolResult]) -> Outcome:
    """Determine episode outcome from tool results."""
    if not tool_results:
        return Outcome.SUCCESS
    errors = [r for r in tool_results if r.is_error]
    if len(errors) == len(tool_results):
        return Outcome.FAILURE
    if errors:
        return Outcome.PARTIAL
    return Outcome.SUCCESS


def _extract_episode(
    session_id: str,
    user_input: str,
    turn_result: TurnResult,
    *,
    summary_max_chars: int = 500,
    task_max_chars: int = 200,
) -> Episode:
    """Derive an Episode from a completed agent turn.

    Uses heuristics for tagging and outcome determination.  Embedding
    generation is deferred to Phase 3.5.
    """
    tools_used = list({tc.name for tc in turn_result.tool_calls})
    outcome = _determine_outcome(turn_result.tool_results)
    tags = _infer_tags(tools_used)

    summary = turn_result.response[:summary_max_chars]
    if len(turn_result.response) > summary_max_chars:
        summary = summary.rstrip() + "…"
    if not summary:
        summary = f"Completed task with {len(tools_used)} tool(s) used."

    task_description = user_input[:task_max_chars]
    if len(user_input) > task_max_chars:
        task_description = task_description.rstrip() + "…"

    return Episode(
        episode_id=str(uuid.uuid4()),
        session_id=session_id,
        timestamp=datetime.now(UTC),
        summary=summary,
        task_description=task_description,
        tools_used=tools_used,
        outcome=outcome,
        tags=tags,
        embedding=None,
    )


def _parse_outcome_block_for_persona(
    text: str,
    persona_config: PersonaConfig | None,
) -> ParsedOutcome | None:
    """Parse the ---outcome--- block from *text* using the persona's declared schema.

    Returns a :class:`niuu.domain.outcome.ParsedOutcome` when an outcome block is
    found, or ``None`` when no persona schema is configured or no block is present.
    """
    if persona_config is None:
        return None
    produces = persona_config.produces
    if produces is None:
        return None
    schema_fields = produces.schema
    if not schema_fields:
        return None
    try:
        from niuu.domain.outcome import OutcomeSchema, parse_outcome_block
        from ravn.domain.valkyrie_contracts import (  # noqa: PLC0415
            VALKYRIE_RUNTIME_OWNED_FIELDS,
            is_valkyrie_outcome_event,
        )

        if is_valkyrie_outcome_event(produces.event_type):
            schema_fields = {
                name: field
                for name, field in schema_fields.items()
                if name not in VALKYRIE_RUNTIME_OWNED_FIELDS
            }
        schema = OutcomeSchema(fields=schema_fields)
        return parse_outcome_block(text, schema)
    except Exception:
        logger.warning("Outcome block parsing failed; continuing without.", exc_info=True)
        return None


async def _validate_mimir_outcome_for_persona(
    parsed_outcome: ParsedOutcome,
    *,
    persona_config: PersonaConfig | None,
    mimir: MimirPort | None,
) -> ParsedOutcome:
    """Apply Mimir-backed outcome validation declared by persona schemas.

    Any successful outcome with an ``artifact_path`` must resolve to a real
    durable Mimir page. Research outcomes additionally enforce provenance: the
    page must exist, be marked
    ``produced_by_thread: true``, reference non-empty ``source_ids``, and those
    source IDs must resolve to ingested raw sources that are not merely the
    final page content copied back into ``raw/``.
    """
    if persona_config is None:
        return parsed_outcome
    errors: list[str] = []
    artifact_path = str(parsed_outcome.fields.get("artifact_path") or "").strip()
    verdict = str(parsed_outcome.fields.get("verdict") or "").strip().lower()
    artifact_succeeded = "artifact_path" in persona_config.produces.schema and verdict not in {
        "blocked",
        "fail",
        "failed",
    }
    if artifact_succeeded:
        if not artifact_path:
            errors.append("successful artifact outcome requires a non-empty artifact_path")
        elif mimir is None:
            errors.append(
                f"artifact {artifact_path} cannot be verified because Mimir is not configured"
            )
        else:
            try:
                await mimir.get_page(artifact_path)
            except FileNotFoundError:
                errors.append(f"artifact not found in Mimir: {artifact_path}")

    produces_research_completed = persona_config.produces.event_type == "research.completed" or (
        "research.completed" in set(persona_config.produces.event_type_map.values())
    )
    if not produces_research_completed:
        if errors:
            parsed_outcome.valid = False
            parsed_outcome.errors.extend(errors)
        return parsed_outcome
    if mimir is None:
        parsed_outcome.valid = False
        parsed_outcome.errors.append("research.completed outcome requires Mimir")
        return parsed_outcome

    page_path = str(parsed_outcome.fields.get("page_path") or "").strip()
    if not page_path:
        errors.append("research.completed outcome requires a non-empty page_path")
    else:
        try:
            page = await mimir.get_page(page_path)
        except FileNotFoundError:
            errors.append(f"research page not found in Mimir: {page_path}")
        else:
            if not page.meta.produced_by_thread:
                errors.append(f"research page {page_path} must include produced_by_thread: true")

            source_ids = [
                stripped_id
                for source_id in page.meta.source_ids
                if (stripped_id := str(source_id).strip())
            ]
            if not source_ids:
                errors.append(
                    f"research page {page_path} has no source_ids provenance; ingest the "
                    "actual sources and reference them in frontmatter"
                )
            else:
                resolved_sources = []
                missing_source_ids = []
                for source_id in source_ids:
                    source = await mimir.read_source(source_id)
                    if source is None:
                        missing_source_ids.append(source_id)
                        continue
                    resolved_sources.append(source)

                if missing_source_ids:
                    errors.append(
                        f"research page {page_path} references missing source_ids: "
                        + ", ".join(missing_source_ids)
                    )

                stripped_content = page.content.strip()
                if resolved_sources and all(
                    source.content.strip() == stripped_content for source in resolved_sources
                ):
                    errors.append(
                        f"research page {page_path} only references self-ingested page content; "
                        "ingest the material sources you actually used"
                    )

    if errors:
        parsed_outcome.valid = False
        parsed_outcome.errors.extend(errors)
    return parsed_outcome


_THINK_PREFIXES = ("think:", "think: ")

# Matches --think only as a standalone flag (word boundary on both sides).
# Captures any surrounding whitespace so collapsing leaves a single space.
_THINK_FLAG_RE = re.compile(r"(?<!\S)--think(?!\S)")


def _parse_think_flag(user_input: str) -> tuple[bool, str]:
    """Return (explicit_thinking, cleaned_input).

    Strips ``think:`` prefix or ``--think`` standalone flag from user input
    and returns a bool indicating whether explicit thinking was requested.

    ``--think`` is only recognised as a standalone flag; ``--thinking`` and
    other ``--think``-prefixed words are left untouched.
    """
    stripped = user_input
    for prefix in _THINK_PREFIXES:
        if stripped.lower().startswith(prefix):
            return True, stripped[len(prefix) :].lstrip()
    if _THINK_FLAG_RE.search(stripped):
        cleaned = _THINK_FLAG_RE.sub(" ", stripped).strip()
        # Collapse any run of spaces introduced by the substitution.
        cleaned = re.sub(r" {2,}", " ", cleaned)
        return True, cleaned
    return False, stripped


def _build_assistant_content(response: LLMResponse) -> list[dict]:
    """Build the Anthropic-format assistant content list from an LLMResponse."""
    content: list[dict] = []

    if response.content:
        content.append({"type": "text", "text": response.content})

    for tc in response.tool_calls:
        content.append(
            {
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            }
        )

    return content
