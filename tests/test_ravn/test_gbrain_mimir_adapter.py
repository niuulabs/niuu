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

from niuu.domain.mimir import (
    MimirPageMeta,
    MimirSource,
    PageConfidence,
    PageType,
    compute_content_hash,
    compute_source_id,
)
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
    async def test_list_pages_filters_prefix_locally(self) -> None:
        route = respx.post(_MCP).mock(
            return_value=httpx.Response(200, json=_tool_result(structured=[_page("wiki/x")]))
        )
        adapter = _adapter()

        metas = await adapter.list_pages(prefix="wiki/entities/")

        assert json.loads(route.calls[0].request.content)["params"]["arguments"] == {
            "limit": 100,
            "offset": 0,
            "sort": "slug",
        }
        assert metas == []
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
    @pytest.mark.parametrize("method", ["read_page", "get_page"])
    @pytest.mark.parametrize(
        "error,expected",
        [
            ({"error": "page_not_found", "message": "Page not found: log"}, FileNotFoundError),
            ({"error": "db_access", "message": "database unavailable"}, RuntimeError),
            (["page_not_found"], RuntimeError),
            ("rate limited", RuntimeError),
        ],
    )
    @respx.mock
    async def test_page_error_contract(self, method, error, expected) -> None:
        text = error if isinstance(error, str) else json.dumps(error)
        respx.post(_MCP).mock(
            return_value=httpx.Response(200, json=_tool_result(text=text, is_error=True))
        )
        adapter = _adapter()
        with pytest.raises(expected):
            await getattr(adapter, method)("log.md")
        await adapter.close()

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

    @pytest.mark.parametrize("op", ["lint"])
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


@respx.mock
async def test_graph_uses_stored_links_and_frontmatter():
    records = {
        "research/experiment": _page(
            "research/experiment",
            content=(
                "---\ncategory: research\ntype: observation\nsummary: Retrieval comparison\n"
                "source_ids: [paper]\n---\n[[concepts/retrieval]]"
            ),
        ),
        "concepts/retrieval": _page(
            "concepts/retrieval",
            content=(
                "---\ncategory: concepts\ntype: entity\nentity_type: concept\n"
                "source_ids: [paper]\n---\n# Retrieval"
            ),
        ),
    }

    def reply(request):
        params = json.loads(request.content)["params"]
        if params["name"] == "list_pages":
            return httpx.Response(200, json=_tool_result(structured=list(records.values())))
        assert params["name"] == "get_page"
        return httpx.Response(
            200, json=_tool_result(structured=records[params["arguments"]["slug"]])
        )

    respx.post(_MCP).mock(side_effect=reply)
    adapter = _adapter()
    try:
        graph = await adapter.get_graph()
        assert graph.nodes[0].category == "research"
        assert graph.nodes[0].kind == "observation"
        assert graph.nodes[0].summary == "Retrieval comparison"
        assert graph.nodes[1].kind == "concept"
        assert {edge.type for edge in graph.edges} == {"wikilink", "shared_source"}
    finally:
        await adapter.close()


@respx.mock
async def test_summary_paginates_all_pages():
    route = respx.post(_MCP).mock(
        side_effect=[
            httpx.Response(
                200, json=_tool_result(structured=[_page(f"notes/{i}") for i in range(100)])
            ),
            httpx.Response(200, json=_tool_result(structured=[_page("research/last")])),
        ]
    )
    adapter = _adapter()
    summary = await adapter.summarize()
    assert summary.page_count == 101
    assert summary.categories == ["notes", "research"]
    assert json.loads(route.calls[1].request.content)["params"]["arguments"]["offset"] == 100
    await adapter.close()


def test_sse_preserves_unicode_line_separators():
    from ravn.adapters.mimir.gbrain import _parse_mcp_response

    message = {"result": {"content": [{"text": "first\u2028second"}]}}
    assert (
        _parse_mcp_response("data: " + json.dumps(message, ensure_ascii=False) + "\n\n") == message
    )


