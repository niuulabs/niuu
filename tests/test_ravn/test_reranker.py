"""The reranker, and the reason it applies its own template.

Measured against the live vLLM server on 2026-08-15, `/v1/rerank` does not apply Qwen3-Reranker's
instruction template, and the scores that come back are not relevance: a chocolate-cake document
ranked first for "what port is the broker on" (0.820) AND for "superconducting qubit physics"
(0.653). Wiring that into recall would actively demote the correct answer. With the template
applied, the same model orders correctly.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ravn.adapters.reranker.qwen import QwenRerankerAdapter


class _FakeClient:
    def __init__(
        self, scores: list[float] | None = None, status: int = 200, payload: Any = None
    ) -> None:
        self.scores = scores or []
        self.status = status
        self.payload = payload
        self.sent: list[dict] = []

    async def post(self, url: str, headers: dict, json: dict) -> httpx.Response:
        self.sent.append(json)
        request = httpx.Request("POST", url)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "nope"}, request=request)
        body = (
            self.payload
            if self.payload is not None
            else {"data": [{"index": i, "score": s} for i, s in enumerate(self.scores)]}
        )
        return httpx.Response(200, json=body, request=request)


def _adapter(client: _FakeClient) -> QwenRerankerAdapter:
    a = QwenRerankerAdapter(base_url="http://x/v1")
    a._client = client  # type: ignore[assignment]
    return a


class TestOrdering:
    async def test_documents_come_back_best_first(self) -> None:
        client = _FakeClient(scores=[0.20, 0.48, 0.23])
        ranked = await _adapter(client).rerank("q", ["cake", "broker", "qubits"])

        assert [i for i, _ in ranked] == [1, 2, 0]

    async def test_top_n_truncates(self) -> None:
        client = _FakeClient(scores=[0.20, 0.48, 0.23])
        ranked = await _adapter(client).rerank("q", ["a", "b", "c"], top_n=2)

        assert len(ranked) == 2


class TestTheTemplate:
    async def test_each_document_is_wrapped_in_the_instruction_format(self) -> None:
        """The template is the difference between relevance and noise — it is part of the prompt."""
        client = _FakeClient(scores=[0.1, 0.2])
        await _adapter(client).rerank("what port?", ["doc one", "doc two"])

        sent = client.sent[0]
        assert all("<Instruct>:" in t and "<Query>: what port?" in t for t in sent["text_2"])
        assert "<Document>: doc one" in sent["text_2"][0]


class TestConfiguredFailures:
    async def test_an_unavailable_service_raises(self) -> None:
        client = _FakeClient(status=503)

        with pytest.raises(httpx.HTTPStatusError):
            await _adapter(client).rerank("q", ["a", "b"])

    async def test_an_unexpected_payload_raises(self) -> None:
        client = _FakeClient(payload={"unexpected": "shape"})

        with pytest.raises(ValueError):
            await _adapter(client).rerank("q", ["a", "b"])

    async def test_a_partial_answer_is_refused_rather_than_dropping_candidates(self) -> None:
        """Scoring 1 of 2 would silently discard a candidate; the retrieval order is complete."""
        client = _FakeClient(payload={"data": [{"index": 0, "score": 0.9}]})

        with pytest.raises(ValueError):
            await _adapter(client).rerank("q", ["a", "b"])

    async def test_empty_input_asks_nothing(self) -> None:
        client = _FakeClient(scores=[])

        assert await _adapter(client).rerank("", ["a"]) == []
        assert await _adapter(client).rerank("q", []) == []
        assert client.sent == []
