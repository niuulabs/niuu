"""BifröstAdapter — LLM spec decomposition via Anthropic-compatible HTTP API.

Works with the Anthropic API directly, Bifröst gateway, or any
Anthropic-compatible endpoint (e.g. Ollama with Anthropic compat).
"""

from __future__ import annotations

import json
import logging

import httpx

from ting.adapters.anthropic_client import anthropic_messages_call
from ting.adapters.bifrost_publisher import BifrostPublisher
from ting.domain.models import SagaStructure
from ting.domain.validation import ValidationError, parse_and_validate
from ting.ports.llm import LLMPort

logger = logging.getLogger(__name__)

DECOMPOSITION_PROMPT = """\
You are a saga decomposition engine for the Niuu platform.

Given a specification and repository, decompose the work into a structured saga \
with phases and runs.

## Rules

1. Each run MUST be completable in a single Völundr session (2–6 hours estimated).
2. Runs estimated over 8 hours MUST be split into smaller runs.
3. `declared_files` MUST be non-empty — every run must specify which files it touches.
4. `acceptance_criteria` MUST have at least one item per run.
5. Runs that are too vague MUST be split into more specific sub-runs.
6. Self-score your confidence for each run:
   - 1.0 = highly specific, well-bounded, clear acceptance criteria
   - 0.5 = moderate clarity, some ambiguity remains
   - 0.0 = vague, risky, or poorly scoped

## Output format

Respond with ONLY valid JSON (no markdown fences, no commentary). The schema:

{{
  "name": "<saga name>",
  "phases": [
    {{
      "name": "<phase name>",
      "runs": [
        {{
          "name": "<run name>",
          "description": "<what this run accomplishes>",
          "acceptance_criteria": ["<criterion 1>", ...],
          "declared_files": ["<file path 1>", ...],
          "estimate_hours": <float between 2 and 6>,
          "confidence": <float between 0.0 and 1.0>
        }}
      ]
    }}
  ]
}}

## Input

Repository: {repo}

Specification:
{spec}
"""

# HTTP status codes that trigger a retry (transient server/rate-limit errors).
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503})

# HTTP status codes that indicate the upstream provider is unhealthy.
_PROVIDER_ERROR_CODES = frozenset({500, 502, 503, 504})


class DecompositionError(Exception):
    """Raised when LLM decomposition fails after all retries."""


