"""The write rules table and a session's layers (design section 4)."""

from __future__ import annotations

import pytest

from mimir.memory.models import (
    ChangeAction,
    Evidence,
    Layer,
    LayerKind,
    MemoryValidationError,
)
from mimir.memory.rules import (
    Rule,
    RuleRefusalError,
    SessionView,
    Verdict,
    approver_of,
    under_prefixes,
)
from niuu.cap_telemetry import cap_hit_counts, reset_cap_hits
from niuu.domain.mimir_facts import AuthorKind, TrustLevel
from tests.test_mimir.unit.memory_fakes import PROJECT_ID, SESSION_ID, context


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


def _view(**kwargs) -> SessionView:
    return SessionView.of(context(**kwargs), "acme-brain")


class TestLayers:
    def test_a_project_session_has_session_project_and_org_nearest_first(self) -> None:
        assert [layer.id for layer in _view().layers] == [
            f"session:{SESSION_ID}",
            f"project:{PROJECT_ID}",
            "org",
        ]

    def test_a_session_without_a_project_has_session_and_org(self) -> None:
        assert [layer.kind for layer in _view(project_id=None).layers] == [
            LayerKind.SESSION,
            LayerKind.ORG,
        ]

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            (None, f"session:{SESSION_ID}"),
            ("session", f"session:{SESSION_ID}"),
            ("project", f"project:{PROJECT_ID}"),
            (f"project:{PROJECT_ID}", f"project:{PROJECT_ID}"),
            ("ORG", "org"),
        ],
    )
    def test_layers_are_named_short_or_in_full(self, name, expected) -> None:
        assert _view().layer(name).id == expected

    @pytest.mark.parametrize(
        "name", ["project:9a0e0000-0000-4000-8000-0000000000bb", "session:other", "run:x", "x"]
    )
    def test_a_layer_the_session_does_not_have_is_refused_and_counted(self, name) -> None:
        with pytest.raises(RuleRefusalError, match="layer_not_in_session") as caught:
            _view().layer(name)
        assert caught.value.rule is Rule.LAYER_NOT_IN_SESSION
        assert cap_hit_counts()["memory.write_rules.layer_not_in_session"] == 1

    def test_the_project_layer_of_a_projectless_session_is_refused(self) -> None:
        with pytest.raises(RuleRefusalError):
            _view(project_id=None).layer("project")

    def test_a_session_is_an_agent(self) -> None:
        caller = _view().caller
        assert caller.author.kind is AuthorKind.AGENT
        assert caller.trust is TrustLevel.AGENT
        assert caller.author.id == f"session:{SESSION_ID}"

    def test_layer_ids_parse_and_namespace(self) -> None:
        layer = Layer.parse(f"project:{PROJECT_ID}")
        assert layer.namespace == f"project-{PROJECT_ID}"
        assert layer.proposals_namespace == f"_proposals/project-{PROJECT_ID}"
        assert Layer.from_namespace(layer.namespace) == layer
        with pytest.raises(MemoryValidationError):
            Layer.parse("org:x")
        with pytest.raises(MemoryValidationError):
            Layer(LayerKind.PROJECT, "Bad Id")


SESSION = Layer(LayerKind.SESSION, SESSION_ID)
PROJECT = Layer(LayerKind.PROJECT, PROJECT_ID)
ORG = Layer(LayerKind.ORG)


class TestRulesTable:
    @pytest.mark.parametrize("action", list(ChangeAction))
    def test_the_session_layer_is_written(self, action) -> None:
        assert _view().decide(SESSION, action, "anything").verdict is Verdict.WRITE

    @pytest.mark.parametrize("action", list(ChangeAction))
    def test_the_org_layer_is_proposed_to(self, action) -> None:
        decision = _view().decide(ORG, action, "decisions/x")
        assert (decision.verdict, decision.rule) == (Verdict.PROPOSE, Rule.ORG_PROPOSES)

    @pytest.mark.parametrize("action", [ChangeAction.WRITE_PAGE, ChangeAction.ADD_TIMELINE])
    @pytest.mark.parametrize(
        ("slug", "verdict"),
        [
            ("decisions/drop-queue", Verdict.WRITE),
            ("runbooks/deploy/rollback", Verdict.WRITE),
            ("services/booking-api", Verdict.PROPOSE),
            ("decisions-old/x", Verdict.PROPOSE),
            ("decisions", Verdict.PROPOSE),
        ],
    )
    def test_project_pages_are_written_only_under_write_prefixes(
        self, action, slug, verdict
    ) -> None:
        decision = _view().decide(PROJECT, action, slug)
        assert decision.verdict is verdict
        assert decision.rule is Rule.PROJECT_WRITE_PREFIXES

    @pytest.mark.parametrize(
        "action", [ChangeAction.REMEMBER, ChangeAction.ADD_SOURCE, ChangeAction.RETIRE]
    )
    def test_other_project_records_are_proposed_even_under_a_prefix(self, action) -> None:
        decision = _view().decide(PROJECT, action, "decisions/x")
        assert (decision.verdict, decision.rule) == (
            Verdict.PROPOSE,
            Rule.PROJECT_PROPOSES_RECORDS,
        )

    def test_no_write_prefixes_means_every_project_page_is_proposed(self) -> None:
        decision = _view(prefixes=()).decide(PROJECT, ChangeAction.WRITE_PAGE, "decisions/x")
        assert decision.verdict is Verdict.PROPOSE
        assert "none set" in decision.reason

    def test_prefixes_match_whole_segments(self) -> None:
        assert under_prefixes("Decisions/X", ("decisions",))
        assert under_prefixes("decisions/x", ("/decisions/",))
        assert not under_prefixes("decisionsx/y", ("decisions/",))
        assert not under_prefixes("anything", ("", "/"))

    def test_approvers_are_named(self) -> None:
        assert approver_of(ORG) == "an org admin"
        assert PROJECT_ID in approver_of(PROJECT)


class TestEvidence:
    @pytest.mark.parametrize(
        "ref",
        [
            "source:src_1a2b3c4d5e6f7a8b",
            "session_event:s1/e9",
            "tool_call:toolu_01",
            "room_message:r/m",
            "url:https://linear.app/x/LIN-482",
            "commit:niuulabs/niuu@1f4f9622b",
            "statement:st-1",
            "page:services/booking-api",
            "fact:12@l/org/x",
        ],
    )
    def test_typed_references_parse(self, ref) -> None:
        assert Evidence.parse(ref).ref == ref

    @pytest.mark.parametrize(
        "ref",
        [
            "the user said so",
            "vibes:strong",
            "url:ftp://x",
            "commit:abc",
            "source:not-a-source",
            "session_event:only-one-part",
        ],
    )
    def test_free_text_and_malformed_references_are_refused(self, ref) -> None:
        with pytest.raises(MemoryValidationError):
            Evidence.parse(ref)
