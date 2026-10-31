"""System One decision models over ``POST /v1/systemone``.

One wire protocol serves every model in this class: TypeSafe's hosted Jev
(``https://api.typesafe.ai``, ``jev-latest``), Cloudflare Clef / Clef-flash served by SGLang or
vLLM, and Ollama >= 0.35 (``http://127.0.0.1:11434``, ``clef-flash``, ``nimble``). Switching
between them is ``base_url`` + ``model``; nothing else changes.

The response is validated in full. Every question must come back answered, with its own type, with
finite probabilities inside their bounds and — for a Choice — a distribution over exactly the
options that were asked. Anything else raises: a decision model that silently answered a subset
would read as a decision the caller never got.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

import httpx

from ravn.ports.decision import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionAnswer,
    DecisionPort,
    DecisionQuestion,
    DecisionResult,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)

_DEFAULT_BASE_URL = "https://api.typesafe.ai"
_DEFAULT_MODEL = "jev-latest"
_DEFAULT_TIMEOUT_S = 30.0
_LONG_CONTEXT_HEADER = "TypeSafe-Danger-Zone-Long-Context"


class SystemOneDecisionAdapter(DecisionPort):
    """Ask a ``/v1/systemone`` decision model; configured failures are explicit."""

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        api_key: str = "",
        timeout: float = _DEFAULT_TIMEOUT_S,
        long_context: bool = False,
    ) -> None:
        if not model.strip():
            raise ValueError("SystemOneDecisionAdapter needs a model, e.g. 'jev-latest'")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._long_context = long_context
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self._long_context:
            headers[_LONG_CONTEXT_HEADER] = "true"
        return headers

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def decide(
        self,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        *,
        purpose: str,
    ) -> DecisionResult:
        if not questions:
            raise ValueError(f"decision {purpose!r} asked no questions")
        response = await self._get_client().post(
            f"{self._base_url}/v1/systemone",
            headers=self._headers(),
            json={
                "model": self._model,
                "state": state,
                "questions": {qid: _wire_question(q) for qid, q in questions.items()},
            },
        )
        if response.is_error:
            raise httpx.HTTPStatusError(
                f"decision model {self._model!r} rejected {purpose!r} with "
                f"{response.status_code}: {_error_detail(response)}",
                request=response.request,
                response=response,
            )
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
            raise ValueError(f"decision model {self._model!r} returned no answers object")
        raw_answers: dict[str, Any] = payload["answers"]
        answers: dict[str, DecisionAnswer] = {}
        for qid, question in questions.items():
            raw = raw_answers.get(qid)
            if not isinstance(raw, dict):
                raise ValueError(f"decision model {self._model!r} did not answer question {qid!r}")
            answers[qid] = _parse_answer(qid, question, raw)
        model = payload.get("model")
        return DecisionResult(
            model=model if isinstance(model, str) and model else self._model,
            answers=answers,
            input_tokens=_input_tokens(payload.get("usage")),
        )


def _wire_question(question: DecisionQuestion) -> dict[str, Any]:
    if isinstance(question, NoulQuestion):
        wire: dict[str, Any] = {"type": "noul", "instructions": question.instructions}
        if question.criteria is not None:
            wire["criteria"] = dict(question.criteria)
        return wire
    if isinstance(question, ChoiceQuestion):
        if not question.options:
            raise ValueError("a choice question needs at least one option")
        return {
            "type": "choice",
            "instructions": question.instructions,
            "criteria": dict(question.options),
        }
    if not question.levels:
        raise ValueError("a score question needs at least one level")
    return {
        "type": "score",
        "instructions": question.instructions,
        "criteria": list(question.levels),
    }


def _parse_answer(qid: str, question: DecisionQuestion, raw: dict[str, Any]) -> DecisionAnswer:
    if isinstance(question, NoulQuestion):
        return NoulAnswer(probability=_unit(raw.get("noul"), f"{qid}.noul"))
    if isinstance(question, ChoiceQuestion):
        choice = raw.get("choice")
        if choice not in question.options:
            raise ValueError(f"question {qid!r} chose {choice!r}, which was not an option")
        probabilities = raw.get("probabilities")
        if not isinstance(probabilities, dict) or set(probabilities) != set(question.options):
            raise ValueError(f"question {qid!r} returned probabilities for a different option set")
        return ChoiceAnswer(
            choice=choice,
            confidence=_unit(raw.get("confidence"), f"{qid}.confidence"),
            probabilities={
                name: _unit(value, f"{qid}.probabilities.{name}")
                for name, value in probabilities.items()
            },
        )
    return _parse_score(qid, question, raw)


def _parse_score(qid: str, question: ScoreQuestion, raw: dict[str, Any]) -> ScoreAnswer:
    top = len(question.levels) - 1
    score = _finite(raw.get("score"), f"{qid}.score")
    if not 0 <= score <= top:
        raise ValueError(f"question {qid!r} scored {score}, outside levels 0..{top}")
    probabilities = raw.get("probabilities")
    expected = {str(index) for index in range(len(question.levels))}
    if not isinstance(probabilities, dict) or set(probabilities) != expected:
        raise ValueError(f"question {qid!r} returned probabilities for a different level set")
    return ScoreAnswer(
        score=score,
        confidence=_unit(raw.get("confidence"), f"{qid}.confidence"),
        probabilities={
            int(level): _unit(value, f"{qid}.probabilities.{level}")
            for level, value in probabilities.items()
        },
    )


def _finite(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"decision answer {where} is not a finite number: {value!r}")
    return float(value)


def _unit(value: object, where: str) -> float:
    number = _finite(value, where)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"decision answer {where} is outside [0, 1]: {number}")
    return number


def _input_tokens(usage: object) -> int | None:
    """Reported billable input tokens; None only when the backend reports no usage at all."""
    if usage is None:
        return None
    if not isinstance(usage, dict):
        raise ValueError(f"decision response usage is not an object: {usage!r}")
    tokens = usage.get("input_tokens")
    if tokens is None:
        return None
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise ValueError(f"decision response usage.input_tokens is invalid: {tokens!r}")
    return tokens


def _error_detail(response: httpx.Response) -> str:
    """The service's own reason for rejecting a request, e.g. ``max_tokens_exceeded``.

    A bare status code says nothing about whether the request was too large, malformed or
    unauthorised, and the response body is otherwise lost with the exception.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text or "(empty body)"
    if isinstance(body, dict) and "detail" in body:
        return json.dumps(body["detail"], sort_keys=True)
    return json.dumps(body, sort_keys=True)
