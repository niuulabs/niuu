"""The /v1/systemone decision adapter: wire format, strict answer validation, explicit failure."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ravn.adapters.decision.systemone import SystemOneDecisionAdapter
from ravn.ports.decision import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionPort,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)


class _FakeClient:
    def __init__(self, payload: Any = None, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.sent: list[tuple[str, dict, dict]] = []

    async def post(self, url: str, headers: dict, json: dict) -> httpx.Response:
        self.sent.append((url, headers, json))
        request = httpx.Request("POST", url)
        if self.status != 200:
            body = self.payload if self.payload is not None else {"detail": "nope"}
            return httpx.Response(self.status, json=body, request=request)
        return httpx.Response(200, json=self.payload, request=request)


def _adapter(client: _FakeClient, **kwargs: Any) -> SystemOneDecisionAdapter:
    adapter = SystemOneDecisionAdapter(base_url="https://api.example/", **kwargs)
    adapter._client = client  # type: ignore[assignment]
    return adapter


_QUESTIONS = {
    "urgent": NoulQuestion("The message needs a reply today.", {"true": "yes", "false": "no"}),
    "route": ChoiceQuestion("Which team owns this?", {"billing": "money", "ops": None}),
    "risk": ScoreQuestion("How risky is the change?", ["none", "some", "severe"]),
}

_GOOD = {
    "model": "jev-1.14.0",
    "answers": {
        "urgent": {"type": "noul", "noul": 0.81},
        "route": {
            "type": "choice",
            "choice": "ops",
            "confidence": 0.7,
            "probabilities": {"billing": 0.2, "ops": 0.8},
        },
        "risk": {
            "type": "score",
            "score": 1.4,
            "confidence": 0.6,
            "legend": {"0": "none", "1": "some", "2": "severe"},
            "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5},
        },
    },
    "usage": {"input_tokens": 321, "output_tokens": 0},
}


def test_implements_the_port() -> None:
    assert isinstance(SystemOneDecisionAdapter(), DecisionPort)


def test_refuses_an_empty_model() -> None:
    with pytest.raises(ValueError, match="needs a model"):
        SystemOneDecisionAdapter(model=" ")


class TestWireFormat:
    async def test_posts_every_question_in_one_request(self) -> None:
        client = _FakeClient(_GOOD)
        await _adapter(client, model="clef-flash").decide(
            {"ticket": "x"}, _QUESTIONS, purpose="test"
        )

        url, headers, body = client.sent[0]
        assert url == "https://api.example/v1/systemone"
        assert "Authorization" not in headers
        assert body["model"] == "clef-flash"
        assert body["state"] == {"ticket": "x"}
        assert body["questions"] == {
            "urgent": {
                "type": "noul",
                "instructions": "The message needs a reply today.",
                "criteria": {"true": "yes", "false": "no"},
            },
            "route": {
                "type": "choice",
                "instructions": "Which team owns this?",
                "criteria": {"billing": "money", "ops": None},
            },
            "risk": {
                "type": "score",
                "instructions": "How risky is the change?",
                "criteria": ["none", "some", "severe"],
            },
        }

    async def test_noul_without_criteria_sends_none(self) -> None:
        client = _FakeClient({"answers": {"q": {"noul": 0.5}}})
        await _adapter(client).decide("s", {"q": NoulQuestion("x")}, purpose="test")

        assert "criteria" not in client.sent[0][2]["questions"]["q"]

    async def test_api_key_and_long_context_headers(self) -> None:
        client = _FakeClient(_GOOD)
        await _adapter(client, api_key="sk-test", long_context=True).decide(
            "s", _QUESTIONS, purpose="test"
        )

        headers = client.sent[0][1]
        assert headers["Authorization"] == "Bearer sk-test"
        assert headers["TypeSafe-Danger-Zone-Long-Context"] == "true"

    async def test_no_questions_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="asked no questions"):
            await _adapter(_FakeClient(_GOOD)).decide("s", {}, purpose="test")

    @pytest.mark.parametrize(
        "question",
        [ChoiceQuestion("x", {}), ScoreQuestion("x", [])],
    )
    async def test_empty_answer_space_is_an_error(self, question: Any) -> None:
        with pytest.raises(ValueError, match="at least one"):
            await _adapter(_FakeClient(_GOOD)).decide("s", {"q": question}, purpose="test")


class TestAnswers:
    async def test_parses_every_answer_type(self) -> None:
        result = await _adapter(_FakeClient(_GOOD)).decide("s", _QUESTIONS, purpose="test")

        assert result.model == "jev-1.14.0"
        assert result.input_tokens == 321
        assert result.answers["urgent"] == NoulAnswer(probability=0.81)
        assert result.answers["route"] == ChoiceAnswer(
            choice="ops", confidence=0.7, probabilities={"billing": 0.2, "ops": 0.8}
        )
        assert result.answers["risk"] == ScoreAnswer(
            score=1.4, confidence=0.6, probabilities={0: 0.1, 1: 0.4, 2: 0.5}
        )

    async def test_missing_usage_is_reported_as_unknown(self) -> None:
        payload = {"answers": _GOOD["answers"]}
        result = await _adapter(_FakeClient(payload), model="nimble").decide(
            "s", _QUESTIONS, purpose="test"
        )

        assert result.input_tokens is None
        assert result.model == "nimble"


def _with(qid: str, **override: Any) -> dict:
    answers = {key: dict(value) for key, value in _GOOD["answers"].items()}
    answers[qid].update(override)
    return {**_GOOD, "answers": answers}


class TestRejectsMalformedAnswers:
    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ([], "no answers object"),
            ({"answers": None}, "no answers object"),
            ({"answers": {"urgent": {"noul": 0.5}}}, "did not answer question 'route'"),
            (_with("urgent", noul=1.2), r"outside \[0, 1\]"),
            (_with("urgent", noul=True), "not a finite number"),
            (_with("urgent", noul="0.5"), "not a finite number"),
            (_with("route", choice="legal"), "not an option"),
            (_with("route", probabilities={"billing": 1.0}), "different option set"),
            (_with("route", confidence=None), "not a finite number"),
            (_with("risk", score=2.5), "outside levels 0..2"),
            (_with("risk", probabilities={"0": 1.0}), "different level set"),
            ({**_GOOD, "usage": "lots"}, "usage is not an object"),
            ({**_GOOD, "usage": {"input_tokens": -1}}, "input_tokens is invalid"),
        ],
    )
    async def test_raises(self, payload: Any, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            await _adapter(_FakeClient(payload)).decide("s", _QUESTIONS, purpose="test")

    async def test_http_error_raises(self) -> None:
        with pytest.raises(httpx.HTTPStatusError, match="rejected 'test' with 429"):
            await _adapter(_FakeClient(status=429)).decide("s", _QUESTIONS, purpose="test")

    async def test_rejection_names_the_services_reason(self) -> None:
        client = _FakeClient({"detail": {"error_type": "max_tokens_exceeded"}}, status=400)

        with pytest.raises(httpx.HTTPStatusError) as raised:
            await _adapter(client).decide("s", _QUESTIONS, purpose="test")

        assert str(raised.value).endswith('400: {"error_type": "max_tokens_exceeded"}')
        assert raised.value.response.status_code == 400

    async def test_rejection_without_detail_keeps_the_body(self) -> None:
        with pytest.raises(httpx.HTTPStatusError, match='503: {"message": "down"}'):
            await _adapter(_FakeClient({"message": "down"}, status=503)).decide(
                "s", _QUESTIONS, purpose="test"
            )

    async def test_rejection_with_a_plain_text_body(self) -> None:
        class _TextClient(_FakeClient):
            async def post(self, url: str, headers: dict, json: dict) -> httpx.Response:
                return httpx.Response(502, text="bad gateway", request=httpx.Request("POST", url))

        with pytest.raises(httpx.HTTPStatusError, match="502: bad gateway"):
            await _adapter(_TextClient()).decide("s", _QUESTIONS, purpose="test")


async def test_close_releases_the_client() -> None:
    adapter = SystemOneDecisionAdapter()
    adapter._get_client()
    await adapter.close()

    assert adapter._client is None
