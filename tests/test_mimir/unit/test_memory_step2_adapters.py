"""Memory step 2 adapters: Forge's people answers, Bifröst, and attached Mímir stores."""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from mimir.adapters.bifrost_answers import BifrostAnswerModel
from mimir.adapters.forge_people import ForgePeopleDirectory
from mimir.backends.mimir_port import MimirPortBackend
from mimir.memory.models import (
    Attribution,
    CapabilityMissingError,
    Evidence,
    HitKind,
    MemoryBackendError,
    MemoryValidationError,
    NewTimelineRow,
    PageWrite,
    RecordNotFoundError,
    RevisionConflictError,
)
from mimir.ports.answer_model import AnswerModelError
from mimir.ports.people_directory import (
    ForgeUnavailableError,
    NotVisibleError,
    Person,
    PersonRefusedError,
)
from niuu.domain.mimir_facts import TrustLevel
from tests.test_mimir.unit.fakes import InMemoryMimirAdapter

FORGE = "http://forge.test"
BIFROST = "http://bifrost.test"
PERSON = Person("ines", "acme", ("volundr:developer",), authorization="Bearer login")
NS = "attached-notes"


# ---------------------------------------------------------------------------
# Forge: roles and owners
# ---------------------------------------------------------------------------


@pytest.fixture
async def people():
    directory = ForgePeopleDirectory(FORGE + "/", timeout_seconds=5)
    yield directory
    await directory.close()


class TestForgePeopleDirectory:
    @respx.mock
    async def test_a_role_is_asked_with_the_persons_login(self, people) -> None:
        route = respx.get(f"{FORGE}/api/v1/forge/projects/p1/role").mock(
            return_value=httpx.Response(200, json={"project_id": "p1", "role": "approver"})
        )
        assert await people.project_role("p1", PERSON) == "approver"
        assert route.calls.last.request.headers["authorization"] == "Bearer login"

    @respx.mock
    async def test_mini_mode_sends_no_authorization(self, people) -> None:
        route = respx.get(f"{FORGE}/api/v1/forge/sessions/s1").mock(
            return_value=httpx.Response(200, json={"owner_id": "ines", "tenant_id": "acme"})
        )
        owned = await people.session_owner("s1", Person("dev-user", "default", ()))
        assert (owned.owner_id, owned.tenant_id) == ("ines", "acme")
        assert "authorization" not in route.calls.last.request.headers

    @respx.mock
    async def test_a_runs_owner_and_sessions(self, people) -> None:
        respx.get(f"{FORGE}/api/v1/forge/memory/runs/r1").mock(
            return_value=httpx.Response(
                200, json={"owner_id": "ines", "tenant_id": "acme", "session_ids": ["s1", "s2"]}
            )
        )
        owned = await people.run_owner("r1", PERSON)
        assert owned.session_ids == ("s1", "s2")

    @pytest.mark.parametrize(
        ("response", "error"),
        [
            (httpx.Response(404), NotVisibleError),
            (httpx.Response(401), PersonRefusedError),
            (httpx.Response(403), PersonRefusedError),
            (httpx.Response(500), ForgeUnavailableError),
            (httpx.Response(200, text="not json"), ForgeUnavailableError),
            (httpx.Response(200, json=["a"]), ForgeUnavailableError),
            (httpx.Response(200, json={"role": "boss"}), ForgeUnavailableError),
        ],
    )
    @respx.mock
    async def test_every_other_answer_is_typed(self, people, response, error) -> None:
        respx.get(f"{FORGE}/api/v1/forge/projects/p1/role").mock(return_value=response)
        with pytest.raises(error):
            await people.project_role("p1", PERSON)

    @respx.mock
    async def test_answers_missing_what_is_needed_are_unusable(self, people) -> None:
        respx.get(f"{FORGE}/api/v1/forge/sessions/s1").mock(
            return_value=httpx.Response(200, json={"owner_id": "ines"})
        )
        respx.get(f"{FORGE}/api/v1/forge/memory/runs/r1").mock(
            return_value=httpx.Response(200, json={"owner_id": "ines", "tenant_id": "acme"})
        )
        with pytest.raises(ForgeUnavailableError, match="tenant_id"):
            await people.session_owner("s1", PERSON)
        with pytest.raises(ForgeUnavailableError, match="session_ids"):
            await people.run_owner("r1", PERSON)

    @respx.mock
    async def test_an_unreachable_forge_is_unavailable(self, people) -> None:
        respx.get(f"{FORGE}/api/v1/forge/projects/p1/role").mock(
            side_effect=httpx.ConnectError("refused")
        )
        with pytest.raises(ForgeUnavailableError, match="could not be asked"):
            await people.project_role("p1", PERSON)

    async def test_an_id_that_is_not_one_never_reaches_forge(self, people) -> None:
        with pytest.raises(NotVisibleError):
            await people.project_role("../admin", PERSON)

    def test_settings_are_checked(self) -> None:
        with pytest.raises(ValueError, match="forge_base_url"):
            ForgePeopleDirectory("", timeout_seconds=1)
        with pytest.raises(ValueError, match="timeout"):
            ForgePeopleDirectory(FORGE, timeout_seconds=0)


