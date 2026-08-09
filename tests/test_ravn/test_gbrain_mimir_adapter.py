"""GBrainMimirAdapter — gbrain behind MimirPort, all HTTP mocked with respx.

The adapter exists so gbrain can be measured against the markdown Mímir
adapter on the same golden set (NIU-1133). These tests pin the contract it
has to honour to make that comparison meaningful: the same page shapes, and
loud failures where gbrain simply cannot do what MimirPort asks.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from niuu.domain.mimir import MimirPageMeta, MimirSource, PageConfidence, PageType
from niuu.ports.mimir import MimirPort
from ravn.adapters.mimir.gbrain import GBrainMimirAdapter

_MCP = "https://brain.test/mcp"
_INGEST = "https://brain.test/ingest"


def _tool_result(*, text: str = "", structured: object = None, is_error: bool = False) -> dict:
    result: dict = {"content": [{"type": "text", "text": text}] if text else []}
    if structured is not None:
        result["structuredContent"] = structured
    if is_error:
        result["isError"] = True
    return {"jsonrpc": "2.0", "id": 1, "result": result}


def _page(slug: str, **extra) -> dict:
    return {"slug": slug, "title": slug, "content": f"body of {slug}", **extra}


def _adapter(**kwargs) -> GBrainMimirAdapter:
    return GBrainMimirAdapter(_MCP, "gbrain_tok", **kwargs)


class TestConstruction:
    def test_it_is_a_mimir_port(self) -> None:
        assert isinstance(_adapter(), MimirPort)

    def test_missing_token_is_rejected_at_construction(self) -> None:
        """A brain that will reject every call should not build silently."""
        with pytest.raises(ValueError, match="requires an API token"):
            GBrainMimirAdapter(_MCP, "")

    def test_missing_url_is_rejected_at_construction(self) -> None:
        with pytest.raises(ValueError, match="requires an MCP URL"):
            GBrainMimirAdapter("", "tok")


class TestRetrieval:
    @respx.mock
    async def test_search_returns_pages(self) -> None:
        respx.post(_MCP).mock(
            return_value=httpx.Response(
                200, json=_tool_result(structured=[_page("concepts/rrf"), _page("ops/pods")])
            )
        )
        adapter = _adapter()

        pages = await adapter.search("retrieval fusion")

        assert [p.meta.path for p in pages] == ["concepts/rrf", "ops/pods"]
        assert pages[0].content == "body of concepts/rrf"
        await adapter.close()

    @respx.mock
    async def test_search_uses_the_hybrid_tool_not_the_keyword_one(self) -> None:
        """gbrain's tool names invert the obvious reading.

        ``search`` is keyword-only tsvector; ``query`` is hybrid (vector +
        keyword + RRF + expansion). Mímir's ``search()`` is hybrid, so binding
        to ``search`` would hand the bake-off a rigged comparison.
        """
        route = respx.post(_MCP).mock(return_value=httpx.Response(200, json=_tool_result()))
        adapter = _adapter(search_limit=3)

        await adapter.search("unhealthy pods")

        sent = json.loads(route.calls[0].request.content)
        assert sent["params"]["name"] == "query"
        assert sent["params"]["arguments"] == {
            "query": "unhealthy pods",
            "limit": 3,
            "expand": True,
        }
        await adapter.close()

    @respx.mock
    async def test_query_expansion_can_be_turned_off(self) -> None:
        """Expansion costs a generation per search, so it is an explicit knob."""
        route = respx.post(_MCP).mock(return_value=httpx.Response(200, json=_tool_result()))
        adapter = _adapter(query_expansion=False)

        await adapter.search("unhealthy pods")

        sent = json.loads(route.calls[0].request.content)
        assert sent["params"]["arguments"]["expand"] is False
        await adapter.close()

    @respx.mock
    async def test_query_fills_the_answer_that_mimir_always_left_empty(self) -> None:
        """The whole point of this adapter.

        MimirQueryResult.answer is documented as an LLM-synthesised answer and
        both existing implementations return "". gbrain's `think` fills it.
        """
        respx.post(_MCP).mock(
            return_value=httpx.Response(
                200,
                json=_tool_result(
                    text=json.dumps(
                        {
                            "question": "why were pods failing?",
                            "answer": "Pods were failing readiness after the 1.32 upgrade.",
                            "citations": [_page("ops/pods")],
                            "synthesisOk": True,
                        }
                    ),
                ),
            )
        )
        adapter = _adapter()

        result = await adapter.query("why were pods failing?")

        assert result.answer == "Pods were failing readiness after the 1.32 upgrade."
        assert [p.meta.path for p in result.sources] == ["ops/pods"]
        await adapter.close()

    @respx.mock
    async def test_query_raises_when_gbrain_could_not_synthesise(self) -> None:
        """`think` answers 200 with a placeholder when it has no chat model.

        Observed live: ``answer`` becomes "(no LLM available — set
        ANTHROPIC_API_KEY ...)" with ``synthesisOk: false``. Passing that back
        would put a placeholder into a resident's reasoning and call it an
        answer.
        """
        respx.post(_MCP).mock(
            return_value=httpx.Response(
                200,
                json=_tool_result(
                    text=json.dumps(
                        {
                            "answer": "(no LLM available — set ANTHROPIC_API_KEY or pass `client`)",
                            "citations": [],
                            "gaps": ["no LLM available; gather succeeded but synthesis skipped"],
                            "warnings": ["NO_ANTHROPIC_API_KEY"],
                            "synthesisOk": False,
                        }
                    ),
                ),
            )
        )
        adapter = _adapter()

        with pytest.raises(RuntimeError, match="NO_ANTHROPIC_API_KEY"):
            await adapter.query("why were pods failing?")
        await adapter.close()

    @respx.mock
    async def test_query_calls_think_with_a_question_argument(self) -> None:
        """`think` takes `question`; the retrieval tools take `query`."""
        route = respx.post(_MCP).mock(
            return_value=httpx.Response(
                200, json=_tool_result(text=json.dumps({"answer": "x", "synthesisOk": True}))
            )
        )
        adapter = _adapter()

        await adapter.query("what happened?")

        sent = json.loads(route.calls[0].request.content)
        assert sent["params"]["name"] == "think"
        assert sent["params"]["arguments"] == {"question": "what happened?"}
        await adapter.close()

    @respx.mock
    async def test_think_model_is_passed_because_gbrain_ignores_its_own_config(self) -> None:
        """`think` does not honour the brain's configured chat_model.

        Observed live: with `chat_model` set to a reachable vLLM, `think`
        still reported `modelUsed: anthropic:claude-opus-4-7` and failed on
        NO_ANTHROPIC_API_KEY. Only the per-call `model` argument is obeyed.
        """
        route = respx.post(_MCP).mock(
            return_value=httpx.Response(
                200, json=_tool_result(text=json.dumps({"answer": "x", "synthesisOk": True}))
            )
        )
        adapter = _adapter(think_model="nvidia:nvidia/nemotron-3-super")

        await adapter.query("what happened?")

        sent = json.loads(route.calls[0].request.content)
        assert sent["params"]["arguments"]["model"] == "nvidia:nvidia/nemotron-3-super"
        await adapter.close()

    @respx.mock
    async def test_think_citations_keep_their_page_paths(self) -> None:
        """`think` names the cited page `page_slug`; retrieval uses `slug`.

        Reading only `slug` left every citation with an empty path — sources
        that look present and identify nothing.
        """
        respx.post(_MCP).mock(
            return_value=httpx.Response(
                200,
                json=_tool_result(
                    text=json.dumps(
                        {
                            "answer": "Nordvolt firmware.",
                            "citations": [
                                {"page_slug": "entities/nordvolt", "citation_index": 1},
                                {"page_slug": "projects/helios", "citation_index": 2},
                            ],
                            "synthesisOk": True,
                        }
                    ),
                ),
            )
        )
        adapter = _adapter()

        result = await adapter.query("who caused the delay?")

        assert [p.meta.path for p in result.sources] == [
            "entities/nordvolt",
            "projects/helios",
        ]
        await adapter.close()

    @respx.mock
    async def test_get_page_raises_when_the_page_is_absent(self) -> None:
        """MimirPort callers catch FileNotFoundError; an empty page is not a page."""
        respx.post(_MCP).mock(return_value=httpx.Response(200, json=_tool_result()))
        adapter = _adapter()

        with pytest.raises(FileNotFoundError):
            await adapter.get_page("wiki/entities/nobody.md")
        await adapter.close()

    @respx.mock
    async def test_list_pages_passes_a_slugified_prefix(self) -> None:
        route = respx.post(_MCP).mock(
            return_value=httpx.Response(200, json=_tool_result(structured=[_page("wiki/x")]))
        )
        adapter = _adapter()

        metas = await adapter.list_pages(prefix="wiki/entities/")

        assert json.loads(route.calls[0].request.content)["params"]["arguments"]["prefix"] == (
            "wiki/entities"
        )
        assert [m.path for m in metas] == ["wiki/x"]
        await adapter.close()


class TestWrites:
    @respx.mock
    async def test_upsert_page_slugifies_the_mimir_path(self) -> None:
        route = respx.post(_MCP).mock(return_value=httpx.Response(200, json=_tool_result()))
        adapter = _adapter()

        await adapter.upsert_page("wiki/entities/person-karpathy.md", "# Andrej")

        args = json.loads(route.calls[0].request.content)["params"]["arguments"]
        assert args["slug"] == "wiki/entities/person-karpathy"
        assert args["content"] == "# Andrej"
        await adapter.close()

    @respx.mock
    async def test_page_metadata_survives_the_round_trip(self) -> None:
        """gbrain has no typed frontmatter, so Mímir's rides along in the body.

        Without this a page loses its type and confidence the moment it is
        written to gbrain, which would quietly degrade the corpus.
        """
        route = respx.post(_MCP).mock(return_value=httpx.Response(200, json=_tool_result()))
        adapter = _adapter()
        meta = MimirPageMeta(
            path="wiki/decisions/use-rrf.md",
            title="Use RRF",
            summary="",
            category="decisions",
            updated_at=datetime.now(UTC),
            page_type=PageType.decision,
            confidence=PageConfidence.high,
        )

        await adapter.upsert_page("wiki/decisions/use-rrf.md", "## Compiled Truth", meta=meta)

        content = json.loads(route.calls[0].request.content)["params"]["arguments"]["content"]
        assert "mimir_path: wiki/decisions/use-rrf.md" in content
        assert "type: decision" in content
        assert "confidence: high" in content
        await adapter.close()

    @respx.mock
    async def test_ingest_uses_the_webhook_when_configured(self) -> None:
        route = respx.post(_INGEST).mock(return_value=httpx.Response(200, text="ok"))
        adapter = _adapter(ingest_url=_INGEST)
        source = MimirSource(
            source_id="src_abc",
            title="Incident review",
            content="the pod restarted",
            source_type="document",
            ingested_at=datetime.now(UTC),
            content_hash="h",
        )

        refs = await adapter.ingest(source)

        request = route.calls[0].request
        assert request.headers["x-gbrain-source-id"] == "src_abc"
        assert request.headers["content-type"].startswith("text/markdown")
        assert refs == ["sources/src_abc"]
        await adapter.close()

    @respx.mock
    async def test_ingest_falls_through_to_put_page_without_a_webhook(self) -> None:
        """Two transports for the same write, not a degraded mode."""
        route = respx.post(_MCP).mock(return_value=httpx.Response(200, json=_tool_result()))
        adapter = _adapter()
        source = MimirSource(
            source_id="src_def",
            title="Note",
            content="body",
            source_type="document",
            ingested_at=datetime.now(UTC),
            content_hash="h",
        )

        await adapter.ingest(source)

        assert json.loads(route.calls[0].request.content)["params"]["name"] == "put_page"
        await adapter.close()


class TestFailuresAreLoud:
    @respx.mock
    async def test_a_tool_error_raises(self) -> None:
        respx.post(_MCP).mock(
            return_value=httpx.Response(200, json=_tool_result(text="rate limited", is_error=True))
        )
        adapter = _adapter()

        with pytest.raises(RuntimeError, match="rate limited"):
            await adapter.search("anything")
        await adapter.close()

    @respx.mock
    async def test_a_jsonrpc_error_raises(self) -> None:
        respx.post(_MCP).mock(
            return_value=httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "nope"}}
            )
        )
        adapter = _adapter()

        with pytest.raises(RuntimeError, match="nope"):
            await adapter.search("anything")
        await adapter.close()

    @pytest.mark.parametrize("op", ["lint", "list_sources", "summarize"])
    async def test_unsupported_operations_raise_rather_than_look_empty(self, op: str) -> None:
        """gbrain cannot lint or enumerate raw sources.

        Returning a clean report over an unlinted corpus, or an empty source
        list, would read as success. Per .claude/rules/no-fallbacks.md these
        say so instead.
        """
        adapter = _adapter()

        with pytest.raises(NotImplementedError, match="no equivalent"):
            await getattr(adapter, op)()
        await adapter.close()

    async def test_read_source_raises_too(self) -> None:
        adapter = _adapter()

        with pytest.raises(NotImplementedError, match="no equivalent"):
            await adapter.read_source("src_abc")
        await adapter.close()


class TestTransportQuirks:
    @respx.mock
    async def test_sse_framed_responses_are_parsed(self) -> None:
        """gbrain may answer as text/event-stream depending on the client."""
        body = "event: message\ndata: " + json.dumps(_tool_result(structured=[_page("a/b")]))
        respx.post(_MCP).mock(return_value=httpx.Response(200, text=body))
        adapter = _adapter()

        pages = await adapter.search("x")

        assert [p.meta.path for p in pages] == ["a/b"]
        await adapter.close()

    @respx.mock
    async def test_json_in_a_text_block_is_parsed(self) -> None:
        """Structured content is preferred, but JSON-in-text is also accepted."""
        respx.post(_MCP).mock(
            return_value=httpx.Response(
                200, json=_tool_result(text=json.dumps({"results": [_page("c/d")]}))
            )
        )
        adapter = _adapter()

        pages = await adapter.search("x")

        assert [p.meta.path for p in pages] == ["c/d"]
        await adapter.close()

    @respx.mock
    async def test_an_empty_response_body_raises(self) -> None:
        respx.post(_MCP).mock(return_value=httpx.Response(200, text=""))
        adapter = _adapter()

        with pytest.raises(RuntimeError, match="empty response"):
            await adapter.search("x")
        await adapter.close()

    @respx.mock
    async def test_the_bearer_token_is_sent(self) -> None:
        route = respx.post(_MCP).mock(return_value=httpx.Response(200, json=_tool_result()))
        adapter = _adapter()

        await adapter.search("x")

        assert route.calls[0].request.headers["authorization"] == "Bearer gbrain_tok"
        await adapter.close()


class TestConfigWiring:
    """gbrain reaches a resident through mimir.instances, not a code change.

    Per .claude/rules/dynamic-adapters.md a new backend is a class path plus
    kwargs — adding one should touch YAML, not the container.
    """

    def test_a_mimir_instance_can_name_the_gbrain_adapter(self, monkeypatch) -> None:
        from ravn.cli.runtime_builders import _build_mimir
        from ravn.config import MimirInstanceConfig, Settings

        monkeypatch.setenv("TEST_GBRAIN_TOKEN", "gbrain_from_env")
        settings = Settings()
        settings.mimir.enabled = True
        settings.mimir.instances = [
            MimirInstanceConfig(
                name="brain",
                role="shared",
                adapter="ravn.adapters.mimir.gbrain.GBrainMimirAdapter",
                kwargs={"mcp_url": "https://brain.test/mcp"},
                secret_kwargs_env={"api_token": "TEST_GBRAIN_TOKEN"},
            )
        ]

        built = _build_mimir(settings)

        assert built is not None

    def test_an_instance_with_no_backend_raises_instead_of_being_skipped(self) -> None:
        """It used to warn and continue, leaving the mount silently absent."""
        from ravn.cli.runtime_builders import _build_mimir
        from ravn.config import MimirInstanceConfig, Settings

        settings = Settings()
        settings.mimir.enabled = True
        settings.mimir.instances = [MimirInstanceConfig(name="nowhere", role="local")]

        with pytest.raises(ValueError, match="no adapter, path or url"):
            _build_mimir(settings)
