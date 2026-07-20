"""Tests for the Retrieval Reflex scanner (NIU-1059) — src/ravn/reflex.py."""

from __future__ import annotations

import logging
import time

import httpx
import pytest
import respx

from ravn.config import MimirAuthConfig, MimirConfig, MimirInstanceConfig, MimirReflexConfig
from ravn.reflex import (
    INJECTED_LOG_EVENT,
    POINTER_BLOCK_HEADER,
    EntityIndex,
    EntityIndexCache,
    ReflexEntity,
    ReflexInjector,
    build_reflex_injector,
    format_pointer_block,
    http_entity_fetcher,
    parse_entities,
    scan_message,
)


def _entity(
    slug: str,
    title: str | None = None,
    *,
    type_: str | None = "decision",
    confidence: str | None = "high",
    updated_at: str = "2026-05-20T10:00:00Z",
    path: str = "",
) -> ReflexEntity:
    return ReflexEntity(
        slug=slug,
        title=title or slug.replace("-", " ").title(),
        type=type_,
        confidence=confidence,
        updated_at=updated_at,
        path=path or f"wiki/technical/{slug}.md",
    )


@pytest.fixture
def index() -> EntityIndex:
    return EntityIndex(
        [
            _entity("volundr-auth", "Volundr Auth", path="wiki/technical/volundr/auth.md"),
            _entity("skuld", "Skuld"),
            _entity("drive-loop", "Drive Loop"),
            _entity("mimir-curator-persona", "Mimir Curator Persona"),
            _entity("karpathy", "Andrej Karpathy", type_="person"),
        ]
    )


class TestParseEntities:
    def test_parses_full_rows(self):
        payload = [
            {
                "slug": "volundr-auth",
                "title": "Volundr Auth",
                "type": "decision",
                "confidence": "high",
                "updated_at": "2026-05-20T10:00:00Z",
                "path": "wiki/technical/volundr/auth.md",
            }
        ]
        entities = parse_entities(payload)
        assert len(entities) == 1
        assert entities[0].slug == "volundr-auth"
        assert entities[0].type == "decision"
        assert entities[0].path == "wiki/technical/volundr/auth.md"

    def test_missing_optionals_default_to_none_or_empty(self):
        entities = parse_entities([{"slug": "s1", "title": "S One"}])
        assert entities[0].type is None
        assert entities[0].confidence is None
        assert entities[0].updated_at == ""
        assert entities[0].path == ""

    def test_slug_falls_back_to_title_and_vice_versa(self):
        entities = parse_entities([{"title": "Only Title"}, {"slug": "only-slug"}])
        assert entities[0].slug == "Only Title"
        assert entities[1].title == "only-slug"

    def test_skips_malformed_rows(self):
        entities = parse_entities([{"slug": "ok", "title": "Ok"}, "garbage", {}, None])
        assert [e.slug for e in entities] == ["ok"]

    def test_non_list_payload_raises(self):
        with pytest.raises(ValueError, match="must be a list"):
            parse_entities({"slug": "not-a-list"})


class TestEntityIndex:
    def test_lookup_by_slug_and_title(self, index: EntityIndex):
        assert index.lookup("volundr-auth").slug == "volundr-auth"
        assert index.lookup("Volundr Auth").slug == "volundr-auth"

    def test_lookup_is_casefolded(self, index: EntityIndex):
        assert index.lookup("VOLUNDR AUTH").slug == "volundr-auth"
        assert index.lookup("volundr auth").slug == "volundr-auth"

    def test_lookup_is_hyphen_space_insensitive(self, index: EntityIndex):
        assert index.lookup("Drive-Loop").slug == "drive-loop"
        assert index.lookup("drive_loop").slug == "drive-loop"

    def test_unknown_term_returns_none(self, index: EntityIndex):
        assert index.lookup("nonexistent") is None
        assert index.lookup("") is None

    def test_first_entity_wins_on_collision(self):
        first = _entity("dup", "Dup")
        second = _entity("dup", "Dup Two")
        idx = EntityIndex([first, second])
        assert idx.lookup("dup") is first

    def test_len_counts_distinct_keys(self, index: EntityIndex):
        assert len(index) > 0
        assert len(EntityIndex([])) == 0

    def test_empty_slug_key_is_skipped(self):
        entity = ReflexEntity(
            slug="",
            title="Valid Title",
            type=None,
            confidence=None,
            updated_at="",
            path="wiki/v.md",
        )
        idx = EntityIndex([entity])
        assert len(idx) == 1
        assert idx.lookup("Valid Title") is entity


