"""Tests for OpenAIEmbeddingAdapter — all HTTP mocked with respx."""

from __future__ import annotations

import json

import pytest
import respx
from httpx import HTTPStatusError, Response

from ravn.adapters.embedding.openai import (
    _DEFAULT_DIMENSION,
    _DEFAULT_MODEL,
    OpenAIEmbeddingAdapter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(vectors: list[list[float]]) -> dict:
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)
        ],
        "model": _DEFAULT_MODEL,
        "usage": {"prompt_tokens": 8, "total_tokens": 8},
    }


def _models_response(*, max_model_len: int | None) -> dict:
    entry: dict = {"id": _DEFAULT_MODEL, "object": "model", "owned_by": "vllm"}
    if max_model_len is not None:
        entry["max_model_len"] = max_model_len
    return {"object": "list", "data": [entry]}


def _overflow() -> Response:
    """vLLM's verbatim refusal — it reports the clipped count, not the real one."""
    return Response(
        400,
        json={
            "error": {
                "message": (
                    "You passed 8193 input tokens and requested 0 output tokens. "
                    "However, the model's context length is only 8192 tokens"
                ),
                "type": "BadRequestError",
                "code": 400,
            }
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOpenAIEmbeddingAdapter:
    def test_default_dimension_before_call(self) -> None:
        adapter = OpenAIEmbeddingAdapter(api_key="test-key")
        assert adapter.dimension == _DEFAULT_DIMENSION

    def test_api_key_from_constructor(self) -> None:
        adapter = OpenAIEmbeddingAdapter(api_key="sk-abc")
        assert adapter._api_key == "sk-abc"

    def test_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        adapter = OpenAIEmbeddingAdapter()
        assert adapter._api_key == "sk-env"

    @respx.mock
    async def test_embed_returns_vector(self) -> None:
        vec = [0.1, 0.2, 0.3]
        respx.post("https://api.openai.com/v1/embeddings").mock(
            return_value=Response(200, json=_make_response([vec]))
        )
        adapter = OpenAIEmbeddingAdapter(api_key="test")
        result = await adapter.embed("hello")
        assert result == pytest.approx(vec)

    @respx.mock
    async def test_embed_batch_preserves_order(self) -> None:
        vecs = [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]]
        # Return in shuffled order — adapter must re-sort by index.
        shuffled_data = [
            {"object": "embedding", "index": 2, "embedding": vecs[2]},
            {"object": "embedding", "index": 0, "embedding": vecs[0]},
            {"object": "embedding", "index": 1, "embedding": vecs[1]},
        ]
        respx.post("https://api.openai.com/v1/embeddings").mock(
            return_value=Response(
                200,
                json={"object": "list", "data": shuffled_data, "model": _DEFAULT_MODEL},
            )
        )
        adapter = OpenAIEmbeddingAdapter(api_key="test")
        result = await adapter.embed_batch(["a", "b", "c"])
        assert result[0] == pytest.approx(vecs[0])
        assert result[1] == pytest.approx(vecs[1])
        assert result[2] == pytest.approx(vecs[2])

    @respx.mock
    async def test_dimension_updated_after_call(self) -> None:
        vec = [0.1] * 768
        respx.post("https://api.openai.com/v1/embeddings").mock(
            return_value=Response(200, json=_make_response([vec]))
        )
        adapter = OpenAIEmbeddingAdapter(api_key="test")
        await adapter.embed("text")
        assert adapter.dimension == 768

    @respx.mock
    async def test_http_error_propagates(self) -> None:
        respx.post("https://api.openai.com/v1/embeddings").mock(
            return_value=Response(401, json={"error": "Unauthorized"})
        )
        adapter = OpenAIEmbeddingAdapter(api_key="bad-key")
        with pytest.raises(Exception):
            await adapter.embed("text")

    @respx.mock
    async def test_custom_base_url(self) -> None:
        vec = [0.1, 0.2]
        respx.post("https://my-proxy.example.com/v1/embeddings").mock(
            return_value=Response(200, json=_make_response([vec]))
        )
        adapter = OpenAIEmbeddingAdapter(api_key="test", base_url="https://my-proxy.example.com/v1")
        result = await adapter.embed("hello")
        assert result == pytest.approx(vec)

    @respx.mock
    async def test_embed_batch_empty_list_skips_request(self) -> None:
        """Empty batch should still call the API (API handles it)."""
        respx.post("https://api.openai.com/v1/embeddings").mock(
            return_value=Response(200, json={"object": "list", "data": [], "model": _DEFAULT_MODEL})
        )
        adapter = OpenAIEmbeddingAdapter(api_key="test")
        result = await adapter.embed_batch([])
        assert result == []


class TestNoAuthSelfHostedServer:
    """Self-hosted OpenAI-compatible servers take no API key.

    vLLM, TGI and Ollama serve /v1/embeddings unauthenticated. Sending
    ``Authorization: Bearer`` with an empty value is not ignored — httpx
    rejects the bare ``"Bearer "`` with LocalProtocolError, so every call
    failed against exactly the deployments most likely to run without
    credentials. The header must be omitted, not emptied.
    """

    @respx.mock
    async def test_no_authorization_header_when_key_is_absent(self) -> None:
        route = respx.post("http://vllm.test/v1/embeddings").mock(
            return_value=Response(200, json=_make_response([[0.1, 0.2, 0.3]]))
        )
        adapter = OpenAIEmbeddingAdapter(api_key="", base_url="http://vllm.test/v1")

        vector = await adapter.embed("unhealthy pod in kube-system")

        assert vector == [0.1, 0.2, 0.3]
        assert "authorization" not in {k.lower() for k in route.calls[0].request.headers}
        await adapter.close()

    @respx.mock
    async def test_authorization_header_is_sent_when_a_key_is_configured(self) -> None:
        route = respx.post("http://api.test/v1/embeddings").mock(
            return_value=Response(200, json=_make_response([[0.4, 0.5]]))
        )
        adapter = OpenAIEmbeddingAdapter(api_key="sk-secret", base_url="http://api.test/v1")

        await adapter.embed("hello")

        assert route.calls[0].request.headers["authorization"] == "Bearer sk-secret"
        await adapter.close()

    @respx.mock
    async def test_dimension_is_learned_from_the_server(self) -> None:
        """Qwen3-Embedding returns 1024 dims, not the OpenAI default of 1536."""
        respx.post("http://vllm.test/v1/embeddings").mock(
            return_value=Response(200, json=_make_response([[0.0] * 1024]))
        )
        adapter = OpenAIEmbeddingAdapter(api_key="", base_url="http://vllm.test/v1")

        assert adapter.dimension == _DEFAULT_DIMENSION
        await adapter.embed("x")
        assert adapter.dimension == 1024
        await adapter.close()

    @respx.mock
    async def test_oversized_input_is_truncated_rather_than_rejected(self) -> None:
        """A long episode must not be able to kill the turn that recalls it.

        Qwen3-Embedding-0.6B has an 8192-token window and answers 400 —
        "You passed 8193 input tokens ... context length is only 8192" — rather
        than truncating. Memory prefetch treats an embedding failure as fatal,
        so before this Runa lost ten turns in six hours to oversized input.
        """
        route = respx.post("http://vllm.test/v1/embeddings").mock(
            return_value=Response(200, json=_make_response([[0.1, 0.2]]))
        )
        adapter = OpenAIEmbeddingAdapter(base_url="http://vllm.test/v1", max_input_chars=100)

        await adapter.embed("x" * 5000)

        sent = json.loads(route.calls[0].request.content)
        assert len(sent["input"][0]) == 100
        await adapter.close()

    @respx.mock
    async def test_input_within_the_window_is_sent_untouched(self) -> None:
        route = respx.post("http://vllm.test/v1/embeddings").mock(
            return_value=Response(200, json=_make_response([[0.1]]))
        )
        adapter = OpenAIEmbeddingAdapter(base_url="http://vllm.test/v1", max_input_chars=100)

        await adapter.embed("a short episode")

        assert json.loads(route.calls[0].request.content)["input"] == ["a short episode"]
        await adapter.close()

    @respx.mock
    async def test_optimistic_truncation_can_be_disabled(self) -> None:
        """0 sends the text whole; the window retry still backs it up."""
        route = respx.post("http://vllm.test/v1/embeddings").mock(
            return_value=Response(200, json=_make_response([[0.1]]))
        )
        adapter = OpenAIEmbeddingAdapter(base_url="http://vllm.test/v1", max_input_chars=0)

        await adapter.embed("y" * 3000)

        assert len(json.loads(route.calls[0].request.content)["input"][0]) == 3000
        await adapter.close()


class TestContextWindowOverflow:
    """The char bound is a guess; these cover it guessing wrong.

    Measured against Qwen3-Embedding-0.6B's 8192-token window, the chars that
    fit run from 47948 (prose) to 8204 (hex/UUID soup). No single bound serves
    both, so overflow has to be survivable rather than merely unlikely.
    """

    @respx.mock
    async def test_overflow_is_resent_clipped_to_the_real_window(self) -> None:
        models = respx.get("http://vllm.test/v1/models").mock(
            return_value=Response(200, json=_models_response(max_model_len=8192))
        )
        embeddings = respx.post("http://vllm.test/v1/embeddings").mock(
            side_effect=[_overflow(), Response(200, json=_make_response([[0.1, 0.2]]))]
        )
        adapter = OpenAIEmbeddingAdapter(base_url="http://vllm.test/v1", max_input_chars=0)

        assert await adapter.embed("j" * 24_000) == [0.1, 0.2]

        assert models.called
        assert len(embeddings.calls) == 2
        # One char cannot become more than one token, so 8192 chars always fit.
        assert len(json.loads(embeddings.calls[1].request.content)["input"][0]) == 8192
        await adapter.close()

    @respx.mock
    async def test_openai_phrasing_of_the_same_refusal_is_recognised(self) -> None:
        respx.get("http://vllm.test/v1/models").mock(
            return_value=Response(200, json=_models_response(max_model_len=8192))
        )
        embeddings = respx.post("http://vllm.test/v1/embeddings").mock(
            side_effect=[
                Response(
                    400,
                    json={
                        "error": {
                            "message": "This model's maximum context length is 8192 tokens",
                            "code": "context_length_exceeded",
                        }
                    },
                ),
                Response(200, json=_make_response([[0.3]])),
            ]
        )
        adapter = OpenAIEmbeddingAdapter(base_url="http://vllm.test/v1", max_input_chars=0)

        assert await adapter.embed("k" * 20_000) == [0.3]
        assert len(embeddings.calls) == 2
        await adapter.close()

    @respx.mock
    async def test_a_malformed_request_is_not_retried_shorter(self) -> None:
        """Retrying a real bad request would hide it behind a second failure."""
        embeddings = respx.post("http://vllm.test/v1/embeddings").mock(
            return_value=Response(400, json={"error": {"message": "unknown field 'inputs'"}})
        )
        adapter = OpenAIEmbeddingAdapter(base_url="http://vllm.test/v1")

        with pytest.raises(HTTPStatusError):
            await adapter.embed("hello")

        assert len(embeddings.calls) == 1
        await adapter.close()

    @respx.mock
    async def test_window_is_probed_once_and_reused(self) -> None:
        models = respx.get("http://vllm.test/v1/models").mock(
            return_value=Response(200, json=_models_response(max_model_len=8192))
        )
        respx.post("http://vllm.test/v1/embeddings").mock(
            side_effect=[
                _overflow(),
                Response(200, json=_make_response([[0.1]])),
                _overflow(),
                Response(200, json=_make_response([[0.2]])),
            ]
        )
        adapter = OpenAIEmbeddingAdapter(base_url="http://vllm.test/v1", max_input_chars=0)

        await adapter.embed("a" * 20_000)
        await adapter.embed("b" * 20_000)

        assert len(models.calls) == 1
        await adapter.close()

    @respx.mock
    async def test_unreported_window_raises_with_the_remedy(self) -> None:
        """No max_model_len means no safe length — guessing one is what failed."""
        respx.get("http://vllm.test/v1/models").mock(
            return_value=Response(200, json=_models_response(max_model_len=None))
        )
        respx.post("http://vllm.test/v1/embeddings").mock(return_value=_overflow())
        adapter = OpenAIEmbeddingAdapter(base_url="http://vllm.test/v1", max_input_chars=0)

        with pytest.raises(RuntimeError, match="max_input_chars"):
            await adapter.embed("z" * 20_000)
        await adapter.close()

    @respx.mock
    async def test_unreachable_model_listing_raises_with_the_remedy(self) -> None:
        respx.get("http://vllm.test/v1/models").mock(return_value=Response(404))
        respx.post("http://vllm.test/v1/embeddings").mock(return_value=_overflow())
        adapter = OpenAIEmbeddingAdapter(base_url="http://vllm.test/v1", max_input_chars=0)

        with pytest.raises(RuntimeError, match="could not be read"):
            await adapter.embed("z" * 20_000)
        await adapter.close()

    @respx.mock
    async def test_refusal_in_a_non_json_body_is_still_recognised(self) -> None:
        """A gateway can return the refusal as plain text or HTML."""
        respx.get("http://vllm.test/v1/models").mock(
            return_value=Response(200, json=_models_response(max_model_len=8192))
        )
        embeddings = respx.post("http://vllm.test/v1/embeddings").mock(
            side_effect=[
                Response(400, text="Bad Request: context length is only 8192 tokens"),
                Response(200, json=_make_response([[0.4]])),
            ]
        )
        adapter = OpenAIEmbeddingAdapter(base_url="http://vllm.test/v1", max_input_chars=0)

        assert await adapter.embed("m" * 20_000) == [0.4]
        assert len(embeddings.calls) == 2
        await adapter.close()

    @respx.mock
    async def test_a_listing_without_our_model_raises_with_the_remedy(self) -> None:
        respx.get("http://vllm.test/v1/models").mock(
            return_value=Response(
                200, json={"object": "list", "data": [{"id": "some-other-model"}]}
            )
        )
        respx.post("http://vllm.test/v1/embeddings").mock(return_value=_overflow())
        adapter = OpenAIEmbeddingAdapter(base_url="http://vllm.test/v1", max_input_chars=0)

        with pytest.raises(RuntimeError, match="max_input_chars"):
            await adapter.embed("z" * 20_000)
        await adapter.close()

    @respx.mock
    async def test_overflow_of_input_that_already_fits_names_the_batch(self) -> None:
        """Clipping cannot help when no single text is over the window."""
        respx.get("http://vllm.test/v1/models").mock(
            return_value=Response(200, json=_models_response(max_model_len=8192))
        )
        embeddings = respx.post("http://vllm.test/v1/embeddings").mock(return_value=_overflow())
        adapter = OpenAIEmbeddingAdapter(base_url="http://vllm.test/v1", max_input_chars=0)

        with pytest.raises(RuntimeError, match="batch size"):
            await adapter.embed_batch(["short one", "short two"])

        assert len(embeddings.calls) == 1
        await adapter.close()
