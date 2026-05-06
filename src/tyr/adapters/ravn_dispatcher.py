"""Thin in-process ravn dispatcher for single-turn agent calls.

Runs a single LLM turn with a persona's system prompt and parses the
``---outcome---`` block from the response.  No DriveLoop, no mesh, no
discovery — just a persona, an initiative context, and one turn.

Used by ReviewEngine (review-arbiter) and BifrostAdapter (decomposer).
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

import httpx

from niuu.domain.llm_merge import merge_llm
from niuu.domain.outcome import OutcomeSchema, parse_outcome_block
from ravn.adapters.personas.loader import FilesystemPersonaAdapter, PersonaConfig
from ravn.ports.persona import PersonaPort
from tyr.adapters.anthropic_client import anthropic_messages_call

logger = logging.getLogger(__name__)


class RavnDispatcher:
    """Single-turn in-process ravn dispatcher.

    Loads a persona by name, builds a prompt from the persona's system
    prompt template plus the supplied initiative context, makes one
    Anthropic-compatible API call, and returns the parsed outcome fields.

    Parameters
    ----------
    base_url:
        Anthropic-compatible endpoint (default: ``https://api.anthropic.com``).
    api_key:
        API key forwarded as ``x-api-key``.
    model:
        Default model used when neither persona nor ``default_llm_config``
        specifies one.
    timeout:
        HTTP timeout in seconds.
    max_tokens:
        Maximum tokens for the LLM response (used when not overridden by config).
    persona_loader:
        Any :class:`~ravn.ports.persona.PersonaPort` implementation used to
        resolve persona configs.  Defaults to ``FilesystemPersonaAdapter``.
    default_llm_config:
        Global LLM override applied at every dispatch.  Merged on top of the
        persona's own ``llm`` settings using :func:`~niuu.domain.llm_merge.merge_llm`.
        Typically sourced from ``dispatch.in_process.llm_config`` (falling back
        to ``dispatch.flock.llm_config``).  Recognised keys: ``model``,
        ``max_tokens``.  Security keys (``allowed_tools``,
        ``forbidden_tools``) are silently dropped.
    """

    def __init__(
        self,
        *,
        base_url: str = "https://api.anthropic.com",
        api_key: str = "",
        model: str = "claude-sonnet-4-6",
        timeout: float = 60.0,
        max_tokens: int = 4096,
        persona_loader: PersonaPort | None = None,
        default_llm_config: dict | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._client = httpx.AsyncClient(timeout=timeout)
        self._loader = persona_loader or FilesystemPersonaAdapter()
        self._default_llm_config: dict = default_llm_config or {}

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    def load_persona(self, name: str) -> PersonaConfig | None:
        """Return a persona config by name, or ``None`` if not found."""
        return self._loader.load(name)

    async def dispatch(
        self,
        persona_name: str,
        initiative_context: str,
        *,
        model: str | None = None,
    ) -> dict[str, Any] | None:
        """Run a single LLM turn for the named persona.

        Loads the persona, prepends its system prompt, sends the
        *initiative_context* as the user message, and parses the
        ``---outcome---`` block from the response.

        Parameters
        ----------
        persona_name:
            Name of the persona to load (e.g. ``"review-arbiter"``).
        initiative_context:
            The user-facing context string passed to the agent.
        model:
            Override the default model for this dispatch.

        Returns
        -------
        dict | None
            Parsed outcome fields on success.  ``None`` when the persona
            cannot be loaded, the API call fails, or no outcome block is
            present.
        """
        persona = self._loader.load(persona_name)
        if persona is None:
            logger.warning("RavnDispatcher: persona %r not found", persona_name)
            return None

        schema: OutcomeSchema | None = None
        if persona.produces.schema:
            schema = OutcomeSchema(fields=persona.produces.schema)

        system_prompt = persona.system_prompt_template.strip()

        # Resolve effective LLM config: merge persona defaults with global override.
        # Persona's llm.primary_alias hints at the desired tier; default_llm_config
        # provides the concrete model.  Last-wins semantics: global_override beats
        # persona defaults for any non-empty key.
        persona_llm_dict = dataclasses.asdict(persona.llm)
        effective = merge_llm(
            defaults=persona_llm_dict,
            global_override=self._default_llm_config or None,
        )
        effective_model = effective.get("model") or self._model
        effective_max_tokens = int(effective.get("max_tokens") or self._max_tokens)

        used_model = model or effective_model
        used_max_tokens = effective_max_tokens

        try:
            text, _ = await anthropic_messages_call(
                self._client,
                base_url=self._base_url,
                api_key=self._api_key,
                model=used_model,
                max_tokens=used_max_tokens,
                messages=[{"role": "user", "content": initiative_context}],
                system=system_prompt or None,
            )
        except Exception:
            logger.warning(
                "RavnDispatcher: LLM call failed for persona %r",
                persona_name,
                exc_info=True,
            )
            return None

        outcome = parse_outcome_block(text, schema=schema)
        if outcome is None:
            logger.warning(
                "RavnDispatcher: no ---outcome--- block in response for persona %r",
                persona_name,
            )
            return None

        if not outcome.valid:
            logger.warning(
                "RavnDispatcher: invalid outcome for persona %r: %s",
                persona_name,
                outcome.errors,
            )
            return None

        return outcome.fields
