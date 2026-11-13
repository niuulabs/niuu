"""Memory step 2: the run layer and attached layers (design section 4, contract C4/C9).

Run layers live in the org's gbrain (FakeGBrain). Attached layers are registered
Mímir stores (InMemoryMimirAdapter) opened from the gateway's registry.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from mimir.adapters.attached_registry import RegistryAttachedMemories
from mimir.adapters.bifrost_answers import BifrostAnswerModel
from mimir.backends.mimir_port import MimirPortBackend
from mimir.memory.answers import MemoryAnswers
from mimir.memory.gateway import LayerUnavailableError, MemoryGateway
from mimir.memory.models import (
    Attachment,
    AttachmentAccess,
    CapabilityMissingError,
    ChangeAction,
    Evidence,
    Layer,
    LayerKind,
    MemoryValidationError,
    RevisionConflictError,
)
from mimir.memory.rules import Rule, RuleRefusalError, SessionView, Verdict
from mimir.memory.systems import MemorySystems
from mimir.memory.tools import MemoryTools, instructions
from mimir.ports.answer_model import ModelCaller
from mimir.ports.attached_memory import AttachedMemories, AttachedMemoryError
from mimir.ports.memory_backend import MemoryBackend
from mimir.registry import MimirRegistryEntry, MimirRegistryStore
from niuu.cap_telemetry import cap_hit_counts, reset_cap_hits
from niuu.domain.mimir_facts import TrustLevel
from tests.test_mimir.unit.fakes import InMemoryMimirAdapter
from tests.test_mimir.unit.memory_fakes import (
    MCP,
    SESSION_ID,
    FakeGBrain,
    context,
    gbrain_backend,
)

RUN_ID = "a1b2c3d4-0000-4000-8000-000000000123"
URL = "url:https://linear.app/acme/LIN-482"
NOTES = Attachment("team_notes", "mimir-notes", AttachmentAccess.READ_WRITE, ("notes/",))
VENDOR = Attachment("vendor", "mimir-vendor", AttachmentAccess.READ)


def _context(**overrides):
    return replace(context(), workflow_run_id=RUN_ID, attached=(NOTES, VENDOR), **overrides)


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


class FakeAttached(AttachedMemories):
    def __init__(self, **stores: InMemoryMimirAdapter) -> None:
        self.stores = stores
        self.opened: list[tuple[str, str]] = []

    def open(self, tenant_id: str, attachment: Attachment, namespace: str) -> MemoryBackend:
        self.opened.append((tenant_id, attachment.registry_entry_id))
        store = self.stores.get(attachment.name)
        if store is None:
            raise AttachedMemoryError(f"{attachment.registry_entry_id} is not registered")
        return MimirPortBackend(store, namespace=namespace, label=attachment.name)


@pytest.fixture
def brain():
    fake = FakeGBrain()
    with respx.mock(assert_all_called=False) as router:
        router.post(MCP).mock(side_effect=fake.handle)
        yield fake


@pytest.fixture
def stores():
    notes, vendor = InMemoryMimirAdapter(), InMemoryMimirAdapter()
    vendor.pages["guides/booking.md"] = (
        "---\ntitle: Booking guide\n---\n\n## Compiled Truth\n\nThe booking cut-over is owned "
        "by Ines.\n\n## Timeline\n\n- 2026-11-01: guide written. [Source: vendor, docs]\n",
        datetime(2026, 11, 1, tzinfo=UTC),
    )
    return {"team_notes": notes, "vendor": vendor}


@pytest.fixture
async def gateway(brain, stores):
    systems = MemorySystems({"acme-brain": gbrain_backend()}, {"acme": "acme-brain"})
    yield MemoryGateway(systems, default_result_count=10, attached=FakeAttached(**stores))
    await systems.close()


@pytest.fixture
async def session(gateway):
    return await gateway.open(_context())


# ---------------------------------------------------------------------------
# Layers and rules
# ---------------------------------------------------------------------------


class TestLayers:
    def test_a_session_of_a_run_with_attachments_has_every_layer_nearest_first(self) -> None:
        view = SessionView.of(_context(), "acme-brain")
        assert [layer.id for layer in view.layers] == [
            f"session:{SESSION_ID}",
            f"run:{RUN_ID}",
            f"project:{context().project_id}",
            "attached:team_notes",
            "attached:vendor",
            "org",
        ]
        assert [lyr.distance for lyr in view.layers] == sorted(lyr.distance for lyr in view.layers)

    def test_layer_names_and_namespaces(self) -> None:
        assert Layer.parse(f"run:{RUN_ID}").namespace == f"run-{RUN_ID}"
        assert Layer.parse("attached:team_notes").namespace == "attached-team_notes"
        assert Layer.from_namespace(f"run-{RUN_ID}") == Layer(LayerKind.RUN, RUN_ID)
        with pytest.raises(MemoryValidationError):
            Layer(LayerKind.RUN, "Run_1")
        with pytest.raises(MemoryValidationError):
            Layer(LayerKind.ATTACHED, "Bad Name")

    def test_an_attached_layer_is_named_in_full(self) -> None:
        view = SessionView.of(_context(), "acme-brain")
        assert view.layer("run").kind is LayerKind.RUN
        assert view.layer("attached:vendor").key == "vendor"
        with pytest.raises(RuleRefusalError) as refused:
            view.layer("attached")
        assert refused.value.rule is Rule.LAYER_NOT_IN_SESSION

    def test_two_attachments_with_one_name_are_refused(self) -> None:
        with pytest.raises(MemoryValidationError, match="twice"):
            SessionView.of(replace(context(), attached=(NOTES, NOTES)), "acme-brain")

    def test_a_member_session_writes_the_run_layer(self) -> None:
        view = SessionView.of(_context(), "acme-brain")
        for action in ChangeAction:
            decision = view.decide(view.layer("run"), action, "plan/schema")
            assert decision.verdict is Verdict.WRITE
            assert decision.rule is Rule.RUN_MEMBER_WRITES

    def test_a_session_outside_a_run_has_no_run_layer(self) -> None:
        view = SessionView.of(context(), "acme-brain")
        with pytest.raises(RuleRefusalError):
            view.layer(f"run:{RUN_ID}")

    def test_a_read_attachment_refuses_every_write_and_counts_it(self) -> None:
        view = SessionView.of(_context(), "acme-brain")
        with pytest.raises(RuleRefusalError) as refused:
            view.decide(view.layer("attached:vendor"), ChangeAction.WRITE_PAGE, "guides/x")
        assert refused.value.rule is Rule.ATTACHED_READ_ONLY
        assert cap_hit_counts()["memory.write_rules.attached_read_only"] == 1

    def test_a_read_write_attachment_takes_pages_under_its_prefixes_only(self) -> None:
        view = SessionView.of(_context(), "acme-brain")
        notes = view.layer("attached:team_notes")
        assert view.decide(notes, ChangeAction.WRITE_PAGE, "notes/x").verdict is Verdict.WRITE
        assert view.decide(notes, ChangeAction.ADD_TIMELINE, "notes/x").verdict is Verdict.WRITE
        with pytest.raises(RuleRefusalError) as outside:
            view.decide(notes, ChangeAction.WRITE_PAGE, "secrets/x")
        assert outside.value.rule is Rule.ATTACHED_WRITE_PREFIXES
        assert "notes/" in str(outside.value)
        with pytest.raises(RuleRefusalError) as fact:
            view.decide(notes, ChangeAction.REMEMBER)
        assert fact.value.rule is Rule.ATTACHED_PAGES_ONLY

    def test_the_instructions_name_run_and_attached_layers(self, session) -> None:
        text = instructions(session, asks=True)
        assert f"run:{RUN_ID}" in text and "run layer" in text
        assert "attached:vendor is read-only" in text
        assert "attached:team_notes takes pages only under its write prefixes (notes/)" in text
        assert "memory_ask and memory_search read" in text


# ---------------------------------------------------------------------------
# The gateway over a run layer and attached stores
# ---------------------------------------------------------------------------


class TestRunLayer:
    async def test_the_run_layer_is_placed_and_shared_by_member_sessions(
        self, brain, gateway, session
    ) -> None:
        assert any(a["slug"] == f"l/run-{RUN_ID}/_namespace" for _, a in brain.calls)
        written = await gateway.write_page(
            session,
            "plan/schema",
            compiled_truth="The planner keeps one bookings table.",
            expected_revision=None,
            evidence=[URL],
            layer="run",
        )
        assert written.proposal is None and written.layer.kind is LayerKind.RUN
        implementer = await gateway.open(
            replace(_context(), session_id="5e551011-0000-4000-8000-0000000000ff")
        )
        read = await gateway.read(implementer, "plan/schema")
        assert [p.layer.id for p in read.pages] == [f"run:{RUN_ID}"]
        assert read.pages[0].page.attribution.trust is TrustLevel.AGENT


class TestAttachedLayers:
    async def test_reads_merge_attached_pages_labelled_with_their_layer(
        self, gateway, session
    ) -> None:
        hits = await gateway.search(session, "booking cut-over")
        assert [(h.layer.id, h.hit.ref) for h in hits] == [("attached:vendor", "guides/booking.md")]
        read = await gateway.read(session, "guides/booking.md")
        page = read.pages[0]
        assert page.layer.id == "attached:vendor"
        assert "owned by Ines" in page.page.compiled_truth
        assert not page.page.attribution.attested
        assert page.page.attribution.trust is TrustLevel.AGENT
        assert page.page.timeline[0].summary == "guide written"

    async def test_an_entity_card_takes_an_attached_page_without_facts(
        self, gateway, session
    ) -> None:
        card = await gateway.entity(session, "guides/booking.md")
        assert [p.layer.id for p in card.pages] == ["attached:vendor"]
        assert card.facts == ()

    async def test_a_read_write_attachment_takes_a_page_under_its_prefix(
        self, gateway, session, stores
    ) -> None:
        outcome = await gateway.write_page(
            session,
            "notes/cutover",
            compiled_truth="Cut-over moved to Friday.",
            expected_revision=None,
            evidence=[URL],
            layer="attached:team_notes",
        )
        assert outcome.layer.id == "attached:team_notes" and outcome.proposal is None
        content = stores["team_notes"].pages["notes/cutover"][0]
        assert "Cut-over moved to Friday." in content
        assert f"agent:session:{SESSION_ID} (agent); {URL}" in content
        page = (await gateway.read(session, "notes/cutover", layer="attached:team_notes")).pages[0]
        rewritten = await gateway.write_page(
            session,
            "notes/cutover",
            compiled_truth="Cut-over moved to Monday.",
            expected_revision=page.page.revision,
            evidence=[URL],
            layer="attached:team_notes",
        )
        assert rewritten.proposal is None
        assert "Monday" in stores["team_notes"].pages["notes/cutover"][0]
        with pytest.raises(RevisionConflictError, match="read it again"):
            await gateway.write_page(
                session,
                "notes/cutover",
                compiled_truth="Tuesday.",
                expected_revision=page.page.revision,
                layer="attached:team_notes",
            )

    async def test_writes_the_attachment_does_not_allow_are_refused_not_proposed(
        self, gateway, session, stores
    ) -> None:
        with pytest.raises(RuleRefusalError, match="attached_write_prefixes"):
            await gateway.write_page(
                session,
                "secrets/x",
                compiled_truth="x",
                expected_revision=None,
                layer="attached:team_notes",
            )
        with pytest.raises(RuleRefusalError, match="attached_read_only"):
            await gateway.add_timeline(
                session, "guides/booking.md", entry="seen", evidence=[URL], layer="attached:vendor"
            )
        with pytest.raises(RuleRefusalError, match="attached_pages_only"):
            await gateway.remember(
                session,
                fact="x",
                about="services/x",
                kind="fact",
                evidence=[URL],
                layer="attached:team_notes",
            )
        assert "secrets/x" not in stores["team_notes"].pages

    async def test_a_timeline_row_keeps_who_wrote_it(self, gateway, session, stores) -> None:
        await gateway.write_page(
            session,
            "notes/a",
            compiled_truth="A.",
            expected_revision=None,
            layer="attached:team_notes",
        )
        await gateway.add_timeline(
            session, "notes/a", entry="checked", evidence=[URL], layer="attached:team_notes"
        )
        page = (await gateway.read(session, "notes/a", layer="attached:team_notes")).pages[0]
        assert [row.summary for row in page.page.timeline][-1] == "checked"
        assert URL in page.page.timeline[-1].detail

    async def test_an_attachment_the_registry_cannot_open_fails_the_session_naming_it(
        self, gateway
    ) -> None:
        lost = Attachment("lost", "mimir-lost", AttachmentAccess.READ)
        with pytest.raises(LayerUnavailableError, match="attached:lost"):
            await gateway.open(replace(context(), attached=(lost,)))

    async def test_a_gateway_without_a_registry_refuses_attached_layers(self, brain) -> None:
        systems = MemorySystems({"acme-brain": gbrain_backend()}, {"acme": "acme-brain"})
        bare = MemoryGateway(systems, default_result_count=10)
        with pytest.raises(LayerUnavailableError, match="no registry"):
            await bare.open(_context())
        await systems.close()

    async def test_an_attached_store_holds_no_facts_or_sources(self, stores) -> None:
        backend = MimirPortBackend(stores["vendor"], namespace="attached-vendor", label="vendor")
        with pytest.raises(CapabilityMissingError, match="facts"):
            await backend.recall(["attached-vendor"], subject="x")
        with pytest.raises(CapabilityMissingError, match="sources"):
            await backend.get_source("attached-vendor", "src_1")
        with pytest.raises(MemoryValidationError):
            await backend.get_page("attached-other", "guides/booking.md")

    async def test_memory_tools_refuse_an_attached_write_as_a_tool_error(
        self, gateway, session
    ) -> None:
        result = await MemoryTools(gateway).call(
            session,
            "memory_write_page",
            {"ref": "guides/booking.md", "compiled_truth": "x", "layer": "attached:vendor"},
        )
        assert result["isError"]
        assert result["structuredContent"]["rule"] == "attached_read_only"


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def _registry(tmp_path: Path, *entries: MimirRegistryEntry) -> MimirRegistryStore:
    store = MimirRegistryStore(tmp_path / "registry.json")
    for entry in entries:
        store.save_entry(entry)
    return store


class TestRegistryAttachedMemories:
    def _entry(self, **overrides) -> MimirRegistryEntry:
        values = {
            "id": "mimir-notes",
            "name": "team-notes",
            "tenant_id": "acme",
            "url": "http://notes.test",
        }
        return MimirRegistryEntry(**{**values, **overrides})

    def test_an_orgs_own_entry_opens(self, tmp_path) -> None:
        registry = RegistryAttachedMemories(
            _registry(tmp_path, self._entry()), platform_tenant_id="platform"
        )
        backend = registry.open("acme", NOTES, "attached-team_notes")
        assert isinstance(backend, MimirPortBackend)
        assert registry.open("acme", NOTES, "attached-team_notes") is backend

    def test_another_orgs_entry_is_not_found(self, tmp_path) -> None:
        registry = RegistryAttachedMemories(
            _registry(tmp_path, self._entry(tenant_id="globex")), platform_tenant_id="platform"
        )
        with pytest.raises(AttachedMemoryError, match="not registered for org 'acme'"):
            registry.open("acme", NOTES, "attached-team_notes")

    def test_a_global_entry_is_only_for_the_platforms_own_org(self, tmp_path) -> None:
        store = _registry(tmp_path, self._entry(tenant_id=""))
        with pytest.raises(AttachedMemoryError):
            RegistryAttachedMemories(store, platform_tenant_id="platform").open(
                "acme", NOTES, "attached-team_notes"
            )
        assert RegistryAttachedMemories(store, platform_tenant_id="acme").open(
            "acme", NOTES, "attached-team_notes"
        )

    @pytest.mark.parametrize(
        ("overrides", "match"),
        [
            ({"enabled": False}, "disabled"),
            ({"url": ""}, "no adapter or url"),
            ({"auth_ref": "vault:x"}, "auth_ref"),
        ],
    )
    def test_an_unusable_entry_is_refused(self, tmp_path, overrides, match) -> None:
        registry = RegistryAttachedMemories(
            _registry(tmp_path, self._entry(**overrides)), platform_tenant_id="platform"
        )
        with pytest.raises(AttachedMemoryError, match=match):
            registry.open("acme", NOTES, "attached-team_notes")


def test_evidence_can_cite_a_proposal() -> None:
    assert Evidence.parse("proposal:project:abc-1/p-0f1e").value == "project:abc-1/p-0f1e"
    assert Evidence.parse("proposal:org/p-0f1e").value == "org/p-0f1e"
    with pytest.raises(MemoryValidationError):
        Evidence.parse("proposal:just text")


class TestAnswersOverAttachedLayers:
    async def test_ask_cites_an_attached_page_and_the_pack_holds_it(self, brain, gateway) -> None:
        model = BifrostAnswerModel("http://bifrost.test", timeout_seconds=5)
        answers = MemoryAnswers(
            gateway,
            model,
            answer_model="m",
            retrieval_count=5,
        )
        with respx.mock(assert_all_called=False) as router:
            router.post(MCP).mock(side_effect=brain.handle)
            router.post("http://bifrost.test/v1/messages").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "answer": "Ines.",
                                        "citations": [
                                            {"layer": "attached:vendor", "ref": "guides/booking.md"}
                                        ],
                                    }
                                ),
                            }
                        ],
                        "stop_reason": "end_turn",
                    },
                )
            )
            session = await gateway.open(_context(objective="booking cut-over"))
            answer = await answers.ask(
                session, "booking cut-over", layers=None, caller=ModelCaller.session("t")
            )
            pack = await answers.context_pack(session)
        assert answer.citations == (("attached:vendor", "guides/booking.md"),)
        assert "## guides/booking.md [attached:vendor]" in pack.text
