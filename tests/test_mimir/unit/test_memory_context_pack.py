"""The launch context pack (contract C9)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
import respx

from mimir.memory.context_pack import ContextPackBuilder
from mimir.memory.gateway import MemoryGateway
from mimir.memory.models import Attribution, Evidence, PageWrite
from mimir.memory.systems import MemorySystems
from niuu.cap_telemetry import cap_hit_counts, reset_cap_hits
from niuu.domain.mimir_facts import TrustLevel
from tests.test_mimir.unit.memory_fakes import (
    PROJECT_ID,
    FakeGBrain,
    context,
    gbrain_backend,
)

URL = "url:https://linear.app/acme/LIN-482"
PROJECT = f"project:{PROJECT_ID}"


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


@pytest.fixture
def services():
    brain = FakeGBrain()
    with respx.mock(assert_all_called=False) as router:
        brain.mount(router)
        yield brain, router


@pytest.fixture
async def gateway(services):
    systems = MemorySystems({"acme-brain": gbrain_backend()}, {"acme": "acme-brain"})
    yield MemoryGateway(systems, default_result_count=10)
    await systems.close()


async def _seed(session, slug: str, text: str, layer: str = PROJECT) -> None:
    await session.backend.put_page(
        session.view.layer(layer).namespace,
        slug,
        PageWrite(text, title=slug),
        Attribution(
            "operator:owner", TrustLevel.OPERATOR, (Evidence.parse(URL),), datetime.now(UTC)
        ),
        expected_revision=None,
    )


class TestContextPack:
    async def test_it_holds_the_objectives_entities_and_makes_no_catalog_call(
        self, services, gateway
    ) -> None:
        _, router = services
        ctx = replace(context(), objective="change the booking cut-over plan")
        session = await gateway.open(ctx)
        await _seed(session, "plans/booking-cutover", "The cut-over runs on Friday.")
        pack = await ContextPackBuilder(gateway).context_pack(session)
        assert "plans/booking-cutover" in pack.text and "Friday" in pack.text
        assert not router.calls or all(
            "/models/" not in str(call.request.url) for call in router.calls
        )

    async def test_a_large_page_is_kept_whole_and_nothing_is_counted_as_left_out(
        self, services, gateway
    ) -> None:
        ctx = replace(context(), objective="booking cut-over")
        session = await gateway.open(ctx)
        body = "booking cut-over " + "x" * 20_000
        await _seed(session, "plans/booking-cutover", body)
        await _seed(session, "people/ines", "Ines owns the booking cut-over.")
        pack = await ContextPackBuilder(gateway).context_pack(session)
        assert body in pack.text
        assert "people/ines" in pack.text
        assert "did not fit" not in pack.text
        assert cap_hit_counts() == {}

    async def test_a_session_with_nothing_to_lead_to_gets_just_the_header(
        self, services, gateway
    ) -> None:
        session = await gateway.open(context())
        pack = await ContextPackBuilder(gateway).context_pack(session)
        assert pack.text.startswith("Memory context for this session")
        assert "##" not in pack.text

    async def test_changes_since_the_owners_last_session_are_listed(
        self, services, gateway
    ) -> None:
        session = await gateway.open(context())
        await _seed(session, "runbooks/deploy", "Deploy with the blue/green script.")
        since = datetime(2026, 1, 1, tzinfo=UTC)
        session = await gateway.open(replace(context(), since=since))
        pack = await ContextPackBuilder(gateway).context_pack(session)
        assert f"changed [{PROJECT}] runbooks/deploy" in pack.text

    async def test_every_page_the_objective_leads_to_is_considered_and_nothing_is_counted(
        self, services, gateway
    ) -> None:
        brain, _ = services
        brain.search_limit = 1000  # what gbrain's own search would return
        ctx = replace(context(), objective="booking cut-over")
        session = await gateway.open(ctx)
        for index in range(25):
            await _seed(session, f"plans/booking-{index:02d}", "booking cut-over notes")
        pack = await ContextPackBuilder(gateway).context_pack(session)
        assert all(f"plans/booking-{index:02d}" in pack.text for index in range(25))
        assert cap_hit_counts() == {}