class BifrostAdapter(LLMPort):
    """Routes spec decomposition via an Anthropic-compatible Messages API.

    Works with the Anthropic API, Bifröst gateway, or any compatible endpoint.
    Constructor kwargs are forwarded from LLMConfig via the dynamic adapter pattern.

    A :class:`~ting.adapters.bifrost_publisher.BifrostPublisher` can be injected
    after construction via :meth:`set_publisher` to enable Sleipnir event emission:

    * ``bifrost.request.complete`` — after every successful LLM call
    * ``bifrost.quota.warning``    — when cumulative tokens reach *quota_warning_threshold*
    * ``bifrost.quota.exceeded``   — when cumulative tokens exceed *budget_tokens*
    * ``bifrost.provider.down``    — when a provider error is detected
    * ``bifrost.provider.recovered``— when the provider returns a success after an error
    """

    def __init__(
        self,
        *,
        base_url: str = "https://api.anthropic.com",
        api_key: str = "",
        timeout: float = 120.0,
        max_tokens: int = 8192,
        max_retries: int = 2,
        min_estimate_hours: float = 2.0,
        max_estimate_hours: float = 8.0,
        decomposition_system_prompt: str = "",
        budget_tokens: int = 0,
        quota_warning_threshold: float = 0.8,
        agent_id: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._min_estimate_hours = min_estimate_hours
        self._max_estimate_hours = max_estimate_hours
        self._decomposition_prompt = decomposition_system_prompt or DECOMPOSITION_PROMPT
        self._client = httpx.AsyncClient(timeout=timeout)
        self._budget_tokens = budget_tokens
        self._quota_warning_threshold = quota_warning_threshold
        self._agent_id = agent_id
        # Runtime state
        self._publisher: BifrostPublisher | None = None
        self._provider_healthy: bool = True
        self._total_tokens: int = 0
        self._quota_warning_emitted: bool = False
        self._quota_exceeded_emitted: bool = False

    def set_publisher(self, publisher: BifrostPublisher) -> None:
        """Inject the Sleipnir publisher.  Called from main.py after wiring."""
        self._publisher = publisher

    async def decompose_spec(self, spec: str, repo: str, *, model: str) -> SagaStructure:
        return await self._decompose_via_api(spec, repo, model=model)

    async def _decompose_via_api(self, spec: str, repo: str, *, model: str) -> SagaStructure:
        """Decompose through the Anthropic-compatible Messages API."""
        prompt = self._decomposition_prompt.format(spec=spec, repo=repo)
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                raw, usage = await self._call_api(prompt, model=model)
                result = parse_and_validate(
                    raw,
                    min_estimate_hours=self._min_estimate_hours,
                    max_estimate_hours=self._max_estimate_hours,
                )
                await self._on_success(model=model, usage=usage)
                return result
            except (json.JSONDecodeError, ValidationError) as exc:
                logger.warning(
                    "Decomposition attempt %d/%d failed: %s",
                    attempt,
                    self._max_retries,
                    exc,
                )
                last_error = exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _RETRYABLE_STATUS_CODES:
                    raise
                logger.warning(
                    "Decomposition attempt %d/%d: HTTP %d",
                    attempt,
                    self._max_retries,
                    exc.response.status_code,
                )
                await self._on_provider_error(exc)
                last_error = exc

        raise DecompositionError(
            f"Failed to decompose spec after {self._max_retries} attempts: {last_error}"
        )

    async def _call_api(self, prompt: str, *, model: str) -> tuple[str, dict]:
        """Call the Anthropic-compatible Messages API.

        Returns:
            A ``(text, usage)`` tuple where *usage* contains ``input_tokens``
            and ``output_tokens`` as reported by the API.
        """
        return await anthropic_messages_call(
            self._client,
            base_url=self._base_url,
            api_key=self._api_key,
            model=model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Event emission helpers
    # ------------------------------------------------------------------

    async def _on_success(self, *, model: str, usage: dict) -> None:
        """Handle a successful API call: emit request.complete + quota events."""
        if self._publisher is None:
            return

        input_tokens = usage["input_tokens"]
        output_tokens = usage["output_tokens"]
        latency_ms = usage["latency_ms"]

        # Recover if the provider was previously marked as unhealthy.
        if not self._provider_healthy:
            self._provider_healthy = True
            await self._publisher.provider_recovered(provider=self._base_url)

        await self._publisher.request_complete(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )

        self._total_tokens += input_tokens + output_tokens
        await self._check_quota()

    async def _on_provider_error(self, exc: httpx.HTTPStatusError) -> None:
        """Emit provider.down on the first 5xx/502/503 from the provider."""
        if self._publisher is None:
            return
        if exc.response.status_code not in _PROVIDER_ERROR_CODES:
            return
        if self._provider_healthy:
            self._provider_healthy = False
            await self._publisher.provider_down(
                provider=self._base_url,
                status_code=exc.response.status_code,
                error=exc.response.text or exc.response.reason_phrase or "unknown error",
            )

    async def _check_quota(self) -> None:
        """Emit quota events when cumulative token usage crosses configured thresholds.

        Only called from :meth:`_on_success`, which already guards ``publisher is None``.
        """
        if self._budget_tokens <= 0:
            return

        pct_used = self._total_tokens / self._budget_tokens

        if pct_used >= self._quota_warning_threshold and not self._quota_warning_emitted:
            self._quota_warning_emitted = True
            await self._publisher.quota_warning(
                tokens_used=self._total_tokens,
                budget_tokens=self._budget_tokens,
            )

        if pct_used >= 1.0 and not self._quota_exceeded_emitted:
            self._quota_exceeded_emitted = True
            await self._publisher.quota_exceeded(
                tokens_used=self._total_tokens,
                budget_tokens=self._budget_tokens,
            )