class TestScanMessage:
    def test_matches_single_capitalized_word(self, index: EntityIndex):
        matches = scan_message("Please restart Skuld now.", index, 5)
        assert [m.slug for m in matches] == ["skuld"]

    def test_matches_two_word_ngram(self, index: EntityIndex):
        matches = scan_message("Look at Volundr Auth before changing it.", index, 5)
        assert [m.slug for m in matches] == ["volundr-auth"]

    def test_matches_three_word_ngram(self, index: EntityIndex):
        matches = scan_message("Ask the Mimir Curator Persona about it.", index, 5)
        assert "mimir-curator-persona" in [m.slug for m in matches]

    def test_matches_handle(self, index: EntityIndex):
        matches = scan_message("ping @volundr-auth about the rollout", index, 5)
        assert [m.slug for m in matches] == ["volundr-auth"]

    def test_lowercase_mention_not_matched_without_handle(self, index: EntityIndex):
        assert scan_message("the skuld broker is fine", index, 5) == []

    def test_caps_at_max_pointers(self, index: EntityIndex):
        text = "Skuld and Volundr Auth and Drive Loop and Andrej Karpathy"
        matches = scan_message(text, index, 2)
        assert len(matches) == 2

    def test_dedupes_repeated_mentions(self, index: EntityIndex):
        matches = scan_message("Skuld talks to Skuld via Skuld", index, 5)
        assert [m.slug for m in matches] == ["skuld"]

    def test_seen_slugs_are_skipped(self, index: EntityIndex):
        matches = scan_message("Skuld and Drive Loop", index, 5, seen_slugs={"skuld"})
        assert [m.slug for m in matches] == ["drive-loop"]

    def test_no_match_returns_empty(self, index: EntityIndex):
        assert scan_message("Nothing Known Here", index, 5) == []

    def test_empty_text_and_zero_cap(self, index: EntityIndex):
        assert scan_message("", index, 5) == []
        assert scan_message("Skuld", index, 0) == []

    def test_empty_index(self):
        assert scan_message("Skuld", EntityIndex([]), 5) == []

    def test_matches_in_document_order(self, index: EntityIndex):
        matches = scan_message("Drive Loop feeds Skuld", index, 5)
        assert [m.slug for m in matches] == ["drive-loop", "skuld"]


class TestFormatPointerBlock:
    def test_full_block(self):
        entity = _entity(
            "volundr-auth",
            "Volundr Auth",
            path="wiki/technical/volundr/auth.md",
        )
        block = format_pointer_block([entity])
        assert block == (
            f"{POINTER_BLOCK_HEADER}\n"
            "[[volundr-auth]] (decision, high confidence, updated 2026-05-20)"
            " — mimir_read wiki/technical/volundr/auth.md"
        )

    def test_optional_fields_omitted(self):
        entity = ReflexEntity(
            slug="bare",
            title="Bare",
            type=None,
            confidence=None,
            updated_at="",
            path="wiki/bare.md",
        )
        block = format_pointer_block([entity])
        assert block.splitlines()[1] == "[[bare]] — mimir_read wiki/bare.md"

    def test_one_line_per_entity(self):
        block = format_pointer_block([_entity("a"), _entity("b")])
        assert len(block.splitlines()) == 3

    def test_empty_matches(self):
        assert format_pointer_block([]) == ""


class TestEntityIndexCache:
    async def test_caches_within_ttl(self):
        calls = []
        now = [0.0]

        async def fetch():
            calls.append(1)
            return [_entity("skuld", "Skuld")]

        cache = EntityIndexCache(fetch, ttl_seconds=60.0, clock=lambda: now[0])
        first = await cache.get()
        now[0] = 30.0
        second = await cache.get()
        assert first is second
        assert len(calls) == 1

    async def test_refetches_after_ttl(self):
        calls = []
        now = [0.0]

        async def fetch():
            calls.append(1)
            return [_entity("skuld", "Skuld")]

        cache = EntityIndexCache(fetch, ttl_seconds=60.0, clock=lambda: now[0])
        await cache.get()
        now[0] = 61.0
        await cache.get()
        assert len(calls) == 2

    async def test_fetch_error_propagates(self):
        async def fetch():
            raise httpx.ConnectError("mimir down")

        cache = EntityIndexCache(fetch, ttl_seconds=60.0)
        with pytest.raises(httpx.ConnectError):
            await cache.get()


