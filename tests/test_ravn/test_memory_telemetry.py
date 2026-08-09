"""Telemetry for Ravn's three memory surfaces.

These tests pin the behaviour that was missing when episodic prefetch was
found returning nothing for every query on a 3,321-episode store: retrieval
succeeded, domain scoring discarded every candidate, and no signal
distinguished that from an empty corpus. The funnel counters are what make the
two cases distinguishable, so they are asserted directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from niuu.ports.search import SearchResult
from ravn import memory_telemetry
from ravn.adapters.memory.scoring import combined_score, score_and_admit
from ravn.adapters.memory.sqlite import SqliteMemoryAdapter
from ravn.adapters.mimir.http import HttpMimirAdapter
from ravn.adapters.resident_state import select_resident_state
from ravn.domain.models import Episode, Outcome

_MIMIR_URL = "http://mimir.test"


class RecordingTelemetry:
    """Captures counter and histogram emissions instead of exporting them."""

    def __init__(self) -> None:
        self.counts: list[tuple[str, int, dict]] = []
        self.records: list[tuple[str, float, dict]] = []
        self.gauges: list[tuple[str, float, dict]] = []

    def count(self, name, value=1, *, attributes=None, description="") -> None:
        self.counts.append((name, value, attributes or {}))

    def record(self, name, value, *, unit="1", attributes=None, description="") -> None:
        self.records.append((name, value, attributes or {}))

    def duration(self, name, seconds, *, attributes=None, description="") -> None:
        self.records.append((name, seconds, attributes or {}))

    def gauge(self, name, value, *, attributes=None, description="") -> None:
        self.gauges.append((name, value, attributes or {}))

    def total(self, metric: str) -> int:
        return sum(value for name, value, _ in self.counts if name == metric)

    def values(self, metric: str) -> list[float]:
        return [value for name, value, _ in self.records if name == metric]

    def attributes_for(self, metric: str) -> list[dict]:
        return [attrs for name, _, attrs in self.counts if name == metric]


@pytest.fixture
def telemetry(monkeypatch: pytest.MonkeyPatch) -> RecordingTelemetry:
    recorder = RecordingTelemetry()
    monkeypatch.setattr(memory_telemetry, "get_observability", lambda: recorder)
    return recorder


def _episode(episode_id: str, *, age_days: float, outcome: Outcome = Outcome.SUCCESS) -> Episode:
    return Episode(
        episode_id=episode_id,
        session_id="session-1",
        timestamp=datetime.now(UTC) - timedelta(days=age_days),
        summary="pool size raised to 20",
        task_description="size the auxiliary postgres pools",
        tools_used=["edit"],
        outcome=outcome,
        tags=["ravn"],
    )


class TestRetrievalFunnel:
    def test_candidates_and_admitted_diverge_when_the_gate_culls(
        self, telemetry: RecordingTelemetry
    ) -> None:
        """The exact production failure: strong search hits, nothing admitted.

        Old episodes carry a recency multiplier small enough to push a perfect
        relevance score under the gate. Without both counters this is
        indistinguishable from an empty corpus.
        """
        episodes = {f"e{i}": _episode(f"e{i}", age_days=120) for i in range(5)}
        results = [SearchResult(id=f"e{i}", content="c", score=1.0) for i in range(5)]

        matches = score_and_admit(
            results,
            episodes,
            half_life_days=14.0,
            min_relevance=0.3,
            limit=5,
            backend="sqlite",
        )

        assert matches == []
        assert telemetry.total(memory_telemetry.CANDIDATES) == 5
        assert telemetry.total(memory_telemetry.ADMITTED) == 0

    def test_every_candidate_score_is_reported_including_rejected_ones(
        self, telemetry: RecordingTelemetry
    ) -> None:
        """A corpus scoring just under the gate must be visible, not silent."""
        episodes = {f"e{i}": _episode(f"e{i}", age_days=120) for i in range(3)}
        results = [SearchResult(id=f"e{i}", content="c", score=1.0) for i in range(3)]

        score_and_admit(
            results,
            episodes,
            half_life_days=14.0,
            min_relevance=0.3,
            limit=5,
            backend="sqlite",
        )

        scores = telemetry.values(memory_telemetry.RELEVANCE_SCORE)
        assert len(scores) == 3
        assert all(0.0 < score < 0.3 for score in scores)

    def test_top_candidate_age_is_reported(self, telemetry: RecordingTelemetry) -> None:
        """Candidate age localises a recency-decay cull without reading code."""
        episodes = {"old": _episode("old", age_days=90), "new": _episode("new", age_days=1)}
        results = [
            SearchResult(id="old", content="c", score=1.0),
            SearchResult(id="new", content="c", score=0.9),
        ]

        score_and_admit(
            results,
            episodes,
            half_life_days=14.0,
            min_relevance=0.0,
            limit=5,
            backend="sqlite",
        )

        ages = telemetry.values(memory_telemetry.CANDIDATE_AGE_DAYS)
        assert len(ages) == 1
        assert ages[0] == pytest.approx(1.0, abs=0.1)

    def test_fresh_relevant_episodes_are_admitted(self, telemetry: RecordingTelemetry) -> None:
        episodes = {"fresh": _episode("fresh", age_days=1)}
        results = [SearchResult(id="fresh", content="c", score=1.0)]

        matches = score_and_admit(
            results,
            episodes,
            half_life_days=14.0,
            min_relevance=0.3,
            limit=5,
            backend="sqlite",
        )

        assert len(matches) == 1
        assert telemetry.total(memory_telemetry.CANDIDATES) == 1
        assert telemetry.total(memory_telemetry.ADMITTED) == 1


class TestSqliteAdapterOperations:
    async def test_prefetch_reports_empty_and_injects_nothing(
        self, telemetry: RecordingTelemetry, tmp_path
    ) -> None:
        adapter = SqliteMemoryAdapter(path=str(tmp_path / "memory.db"))
        await adapter.initialize()

        block = await adapter.prefetch("anything at all")

        assert block == ""
        results = {
            attrs[memory_telemetry.ATTR_OPERATION]: attrs[memory_telemetry.ATTR_RESULT]
            for attrs in telemetry.attributes_for(memory_telemetry.OPERATIONS)
        }
        assert results["prefetch"] == memory_telemetry.RESULT_EMPTY
        assert telemetry.values(memory_telemetry.INJECTED_CHARS) == [0]

    async def test_prefetch_reports_a_hit_and_the_injected_size(
        self, telemetry: RecordingTelemetry, tmp_path
    ) -> None:
        adapter = SqliteMemoryAdapter(path=str(tmp_path / "memory.db"))
        await adapter.initialize()
        await adapter.record_episode(_episode("fresh", age_days=0.1))

        block = await adapter.prefetch("size the auxiliary postgres pools")

        assert block != ""
        injected = telemetry.values(memory_telemetry.INJECTED_CHARS)
        assert injected[-1] == len(block)
        results = [
            attrs[memory_telemetry.ATTR_RESULT]
            for attrs in telemetry.attributes_for(memory_telemetry.OPERATIONS)
            if attrs[memory_telemetry.ATTR_OPERATION] == "prefetch"
        ]
        assert results == [memory_telemetry.RESULT_HIT]

    async def test_corpus_gauges_expose_missing_embeddings(
        self, telemetry: RecordingTelemetry, tmp_path
    ) -> None:
        """Embedding coverage of 0 is the state the live store was found in."""
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            corpus_stats_interval_seconds=0.0001,
        )
        await adapter.initialize()
        await adapter.record_episode(_episode("one", age_days=1))

        coverage = {
            name: value
            for name, value, _ in telemetry.gauges
            if name == memory_telemetry.CORPUS_EMBEDDING_COVERAGE
        }
        assert coverage[memory_telemetry.CORPUS_EMBEDDING_COVERAGE] == 0.0
        episodes = [
            value for name, value, _ in telemetry.gauges if name == memory_telemetry.CORPUS_EPISODES
        ]
        assert episodes[-1] == 1

    async def test_corpus_sampling_respects_its_interval(
        self, telemetry: RecordingTelemetry, tmp_path
    ) -> None:
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            corpus_stats_interval_seconds=3600.0,
        )
        await adapter.initialize()
        for index in range(3):
            await adapter.record_episode(_episode(f"e{index}", age_days=1))

        samples = [
            name for name, _, _ in telemetry.gauges if name == memory_telemetry.CORPUS_EPISODES
        ]
        assert len(samples) == 1


class TestResidentStateSelection:
    class _Adapter:
        def __init__(self, available: bool) -> None:
            self._available = available

        async def available(self) -> bool:
            return self._available

    async def test_preferred_adapter_records_no_fallback(
        self, telemetry: RecordingTelemetry
    ) -> None:
        preferred = self._Adapter(available=True)

        selected = await select_resident_state(preferred, self._Adapter(available=True))

        assert selected is preferred
        assert telemetry.total(memory_telemetry.RESIDENT_STATE_FALLBACK) == 0

    async def test_falling_back_is_counted(self, telemetry: RecordingTelemetry) -> None:
        """A resident silently demoted to its local store must be visible."""
        fallback = self._Adapter(available=True)

        selected = await select_resident_state(self._Adapter(available=False), fallback)

        assert selected is fallback
        assert telemetry.total(memory_telemetry.RESIDENT_STATE_FALLBACK) == 1

    async def test_no_available_adapter_still_fails_loudly(
        self, telemetry: RecordingTelemetry
    ) -> None:
        with pytest.raises(RuntimeError):
            await select_resident_state(self._Adapter(available=False))


class TestMimirOperations:
    """Mímir is reached over HTTP, so it has a failure mode the others lack:
    a call that succeeds at the transport level and returns nothing."""

    @respx.mock
    async def test_search_returning_nothing_is_reported_as_empty(
        self, telemetry: RecordingTelemetry
    ) -> None:
        respx.get(f"{_MIMIR_URL}/mimir/search").mock(return_value=httpx.Response(200, json=[]))
        adapter = HttpMimirAdapter(base_url=_MIMIR_URL)

        pages = await adapter.search("anything")

        assert pages == []
        results = [
            attrs[memory_telemetry.ATTR_RESULT]
            for attrs in telemetry.attributes_for(memory_telemetry.MIMIR_OPERATIONS)
            if attrs.get(memory_telemetry.ATTR_MIMIR_OPERATION) == "search"
        ]
        assert results == [memory_telemetry.RESULT_EMPTY]
        assert telemetry.values(memory_telemetry.MIMIR_RESULTS) == [0]
        await adapter.aclose()

    @respx.mock
    async def test_search_returning_pages_is_reported_as_a_hit(
        self, telemetry: RecordingTelemetry
    ) -> None:
        respx.get(f"{_MIMIR_URL}/mimir/search").mock(
            return_value=httpx.Response(
                200,
                json=[{"path": "wiki/entities/ravn.md", "title": "Ravn", "summary": "s"}],
            )
        )
        adapter = HttpMimirAdapter(base_url=_MIMIR_URL)

        pages = await adapter.search("ravn")

        assert len(pages) == 1
        assert telemetry.values(memory_telemetry.MIMIR_RESULTS) == [1]
        await adapter.aclose()

    @respx.mock
    async def test_transport_failure_is_counted_as_an_error(
        self, telemetry: RecordingTelemetry
    ) -> None:
        """An unreachable knowledge base must not look like an empty one."""
        respx.get(f"{_MIMIR_URL}/mimir/search").mock(side_effect=httpx.ConnectError("unreachable"))
        adapter = HttpMimirAdapter(base_url=_MIMIR_URL)

        with pytest.raises(httpx.ConnectError):
            await adapter.search("ravn")

        results = [
            attrs[memory_telemetry.ATTR_RESULT]
            for attrs in telemetry.attributes_for(memory_telemetry.MIMIR_OPERATIONS)
        ]
        assert memory_telemetry.RESULT_ERROR in results
        await adapter.aclose()

    @respx.mock
    async def test_an_error_status_is_reported_as_an_error(
        self, telemetry: RecordingTelemetry
    ) -> None:
        respx.get(f"{_MIMIR_URL}/mimir/search").mock(return_value=httpx.Response(503))
        adapter = HttpMimirAdapter(base_url=_MIMIR_URL)

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.search("ravn")

        results = [
            attrs[memory_telemetry.ATTR_RESULT]
            for attrs in telemetry.attributes_for(memory_telemetry.MIMIR_OPERATIONS)
        ]
        assert results == [memory_telemetry.RESULT_ERROR]
        await adapter.aclose()


class TestRecencyFloor:
    """The floor turns recency back into a ranking signal.

    Multiplied into the combined score without a lower bound, exponential
    decay stops ordering results and starts filtering them: at a 14-day
    half-life and a 0.3 gate, nothing older than ~24 days can be admitted at
    any relevance. That is what left a 3,321-episode store returning nothing.
    """

    def test_old_relevant_episodes_are_admitted_with_a_floor(
        self, telemetry: RecordingTelemetry
    ) -> None:
        episodes = {"old": _episode("old", age_days=120)}
        results = [SearchResult(id="old", content="c", score=1.0)]

        matches = score_and_admit(
            results,
            episodes,
            half_life_days=14.0,
            min_relevance=0.3,
            limit=5,
            backend="sqlite",
            recency_floor=0.5,
        )

        assert len(matches) == 1
        assert telemetry.total(memory_telemetry.ADMITTED) == 1

    def test_a_zero_floor_reproduces_the_old_age_ceiling(
        self, telemetry: RecordingTelemetry
    ) -> None:
        """Explicitly opting out restores the previous behaviour."""
        episodes = {"old": _episode("old", age_days=120)}
        results = [SearchResult(id="old", content="c", score=1.0)]

        matches = score_and_admit(
            results,
            episodes,
            half_life_days=14.0,
            min_relevance=0.3,
            limit=5,
            backend="sqlite",
            recency_floor=0.0,
        )

        assert matches == []

    def test_recency_still_orders_results(self) -> None:
        """A floor must not flatten ordering — fresh still outranks old."""
        fresh = combined_score(1.0, _episode("f", age_days=1).timestamp, Outcome.SUCCESS, 14.0, 0.5)
        old = combined_score(1.0, _episode("o", age_days=365).timestamp, Outcome.SUCCESS, 14.0, 0.5)

        assert fresh > old
        assert old == pytest.approx(0.5, abs=1e-6)

    def test_relevance_still_gates_irrelevant_old_episodes(self) -> None:
        """The floor lifts recency, not relevance: weak matches stay out.

        This is the guard against the floor trading "injects nothing" for
        "injects stale noise".
        """
        weak = combined_score(
            0.2, _episode("o", age_days=365).timestamp, Outcome.SUCCESS, 14.0, 0.5
        )

        assert weak < 0.3

    def test_failed_episodes_are_still_down_weighted(self) -> None:
        success = combined_score(
            1.0, _episode("s", age_days=365).timestamp, Outcome.SUCCESS, 14.0, 0.5
        )
        failure = combined_score(
            1.0, _episode("f", age_days=365).timestamp, Outcome.FAILURE, 14.0, 0.5
        )

        assert failure < success


class TestEnvironmentIdentity:
    """Metrics must carry the label the dashboard's environment picker filters on.

    A Mimir tenant can hold more than one resident. Without ``ravn.environment.id``
    the memory row silently blends them, which is exactly the failure mode this
    instrumentation exists to prevent.
    """

    def test_funnel_carries_the_environment_id(self, telemetry: RecordingTelemetry) -> None:
        episodes = {"fresh": _episode("fresh", age_days=1)}
        results = [SearchResult(id="fresh", content="c", score=1.0)]

        score_and_admit(
            results,
            episodes,
            half_life_days=14.0,
            min_relevance=0.3,
            limit=5,
            backend="sqlite",
            environment_id="muninn",
        )

        attrs = telemetry.attributes_for(memory_telemetry.CANDIDATES)
        assert attrs and all(a[memory_telemetry.ATTR_ENVIRONMENT] == "muninn" for a in attrs)

    async def test_adapter_stamps_every_memory_metric(
        self, telemetry: RecordingTelemetry, tmp_path
    ) -> None:
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            environment_id="noatun",
            corpus_stats_interval_seconds=0.0001,
        )
        await adapter.initialize()
        await adapter.record_episode(_episode("fresh", age_days=0.1))
        await adapter.prefetch("size the auxiliary postgres pools")

        for metric in (memory_telemetry.OPERATIONS, memory_telemetry.CANDIDATES):
            attrs = telemetry.attributes_for(metric)
            assert attrs, f"{metric} never emitted"
            assert all(a.get(memory_telemetry.ATTR_ENVIRONMENT) == "noatun" for a in attrs)
        gauges = [a for n, _, a in telemetry.gauges if n == memory_telemetry.CORPUS_EPISODES]
        assert gauges and gauges[0][memory_telemetry.ATTR_ENVIRONMENT] == "noatun"

    def test_the_label_is_omitted_when_unset(self, telemetry: RecordingTelemetry) -> None:
        """An unset id must not produce an empty-string label on the series."""
        memory_telemetry.record_memory_operation(
            operation="query",
            backend="sqlite",
            result=memory_telemetry.RESULT_HIT,
        )

        attrs = telemetry.attributes_for(memory_telemetry.OPERATIONS)[0]
        assert memory_telemetry.ATTR_ENVIRONMENT not in attrs

    async def test_resident_state_fallback_carries_the_environment_id(
        self, telemetry: RecordingTelemetry
    ) -> None:
        class _Adapter:
            def __init__(self, available: bool) -> None:
                self._available = available

            async def available(self) -> bool:
                return self._available

        await select_resident_state(
            _Adapter(available=False), _Adapter(available=True), environment_id="regin"
        )

        attrs = telemetry.attributes_for(memory_telemetry.RESIDENT_STATE_FALLBACK)
        assert attrs and attrs[0][memory_telemetry.ATTR_ENVIRONMENT] == "regin"
