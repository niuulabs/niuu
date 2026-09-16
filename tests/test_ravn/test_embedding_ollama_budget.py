"""Ollama embedding inputs are bounded, and configured embedding failures are explicit.

The failure this covers, from the journal at 22:01:13 on 2026-08-14:

    ollama[2270]: ... /api/embed 400 ... "the input length exceeds the context length"

nomic-embed-text holds 2048 tokens. The adapter posted whatever it was given, so one long earlier
message in the room made every subsequent embed 400 — and because indexing happens on the turn
path, that 400 propagated out and Neo simply never answered.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ravn.adapters.embedding.ollama import OllamaEmbeddingAdapter


class _FakeClient:
    """Records requests; answers /api/show and /api/embed like ollama does."""

    def __init__(
        self, *, context_length: int | None = 2048, reject_over: int | None = None
    ) -> None:
        self.context_length = context_length
        self.reject_over = reject_over
        self.embed_inputs: list[list[str]] = []

    async def post(self, url: str, json: dict[str, Any]) -> httpx.Response:
        request = httpx.Request("POST", url)
        if url.endswith("/api/show"):
            if self.context_length is None:
                return httpx.Response(404, json={"error": "not found"}, request=request)
            return httpx.Response(
                200,
                json={"model_info": {"nomic-bert.context_length": self.context_length}},
                request=request,
            )
        inputs = json["input"]
        self.embed_inputs.append(inputs)
        if self.reject_over is not None and any(len(t) > self.reject_over for t in inputs):
            # Exactly ollama's shape for this failure.
            return httpx.Response(
                400,
                json={"error": "the input length exceeds the context length"},
                request=request,
            )
        return httpx.Response(
            200, json={"embeddings": [[0.1, 0.2] for _ in inputs]}, request=request
        )


def _adapter(client: _FakeClient, **kwargs: Any) -> OllamaEmbeddingAdapter:
    adapter = OllamaEmbeddingAdapter(**kwargs)
    adapter._client = client  # type: ignore[assignment]
    return adapter


class TestInputBudget:
    async def test_a_long_input_is_truncated_rather_than_rejected(self) -> None:
        """The regression: 2048 tokens of context, and a blob far past it."""
        client = _FakeClient(context_length=2048, reject_over=2048 * 3)
        adapter = _adapter(client)

        vector = await adapter.embed("x" * 50_000)

        assert vector == [0.1, 0.2]
        assert len(client.embed_inputs[-1][0]) == 2048 * 3

    async def test_a_short_input_is_sent_untouched(self) -> None:
        client = _FakeClient()
        adapter = _adapter(client)

        await adapter.embed("a short memory")

        assert client.embed_inputs[-1] == ["a short memory"]

    async def test_the_budget_follows_the_model_not_a_constant(self) -> None:
        """A model with more room should get to use it."""
        client = _FakeClient(context_length=8192)
        adapter = _adapter(client)

        await adapter.embed("x" * 50_000)

        assert len(client.embed_inputs[-1][0]) == 8192 * 3

    async def test_context_discovery_failure_propagates(self) -> None:
        """A failed configured server must not silently substitute a budget."""
        client = _FakeClient(context_length=None)
        adapter = _adapter(client)

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.embed("x" * 50_000)
        assert client.embed_inputs == []

    async def test_an_explicit_override_wins_and_asks_nothing(self) -> None:
        client = _FakeClient(context_length=8192)
        adapter = _adapter(client, max_input_chars=100)

        await adapter.embed("x" * 5_000)

        assert len(client.embed_inputs[-1][0]) == 100

    async def test_the_context_length_is_asked_once(self) -> None:
        client = _FakeClient()
        adapter = _adapter(client)

        await adapter.embed("one")
        await adapter.embed("two")

        assert adapter._context_tokens == 2048

    async def test_every_input_in_a_batch_is_bounded(self) -> None:
        client = _FakeClient(context_length=2048, reject_over=2048 * 3)
        adapter = _adapter(client)

        vectors = await adapter.embed_batch(["short", "y" * 40_000])

        assert len(vectors) == 2
        assert [len(t) for t in client.embed_inputs[-1]] == [5, 2048 * 3]

    async def test_an_empty_batch_calls_nothing(self) -> None:
        client = _FakeClient()
        adapter = _adapter(client)

        assert await adapter.embed_batch([]) == []
        assert client.embed_inputs == []


@pytest.mark.asyncio
class TestConfiguredEmbeddingFailures:
    async def test_a_document_is_not_indexed_when_embedding_fails(self, tmp_path: Any) -> None:
        """Configured semantic indexing must not silently store an unembedded row."""
        from niuu.adapters.search.sqlite import SqliteSearchAdapter

        async def _explode(_content: str) -> list[float]:
            raise httpx.HTTPStatusError(
                "400", request=httpx.Request("POST", "/api/embed"), response=httpx.Response(400)
            )

        adapter = SqliteSearchAdapter(str(tmp_path / "search.db"), embed_fn=_explode)
        await adapter.initialize()

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.index("doc-1", "a memory worth keeping", {})
        assert await adapter.unembedded(limit=10) == []
        await adapter.close()

    async def test_recall_fails_when_the_query_cannot_be_embedded(self, tmp_path: Any) -> None:
        """Configured semantic recall must not silently downgrade to keyword search."""
        from niuu.adapters.search.sqlite import SqliteSearchAdapter

        state = {"fail": False}

        async def _sometimes(_content: str) -> list[float]:
            if state["fail"]:
                raise httpx.HTTPStatusError(
                    "400",
                    request=httpx.Request("POST", "/api/embed"),
                    response=httpx.Response(400),
                )
            return [0.1, 0.2]

        adapter = SqliteSearchAdapter(str(tmp_path / "recall.db"), embed_fn=_sometimes)
        await adapter.initialize()
        await adapter.index("doc-1", "the broker listens on 7503", {})

        state["fail"] = True
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.search("broker")
        await adapter.close()