# ---------------------------------------------------------------------------
# Bifröst
# ---------------------------------------------------------------------------


@pytest.fixture
async def bifrost():
    model = BifrostAnswerModel(BIFROST + "/", timeout_seconds=5)
    yield model
    await model.close()


class TestBifrostAnswerModel:
    @pytest.mark.parametrize(
        ("response", "match"),
        [
            (httpx.Response(500, text="provider down"), "provider down"),
            (httpx.Response(200, text="not json"), "not JSON"),
            (httpx.Response(200, json={"content": "text"}), "no content blocks"),
        ],
    )
    @respx.mock
    async def test_an_unusable_answer_is_an_error(self, bifrost, response, match) -> None:
        respx.post(f"{BIFROST}/v1/messages").mock(return_value=response)
        with pytest.raises(AnswerModelError, match=match):
            await bifrost.answer(model="m", system="s", prompt="p", credential="t")

    @respx.mock
    async def test_an_unreachable_bifrost_is_an_error(self, bifrost) -> None:
        respx.post(f"{BIFROST}/v1/messages").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(AnswerModelError, match="could not be reached"):
            await bifrost.answer(model="m", system="s", prompt="p", credential="t")

    @respx.mock
    async def test_tool_use_blocks_are_kept(self, bifrost) -> None:
        respx.post(f"{BIFROST}/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "content": [{"type": "tool_use", "id": "x"}, {"type": "text", "text": "hi"}],
                    "stop_reason": "tool_use",
                },
            )
        )
        reply = await bifrost.answer(model="m", system="s", prompt="p", credential="t")
        assert reply.content == "hi" and len(reply.tool_calls) == 1

    def test_settings_are_checked(self) -> None:
        with pytest.raises(ValueError, match="bifrost_url"):
            BifrostAnswerModel("", timeout_seconds=1)
        with pytest.raises(ValueError, match="timeout"):
            BifrostAnswerModel(BIFROST, timeout_seconds=0)


# ---------------------------------------------------------------------------
# An attached Mímir store
# ---------------------------------------------------------------------------


def _by(author: str = "agent:session:s1") -> Attribution:
    return Attribution(
        author,
        TrustLevel.AGENT,
        (Evidence.parse("url:https://example.test/1"),),
        datetime(2026, 11, 12, tzinfo=UTC),
    )


@pytest.fixture
def store() -> InMemoryMimirAdapter:
    return InMemoryMimirAdapter()


@pytest.fixture
def backend(store) -> MimirPortBackend:
    return MimirPortBackend(store, namespace=NS, label="notes")


