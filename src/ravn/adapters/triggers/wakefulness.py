"""WakefulnessTrigger — detect silence, reflect, emit follow-up intents.

When the operator stops typing for ``silence_threshold_seconds``, this trigger
runs a cheap LLM reflection on the conversation and emits 0–N follow-up intents
as threads into Mímir via ``create_thread()``.

Build order step 3 from the Vaka vision (§4.1, §12).

Two reflection passes
---------------------
1. **Shallow** — fires after ``silence_threshold_seconds`` of silence, capped at
   one per ``reflection_cooldown_seconds``.
2. **Deep** — fires after ``deep_reflection_threshold_seconds`` of continued
   silence, at most once per ``deep_reflection_cooldown_seconds``.  Uses a
   broader context prompt.

State persistence
-----------------
``last_reflection_at`` and ``last_deep_reflection_at`` are persisted to
``~/.ravn/daemon/wakefulness_state.json`` so timestamps survive daemon restarts.

Implements :class:`~ravn.ports.trigger.TriggerPort`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from niuu.ports.mimir import MimirPort
from ravn.config import WakefulnessConfig
from ravn.domain.interaction_tracker import LastInteractionTracker
from ravn.domain.models import AgentTask
from ravn.ports.llm import LLMPort
from ravn.ports.trigger import TriggerPort

logger = logging.getLogger(__name__)

_STATE_FILE_NAME = "wakefulness_state.json"

_REFLECTION_MAX_TOKENS = 512

_SHALLOW_PROMPT = """\
You are reflecting on a conversation between an AI assistant and its operator.
The operator has been silent for a while.

Answer two questions:
1. What is unfinished from this conversation that is worth pursuing?
2. Is any of it actionable right now?

Respond with a JSON array of 0–{max_intents} intent objects. Each object has:
{{
  "title": "short title of the intent",
  "why": "one sentence explaining why this matters",
  "next_action_hint": "concrete next step",
  "budget_hint": "small | medium | large",
  "surface_when": "on_return | background | never"
}}

If nothing is unfinished or actionable, return an empty array: []
Respond ONLY with valid JSON — no markdown fences, no commentary.\
"""

_DEEP_PROMPT = """\
You are performing a deep reflection on a long-silent conversation between an AI
assistant and its operator.

Consider the broader context: recurring themes, long-term goals mentioned, and
any patterns in what the operator cares about.

Answer three questions:
1. What threads of thought from this conversation deserve sustained attention?
2. Are there connections to earlier topics that the operator might not have noticed?
3. What could the assistant proactively prepare for the operator's return?

Respond with a JSON array of 0–{max_intents} intent objects. Each object has:
{{
  "title": "short title of the intent",
  "why": "one sentence explaining why this matters",
  "next_action_hint": "concrete next step",
  "budget_hint": "small | medium | large",
  "surface_when": "on_return | background | never"
}}