class TestHttpEntityFetcher:
    @respx.mock
    async def test_fetches_and_parses(self):
        respx.get("http://mimir.test/mimir/entities/index").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "slug": "volundr-auth",
                        "title": "Volundr Auth",
                        "type": "decision",
                        "confidence": "high",
                        "updated_at": "2026-05-20T10:00:00Z",
                        "path": "wiki/technical/volundr/auth.md",
                    }
                ],
            )
        )
        fetch = http_entity_fetcher("http://mimir.test/", timeout_seconds=5.0)
        entities = await fetch()
        assert [e.slug for e in entities] == ["volundr-auth"]

    @respx.mock
    async def test_http_error_raises(self):
        respx.get("http://mimir.test/mimir/entities/index").mock(return_value=httpx.Response(500))
        fetch = http_entity_fetcher("http://mimir.test", timeout_seconds=5.0)
        with pytest.raises(httpx.HTTPStatusError):
            await fetch()

    @respx.mock
    async def test_sends_auth_headers(self):
        route = respx.get("http://mimir.test/mimir/entities/index").mock(
            return_value=httpx.Response(200, json=[])
        )
        fetch = http_entity_fetcher(
            "http://mimir.test",
            timeout_seconds=5.0,
            headers={"Authorization": "Bearer token-123"},
        )

        await fetch()

        assert route.calls.last.request.headers["Authorization"] == "Bearer token-123"

    @respx.mock
    async def test_sends_dynamic_auth_headers(self):
        route = respx.get("http://mimir.test/mimir/entities/index").mock(
            return_value=httpx.Response(200, json=[])
        )

        async def headers_provider() -> dict[str, str]:
            return {"Authorization": "Bearer dynamic-token"}

        fetch = http_entity_fetcher(
            "http://mimir.test",
            timeout_seconds=5.0,
            headers_provider=headers_provider,
        )

        await fetch()

        assert route.calls.last.request.headers["Authorization"] == "Bearer dynamic-token"


class TestReflexInjector:
    @staticmethod
    def _injector(entities: list[ReflexEntity] | None = None) -> ReflexInjector:
        feed = (
            entities
            if entities is not None
            else [
                _entity("volundr-auth", "Volundr Auth", path="wiki/technical/volundr/auth.md"),
                _entity("skuld", "Skuld"),
            ]
        )

        async def fetch():
            return feed

        return ReflexInjector(fetch_entities=fetch, max_pointers=5, cache_ttl_seconds=300.0)

    async def test_pointer_block_happy_path(self, caplog):
        injector = self._injector()
        with caplog.at_level(logging.INFO, logger="ravn.reflex"):
            block = await injector.pointer_block("Check Volundr Auth please", "s1")
        assert block is not None
        assert block.startswith(POINTER_BLOCK_HEADER)
        assert "[[volundr-auth]]" in block
        injected = [r for r in caplog.records if INJECTED_LOG_EVENT in r.getMessage()]
        assert len(injected) == 1
        assert "volundr-auth" in injected[0].getMessage()
        assert "s1" in injected[0].getMessage()

    async def test_same_slug_injected_once_per_session(self):
        injector = self._injector()
        first = await injector.pointer_block("Check Volundr Auth", "s1")
        second = await injector.pointer_block("Check Volundr Auth again", "s1")
        assert first is not None
        assert second is None

    async def test_sessions_are_independent(self):
        injector = self._injector()
        assert await injector.pointer_block("Check Volundr Auth", "s1") is not None
        assert await injector.pointer_block("Check Volundr Auth", "s2") is not None

    async def test_no_match_returns_none(self):
        injector = self._injector()
        assert await injector.pointer_block("nothing relevant here", "s1") is None

    async def test_fetch_error_fails_open_with_error_log(self, caplog):
        async def fetch():
            raise httpx.ConnectError("mimir down")

        injector = ReflexInjector(fetch_entities=fetch, max_pointers=5, cache_ttl_seconds=300.0)
        with caplog.at_level(logging.ERROR, logger="ravn.reflex"):
            block = await injector.pointer_block("Check Volundr Auth", "s1")
        assert block is None
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("entity feed unreachable" in r.getMessage() for r in errors)

    async def test_apply_prefixes_block(self):
        injector = self._injector()
        result = await injector.apply("Check Volundr Auth", "s1")
        assert result.startswith(POINTER_BLOCK_HEADER)
        assert result.endswith("Check Volundr Auth")
        assert "\n\n" in result

    async def test_apply_returns_text_unchanged_when_no_match(self):
        injector = self._injector()
        assert await injector.apply("nothing here", "s1") == "nothing here"