class TestMimirPortBackend:
    async def test_a_page_round_trips_unattested_with_its_writer_on_the_timeline(
        self, backend
    ) -> None:
        written = await backend.put_page(
            NS,
            "notes/a",
            PageWrite("A is true.", title="A", page_type="concept"),
            _by(),
            expected_revision=None,
        )
        assert written.compiled_truth == "A is true." and written.title == "A"
        assert not written.attribution.attested
        assert written.attribution.author == "external:attached:notes"
        assert "agent:session:s1 (agent)" in written.timeline[-1].detail

    async def test_create_and_rewrite_check_the_revision(self, backend) -> None:
        with pytest.raises(RecordNotFoundError):
            await backend.put_page(NS, "notes/a", PageWrite("x"), _by(), expected_revision="r")
        first = await backend.put_page(NS, "notes/a", PageWrite("x"), _by(), expected_revision=None)
        with pytest.raises(RevisionConflictError, match="already exists"):
            await backend.put_page(NS, "notes/a", PageWrite("y"), _by(), expected_revision=None)
        second = await backend.put_page(
            NS, "notes/a", PageWrite("y"), _by(), expected_revision=first.revision
        )
        assert second.compiled_truth == "y" and len(second.timeline) == 2

    async def test_a_timeline_row_needs_the_page(self, backend) -> None:
        row = NewTimelineRow(date(2026, 11, 12), "seen", (Evidence.parse("url:https://x.test"),))
        with pytest.raises(RecordNotFoundError):
            await backend.append_timeline(NS, "notes/missing", row, _by())

    async def test_list_delete_backlinks_and_search(self, backend, store) -> None:
        await backend.put_page(
            NS, "notes/a", PageWrite("see [[notes/b]]"), _by(), expected_revision=None
        )
        await backend.put_page(NS, "notes/b", PageWrite("bee"), _by(), expected_revision=None)
        assert [p.slug for p in await backend.list_pages(NS, limit=1)] == ["notes/a"]
        assert isinstance(await backend.backlinks(NS, "notes/b"), list)
        hits = await backend.search([NS], "bee", kinds=frozenset(HitKind), limit=5)
        assert [h.ref for h in hits] == ["notes/b"]
        assert await backend.search([NS], "bee", kinds=frozenset({HitKind.SOURCE}), limit=5) == []
        with pytest.raises(MemoryValidationError):
            await backend.search([NS], " ", kinds=frozenset(HitKind), limit=5)
        await backend.delete_page(NS, "notes/b")
        with pytest.raises(RecordNotFoundError):
            await backend.delete_page(NS, "notes/b")

    async def test_a_store_failure_is_a_backend_error_never_empty(self, backend, store) -> None:
        async def broken(query: str):
            raise httpx.ConnectError("down")

        store.search = broken  # type: ignore[method-assign]
        with pytest.raises(MemoryBackendError, match="ConnectError"):
            await backend.search([NS], "x", kinds=frozenset(HitKind), limit=5)

    @pytest.mark.parametrize(
        "call",
        [
            lambda b: b.put_source(NS, None, None),
            lambda b: b.list_sources(NS, limit=1),
            lambda b: b.remember(NS, None),
            lambda b: b.get_fact("f"),
            lambda b: b.retire(NS, "f", by="x", why="y"),
            lambda b: b.entity(NS, "x"),
            lambda b: b.delta([NS], since="x", limit=1),
            lambda b: b.create_namespace(NS),
            lambda b: b.delete_namespace(NS),
            lambda b: b.list_namespaces(),
        ],
    )
    async def test_what_it_does_not_hold_is_named(self, backend, call) -> None:
        with pytest.raises(CapabilityMissingError, match="attached memory notes"):
            await call(backend)

    async def test_close_reaches_the_store(self) -> None:
        closed = []

        class Closing(InMemoryMimirAdapter):
            async def aclose(self) -> None:
                closed.append(True)

        await MimirPortBackend(Closing(), namespace=NS, label="notes").close()
        assert closed == [True]