If nothing warrants attention, return an empty array: []
Respond ONLY with valid JSON — no markdown fences, no commentary.\
"""


class WakefulnessTrigger(TriggerPort):
    """TriggerPort that detects operator silence and emits follow-up intents.

    Args:
        tracker:    Shared interaction tracker — ``touch()`` called by CLI.
        mimir:      Mímir adapter for creating threads.
        llm:        LLM adapter for the reflection call.
        config:     Wakefulness configuration.
        state_dir:  Directory for persisting reflection timestamps.
                    Defaults to ``~/.ravn/daemon``.
    """

    def __init__(
        self,
        tracker: LastInteractionTracker,
        mimir: MimirPort,
        llm: LLMPort,
        config: WakefulnessConfig,
        state_dir: Path | None = None,
    ) -> None:
        self._tracker = tracker
        self._mimir = mimir
        self._llm = llm
        self._config = config
        self._state_dir = state_dir or Path.home() / ".ravn" / "daemon"
        self._last_reflection_at: datetime | None = None
        self._last_deep_reflection_at: datetime | None = None

    @property
    def name(self) -> str:
        return "wakefulness"

    async def run(self, enqueue: Callable[[AgentTask], Awaitable[None]]) -> None:
        """Poll loop — runs until cancelled by the DriveLoop."""
        if not self._config.enabled:
            logger.info("WakefulnessTrigger: disabled — exiting without polling")
            return

        self._load_state()

        logger.info(
            "WakefulnessTrigger: starting (silence=%ds, cooldown=%ds, poll=%ds)",
            self._config.silence_threshold_seconds,
            self._config.reflection_cooldown_seconds,
            self._config.poll_interval_seconds,
        )

        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("WakefulnessTrigger: poll error: %s", exc)
                # Prevent tight loop on repeated errors
                await asyncio.sleep(self._config.poll_interval_seconds)
                continue

            await asyncio.sleep(self._config.poll_interval_seconds)

    async def _poll_once(self) -> None:
        """Single poll cycle: check silence, optionally reflect."""
        now = datetime.now(UTC)

        last = self._tracker.last()
        if last is None:
            return

        silence_seconds = (now - last).total_seconds()

        # Deep reflection pass — broader context, longer silence.
        if silence_seconds >= self._config.deep_reflection_threshold_seconds:
            if not self._in_deep_cooldown(now):
                await self._reflect(deep=True)
                self._last_deep_reflection_at = now
                self._save_state()
                return

        # Shallow reflection pass.
        if silence_seconds < self._config.silence_threshold_seconds:
            return

        if self._in_shallow_cooldown(now):
            return

        await self._reflect(deep=False)
        self._last_reflection_at = now
        self._save_state()

    def _in_shallow_cooldown(self, now: datetime) -> bool:
        if self._last_reflection_at is None:
            return False
        elapsed = (now - self._last_reflection_at).total_seconds()
        return elapsed < self._config.reflection_cooldown_seconds

    def _in_deep_cooldown(self, now: datetime) -> bool:
        if self._last_deep_reflection_at is None:
            return False
        elapsed = (now - self._last_deep_reflection_at).total_seconds()
        return elapsed < self._config.deep_reflection_cooldown_seconds

    async def _reflect(self, *, deep: bool) -> None:
        """Run LLM reflection and create threads for each intent."""
        label = "deep" if deep else "shallow"
        logger.info("WakefulnessTrigger: running %s reflection", label)

        prompt_template = _DEEP_PROMPT if deep else _SHALLOW_PROMPT
        prompt = prompt_template.format(max_intents=self._config.max_intents_per_reflection)

        intents = await self._call_llm(prompt)
        if intents is None:
            return

        capped = intents[: self._config.max_intents_per_reflection]

        for intent in capped:
            title = intent.get("title", "")
            if not title:
                continue

            try:
                await self._mimir.create_thread(
                    title=title,
                    weight=self._config.initial_thread_weight,
                    context_refs=None,
                    next_action_hint=intent.get("next_action_hint"),
                )
                logger.info(
                    "WakefulnessTrigger: created thread %r (why=%s)",
                    title,
                    intent.get("why", ""),
                )
            except Exception as exc:
                logger.warning(
                    "WakefulnessTrigger: failed to create thread %r: %s",
                    title,
                    exc,
                )

    async def _call_llm(self, prompt: str) -> list[dict] | None:
        """Call the LLM and parse a JSON array of intents."""
        try:
            response = await self._llm.generate(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                system="You are a reflective assistant. Respond only with valid JSON.",
                model=self._config.llm_alias,
                max_tokens=_REFLECTION_MAX_TOKENS,
            )
            parsed = json.loads(response.content)
            if not isinstance(parsed, list):
                logger.warning(
                    "WakefulnessTrigger: LLM returned non-array JSON: %s",
                    type(parsed).__name__,
                )
                return None
            return parsed
        except json.JSONDecodeError as exc:
            logger.warning("WakefulnessTrigger: malformed JSON from LLM: %s", exc)
            return None
        except Exception as exc:
            logger.warning("WakefulnessTrigger: LLM call failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        """Load persisted reflection timestamps from the state file."""
        state_file = self._state_dir / _STATE_FILE_NAME
        if not state_file.exists():
            return
        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
            if "last_reflection_at" in raw:
                self._last_reflection_at = datetime.fromisoformat(raw["last_reflection_at"])
            if "last_deep_reflection_at" in raw:
                self._last_deep_reflection_at = datetime.fromisoformat(
                    raw["last_deep_reflection_at"]
                )
        except Exception as exc:
            logger.warning("WakefulnessTrigger: could not load state: %s", exc)
            # Reset state on load failure to avoid stuck cooldowns
            self._last_reflection_at = None
            self._last_deep_reflection_at = None

    def _save_state(self) -> None:
        """Persist reflection timestamps to the state file."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        state_file = self._state_dir / _STATE_FILE_NAME
        state: dict[str, str] = {}
        if self._last_reflection_at is not None:
            state["last_reflection_at"] = self._last_reflection_at.isoformat()
        if self._last_deep_reflection_at is not None:
            state["last_deep_reflection_at"] = self._last_deep_reflection_at.isoformat()
        try:
            state_file.write_text(json.dumps(state), encoding="utf-8")
        except Exception as exc:
            logger.warning("WakefulnessTrigger: could not save state: %s", exc)