class TestRawSources:
    @pytest.mark.parametrize("webhook", [False, True])
    @respx.mock
    async def test_lossless_source_roundtrip(self, webhook):
        source = MimirSource(
            source_id="src_raw",
            title="Title: with YAML",
            content=" \n---\ncontent\n<!-- timeline -->\n\n",
            source_type="web",
            ingested_at=datetime.now(UTC),
            content_hash=compute_content_hash(" \n---\ncontent\n<!-- timeline -->\n\n"),
            origin_url="https://example.test/source",
        )
        stored = {}

        def transport(request):
            if str(request.url) == _INGEST:
                stored["content"] = request.content.decode()
                return httpx.Response(202, json={"job_id": "ingestion"})
            params = json.loads(request.content)["params"]
            if params["name"] == "put_page":
                stored["content"] = params["arguments"]["content"]
                return httpx.Response(200, json=_tool_result())
            return httpx.Response(
                200,
                json=_tool_result(
                    structured=_page("sources/src_raw", content=stored["content"] + "\n")
                ),
            )

        respx.post(_MCP).mock(side_effect=transport)
        if webhook:
            respx.post(_INGEST).mock(side_effect=transport)
        adapter = _adapter(ingest_url=_INGEST if webhook else None)
        await adapter.ingest(source)
        assert await adapter.read_source(source.source_id) == source
        excerpt = await adapter.read_source_excerpt(source.source_id, 4)
        assert excerpt.content == source.content[:4]
        assert excerpt.content_hash == source.content_hash
        await adapter.close()

    @respx.mock
    async def test_missing_source_is_absent(self):
        respx.post(_MCP).mock(
            return_value=httpx.Response(
                200, json=_tool_result(text=json.dumps({"error": "page_not_found"}), is_error=True)
            )
        )
        adapter = _adapter()
        assert await adapter.read_source("src_absent") is None
        await adapter.close()

    @pytest.mark.parametrize("tampered", [False, True])
    @respx.mock
    async def test_legacy_source_checks_original_content_id(self, tampered):
        body = "Original evidence"
        source_id = compute_source_id(body)
        record = _page(
            "sources/" + source_id,
            title="Evidence",
            content="# Evidence\n\n" + ("modified" if tampered else body) + "\n",
            created_at="2026-09-10T00:00:00+00:00",
            source_uri="https://example.test",
        )
        respx.post(_MCP).mock(
            return_value=httpx.Response(200, json=_tool_result(structured=record))
        )
        adapter = _adapter()
        if tampered:
            with pytest.raises(ValueError, match="integrity check failed"):
                await adapter.read_source(source_id)
        else:
            source = await adapter.read_source(source_id)
            assert source.content == body
            assert source.origin_url == "https://example.test"
            assert source.source_type == "web"
        await adapter.close()

    @respx.mock
    async def test_list_sources_filters_compiled_sources(self):
        body = "evidence"
        source_id = compute_source_id(body)

        def transport(request):
            params = json.loads(request.content)["params"]
            if params["name"] == "list_pages":
                return httpx.Response(
                    200,
                    json=_tool_result(
                        structured=[
                            _page("sources/" + source_id),
                            _page("research/report", source_ids=[source_id]),
                        ]
                    ),
                )
            return httpx.Response(
                200,
                json=_tool_result(
                    structured=_page(
                        "sources/" + source_id,
                        title="Evidence",
                        content="# Evidence\n\n" + body,
                        created_at="2026-09-10T00:00:00+00:00",
                    )
                ),
            )

        respx.post(_MCP).mock(side_effect=transport)
        adapter = _adapter()
        sources = await adapter.list_sources()
        assert [s.source_id for s in sources] == [source_id]
        assert await adapter.list_sources(unprocessed_only=True) == []
        assert (await adapter.summarize()).source_count == 1
        await adapter.close()

    @pytest.mark.parametrize("payload", ["broken", "mismatch"])
    @respx.mock
    async def test_corrupt_source_is_not_accepted_as_evidence(self, payload):
        content = "broken"
        if payload == "mismatch":
            content = (
                "```json\n"
                + json.dumps(
                    {
                        "source_id": "src_raw",
                        "title": "Evidence",
                        "content": "tampered",
                        "source_type": "web",
                        "ingested_at": "2026-09-10T00:00:00+00:00",
                        "content_hash": compute_content_hash("original"),
                    }
                )
                + "\n```"
            )
        respx.post(_MCP).mock(
            return_value=httpx.Response(
                200,
                json=_tool_result(
                    structured=_page(
                        "sources/src_raw",
                        content=content,
                        frontmatter={"mimir_source_format": "json-v1"},
                    )
                ),
            )
        )
        adapter = _adapter()
        with pytest.raises(ValueError, match="Malformed|integrity"):
            await adapter.read_source("src_raw")
        await adapter.close()

    @respx.mock
    async def test_empty_response_is_not_source_absence(self):
        respx.post(_MCP).mock(return_value=httpx.Response(200, json=_tool_result()))
        adapter = _adapter()
        with pytest.raises(RuntimeError, match="no source record"):
            await adapter.read_source("src_raw")
        await adapter.close()
