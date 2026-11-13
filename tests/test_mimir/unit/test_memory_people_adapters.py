"""What the people routes ask Forge and Bifröst: a person's projects and attached memories,
the proposal notifications, a person's answer call, and a person's reading view."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from mimir.adapters.bifrost_answers import BifrostAnswerModel
from mimir.adapters.forge_people import ForgePeopleDirectory
from mimir.adapters.forge_proposals import ForgeProposalNotifier
from mimir.memory.models import Attachment, AttachmentAccess, LayerKind, MemoryValidationError
from mimir.memory.rules import LayerView, RuleRefusalError
from mimir.ports.answer_model import ModelCaller
from mimir.ports.people_directory import ForgeUnavailableError, Person
from mimir.ports.proposal_notifier import ProposalNotifyError, WaitingProposal

FORGE = "http://forge.test"
BIFROST = "http://bifrost.test"
PERSON = Person("ines", "acme", (), authorization="Bearer login")
PROJECT_ID = "9a0e0000-0000-4000-8000-0000000000aa"


@pytest.fixture
async def people():
    directory = ForgePeopleDirectory(FORGE, timeout_seconds=5)
    yield directory
    await directory.close()


@pytest.fixture
async def notifier():
    forge = ForgeProposalNotifier(FORGE + "/", timeout_seconds=5)
    yield forge
    await forge.close()


class TestProjects:
    @respx.mock
    async def test_readable_layers_are_the_shared_resolution(self, people) -> None:
        route = respx.get(f"{FORGE}/api/v1/forge/projects").mock(
            return_value=httpx.Response(200, json=[{"id": PROJECT_ID.upper(), "name": "Booking"}])
        )
        found = await people.readable_layers(PERSON)
        assert [(r.layer.id, r.name) for r in found] == [
            ("org", "Org"),
            (f"project:{PROJECT_ID}", "Booking"),
        ]
        assert route.calls.last.request.headers["authorization"] == "Bearer login"

    @respx.mock
    async def test_a_project_answer_that_is_not_a_list_is_unusable(self, people) -> None:
        respx.get(f"{FORGE}/api/v1/forge/projects").mock(
            return_value=httpx.Response(200, json={"projects": []})
        )
        with pytest.raises(ForgeUnavailableError, match="not a list"):
            await people.readable_layers(PERSON)

    @respx.mock
    async def test_a_projects_mounts_are_its_attachments(self, people) -> None:
        respx.get(f"{FORGE}/api/v1/forge/projects/{PROJECT_ID}/memory").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "platform-notes",
                        "registry_entry_id": "reg-1",
                        "access": "read",
                        "write_prefixes": [],
                    }
                ],
            )
        )
        found = await people.project_attachments(PROJECT_ID, PERSON)
        assert found == (
            Attachment("platform-notes", "reg-1", AttachmentAccess.READ, (), source="project"),
        )

    @pytest.mark.parametrize(
        ("mount", "match"),
        [
            ({"name": "n", "registry_entry_id": "r", "access": "all"}, "access"),
            (
                {"name": "n", "registry_entry_id": "r", "access": "read", "write_prefixes": "x"},
                "prefixes",
            ),
            ({"name": "n", "access": "read"}, "registry_entry_id"),
        ],
    )
    @respx.mock
    async def test_a_malformed_mount_is_unusable(self, people, mount, match) -> None:
        respx.get(f"{FORGE}/api/v1/forge/projects/{PROJECT_ID}/memory").mock(
            return_value=httpx.Response(200, json=[mount])
        )
        with pytest.raises(ForgeUnavailableError, match=match):
            await people.project_attachments(PROJECT_ID, PERSON)


class TestProposalNotifier:
    @respx.mock
    async def test_waiting_is_told_with_the_sessions_credential(self, notifier) -> None:
        route = respx.post(f"{FORGE}/api/v1/forge/sessions/s-1/memory/proposals").mock(
            return_value=httpx.Response(201, json={})
        )
        await notifier.waiting(
            "s-1", WaitingProposal("p-1", PROJECT_ID, "Cut-over", "It moved."), credential="tok"
        )
        sent = route.calls.last.request
        assert sent.headers["authorization"] == "Bearer tok"
        assert json.loads(sent.content) == {
            "proposal_id": "p-1",
            "project_id": PROJECT_ID,
            "title": "Cut-over",
            "why": "It moved.",
        }

    @respx.mock
    async def test_decided_is_told_with_the_persons_login(self, notifier) -> None:
        route = respx.post(
            f"{FORGE}/api/v1/forge/projects/{PROJECT_ID}/memory/proposals/p-1/decided"
        ).mock(return_value=httpx.Response(200, json={}))
        await notifier.decided(PROJECT_ID, "p-1", authorization="Bearer me", outcome="kept")
        assert route.calls.last.request.headers["authorization"] == "Bearer me"
        assert json.loads(route.calls.last.request.content) == {"outcome": "kept"}

    @respx.mock
    async def test_mini_mode_sends_no_authorization(self, notifier) -> None:
        route = respx.post(
            f"{FORGE}/api/v1/forge/projects/{PROJECT_ID}/memory/proposals/p-1/decided"
        ).mock(return_value=httpx.Response(204))
        await notifier.decided(PROJECT_ID, "p-1", authorization="", outcome="discarded")
        assert "authorization" not in route.calls.last.request.headers

    @respx.mock
    async def test_every_failure_is_a_notify_error(self, notifier) -> None:
        respx.post(f"{FORGE}/api/v1/forge/sessions/s-1/memory/proposals").mock(
            return_value=httpx.Response(403, text="not yours")
        )
        respx.post(f"{FORGE}/api/v1/forge/projects/{PROJECT_ID}/memory/proposals/p-1/decided").mock(
            side_effect=httpx.ConnectError("refused")
        )
        waiting = WaitingProposal("p-1", PROJECT_ID, "t", "w")
        with pytest.raises(ProposalNotifyError, match="403"):
            await notifier.waiting("s-1", waiting, credential="tok")
        with pytest.raises(ProposalNotifyError, match="could not be told"):
            await notifier.decided(PROJECT_ID, "p-1", authorization="", outcome="kept")
        with pytest.raises(ProposalNotifyError, match="credential"):
            await notifier.waiting("s-1", waiting, credential=" ")
        with pytest.raises(ProposalNotifyError, match="not a valid id"):
            await notifier.waiting("s/../x", waiting, credential="tok")

    def test_settings_are_checked(self) -> None:
        with pytest.raises(ValueError, match="forge_base_url"):
            ForgeProposalNotifier(" ", timeout_seconds=1)
        with pytest.raises(ValueError, match="positive"):
            ForgeProposalNotifier(FORGE, timeout_seconds=0)


class TestPersonsAnswerCall:
    @respx.mock
    async def test_a_persons_call_names_them_for_bifrost(self) -> None:
        route = respx.post(f"{BIFROST}/v1/messages").mock(
            return_value=httpx.Response(
                200, json={"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"}
            )
        )
        model = BifrostAnswerModel(BIFROST, timeout_seconds=5)
        caller = ModelCaller.person(authorization="", user_id="ines", tenant_id="acme")
        await model.answer(model="m", system="s", prompt="p", caller=caller)
        await model.close()
        sent = route.calls.last.request.headers
        assert "authorization" not in sent
        assert sent["x-agent-id"] == "ines" and sent["x-tenant-id"] == "acme"

    def test_a_caller_names_who_pays(self) -> None:
        assert ModelCaller.session("t").authorization == "Bearer t"
        with pytest.raises(ValueError, match="credential"):
            ModelCaller.session(" ")
        with pytest.raises(ValueError, match="org"):
            ModelCaller.person(authorization="", user_id="ines", tenant_id="")


class TestReadingView:
    def test_a_reader_reads_its_projects_their_attachments_and_the_org(self) -> None:
        notes = Attachment("notes", "reg-1", AttachmentAccess.READ)
        view = LayerView.for_reader(
            tenant_id="acme",
            system="brain",
            project_ids=(PROJECT_ID.upper(),),
            attachments=(notes,),
        )
        assert [layer.id for layer in view.layers] == [
            f"project:{PROJECT_ID}",
            "attached:notes",
            "org",
        ]
        assert view.attachment(view.layers[1]) is notes
        assert view.layer("org").kind is LayerKind.ORG

    def test_what_a_reader_does_not_have_is_refused(self) -> None:
        view = LayerView.for_reader(tenant_id="acme", system="brain", project_ids=())
        with pytest.raises(RuleRefusalError, match="no layer 'session:5e551011'; your layers"):
            view.layer("session:5e551011")
        with pytest.raises(MemoryValidationError, match="your attachments"):
            view.attachment(view.layers[0])
        duplicate = Attachment("notes", "reg-1", AttachmentAccess.READ)
        with pytest.raises(MemoryValidationError, match="twice"):
            LayerView.for_reader(
                tenant_id="acme", system="b", project_ids=(), attachments=(duplicate, duplicate)
            )