class TestBuildReflexInjector:
    def test_disabled_returns_none(self):
        cfg = MimirConfig(reflex=MimirReflexConfig(enabled=False))
        assert build_reflex_injector(cfg) is None

    def test_default_config_is_off(self):
        assert MimirConfig().reflex.enabled is False
        assert build_reflex_injector(MimirConfig()) is None

    def test_missing_reflex_attribute_returns_none(self):
        assert build_reflex_injector(object()) is None
        assert build_reflex_injector(None) is None

    def test_non_boolean_enabled_returns_none(self):
        class Fake:
            reflex = type("R", (), {"enabled": object()})()

        assert build_reflex_injector(Fake()) is None

    def test_enabled_with_base_url(self):
        cfg = MimirConfig(reflex=MimirReflexConfig(enabled=True, base_url="http://mimir.test"))
        assert isinstance(build_reflex_injector(cfg), ReflexInjector)

    def test_enabled_falls_back_to_instance_url(self):
        cfg = MimirConfig(
            reflex=MimirReflexConfig(enabled=True),
            instances=[
                MimirInstanceConfig(name="local", path="/tmp/mimir"),
                MimirInstanceConfig(name="shared", url="http://shared.mimir.test"),
            ],
        )
        assert isinstance(build_reflex_injector(cfg), ReflexInjector)

    @respx.mock
    async def test_instance_auth_token_file_is_used_for_entity_feed(self, tmp_path):
        token_file = tmp_path / "mimir-token"
        token_file.write_text("token-from-file\n", encoding="utf-8")
        route = respx.get("http://shared.mimir.test/mimir/entities/index").mock(
            return_value=httpx.Response(200, json=[])
        )
        cfg = MimirConfig(
            reflex=MimirReflexConfig(enabled=True),
            instances=[
                MimirInstanceConfig(
                    name="shared",
                    url="http://shared.mimir.test",
                    auth=MimirAuthConfig(type="bearer", token_file=str(token_file)),
                ),
            ],
        )

        injector = build_reflex_injector(cfg)
        assert injector is not None
        await injector.pointer_block("No matches", "session-1")

        assert route.calls.last.request.headers["Authorization"] == "Bearer token-from-file"

    @respx.mock
    async def test_instance_workload_auth_is_used_for_entity_feed(self, tmp_path):
        token_file = tmp_path / "workload-token"
        token_file.write_text("service-account-token\n", encoding="utf-8")
        exchange = respx.post("http://volundr.test/api/v1/tokens/workload/exchange").mock(
            return_value=httpx.Response(
                200,
                json={"token": "workload-jwt", "expiresAt": 4_000_000_000},
            )
        )
        route = respx.get("http://shared.mimir.test/mimir/entities/index").mock(
            return_value=httpx.Response(200, json=[])
        )
        cfg = MimirConfig(
            reflex=MimirReflexConfig(enabled=True),
            instances=[
                MimirInstanceConfig(
                    name="shared",
                    url="http://shared.mimir.test",
                    auth=MimirAuthConfig(
                        type="workload",
                        token_file=str(token_file),
                        exchange_url="http://volundr.test/api/v1/tokens/workload/exchange",
                        audiences=["mimir"],
                    ),
                ),
            ],
        )

        injector = build_reflex_injector(cfg)
        assert injector is not None
        await injector.pointer_block("No matches", "session-1")

        assert exchange.calls.last.request.content == (
            b'{"token":"service-account-token","audiences":["mimir"]}'
        )
        assert route.calls.last.request.headers["Authorization"] == "Bearer workload-jwt"

    def test_enabled_without_any_url_logs_warning_and_returns_none(self, caplog):
        cfg = MimirConfig(reflex=MimirReflexConfig(enabled=True))
        with caplog.at_level(logging.WARNING, logger="ravn.reflex"):
            assert build_reflex_injector(cfg) is None
        assert any("no Mimir HTTP endpoint" in r.getMessage() for r in caplog.records)


class TestScanBenchmark:
    def test_scan_500_words_against_1000_entities_under_50ms(self):
        entities = [
            _entity(f"entity-{i}", f"Entity Word{i}", path=f"wiki/e/{i}.md") for i in range(1000)
        ]
        index = EntityIndex(entities)
        words = []
        for i in range(250):
            words.append(f"Entity Word{i * 4}")  # two words, some match
            words.append("filler" if i % 2 else "lowercase")
        message = " ".join(words)
        assert len(message.split()) >= 500

        started = time.perf_counter()
        matches = scan_message(message, index, 5)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        print(f"\nreflex scan benchmark: {elapsed_ms:.2f}ms for 500 words x 1000 entities")
        assert matches, "benchmark message should produce matches"
        assert elapsed_ms < 50.0, f"scan took {elapsed_ms:.2f}ms (budget 50ms)"
