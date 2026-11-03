"""F1 extraction from stored log rows (design §5.2), identities, types (F1t) and the
report_artifact turn shape."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from niuu.domain.artifact_reports import (
    ArtifactReport,
    build_report_turn,
    report_from_turn,
)
from niuu.domain.notifications import NotificationDraft, NotificationLink, build_notification_turn
from tests.support.artifacts import CATALOG, hook_row
from volundr.domain.artifacts import (
    ArtifactType,
    Burst,
    Origin,
    Provider,
    PublicationSource,
    TypeCatalog,
    announces,
    artifact_marker,
    burst_id_for,
    choose_burst,
    classify_origin,
    claude_artifact_key,
    is_publication_row,
    occurrence,
    publication_id,
    publications_from_entry,
    revision_id,
)
from volundr.domain.models import SessionLogEntry

SID = uuid.UUID("11111111-2222-3333-4444-555555555555")
AT = datetime(2026, 10, 20, 12, tzinfo=UTC)
URL = "https://claude.ai/artifact/AbC123"
SLIDES = "https://claude.ai/artifact/8jTsAFQMFDb2oA8MsPJ2eL"


def _hook(response, tool_input=None, *, event="PostToolUse", tool="Artifact", **inner):
    body = {
        "tool_name": tool,
        "tool_use_id": "toolu_1",
        "tool_input": tool_input or {},
        "tool_response": response,
        **inner,
    }
    return hook_row(str(SID), 7, AT, body, event=event)


def _turn(turn: dict, seq: int = 9) -> SessionLogEntry:
    return SessionLogEntry(
        session_id=SID,
        seq=seq,
        kind="conversation.turn",
        payload={"type": "conversation.turn", "turn": turn},
        ts=AT,
    )


def _one(entry):
    found = publications_from_entry(entry, CATALOG)
    assert len(found) == 1
    return found[0]


class TestHookRows:
    def test_structured_publish(self):
        pub = _one(_hook({"url": URL, "version": "17-ab", "seq": 3, "title": "Plan"}))
        assert (pub.source, pub.provider, pub.provider_key) == (
            PublicationSource.ARTIFACT_TOOL,
            Provider.CLAUDE_AI,
            "AbC123",
        )
        assert (pub.provider_version, pub.provider_seq, pub.title) == ("17-ab", 3, "Plan")
        assert (pub.session_seq, pub.ordinal, pub.at, pub.tool_use_id) == (7, 0, AT, "toolu_1")
        assert pub.artifact_type is ArtifactType.PAGE
        assert pub.forms_bursts and not pub.subagent

    def test_subagent_and_title_from_input(self):
        pub = _one(_hook({"url": URL}, {"title": "From input"}, agent_id="a1"))
        assert pub.subagent and pub.title == "From input" and pub.provider_seq is None

    def test_typed_create_classifies_by_type_url(self):
        pub = _one(_hook({"url": URL, "created_from_type": True, "type": {"url": SLIDES}}))
        assert pub.artifact_type is ArtifactType.DECK and pub.type_url == SLIDES

    def test_unknown_type_falls_back_to_front_matter_name(self):
        response = {
            "url": URL,
            "type": {"url": "https://claude.ai/artifact/NewType9"},
            "instructions": "---\nname: pages\ndescription: docs\n---\n# Pages",
        }
        assert _one(_hook(response)).artifact_type is ArtifactType.DOC

    def test_unknown_type_without_name(self):
        response = {"url": URL, "type": {"url": "https://claude.ai/artifact/NewType9"}}
        assert _one(_hook(response)).artifact_type is ArtifactType.UNKNOWN

    def test_type_url_from_input_on_create(self):
        assert _one(_hook({"url": URL}, {"type_url": SLIDES})).artifact_type is ArtifactType.DECK

    @pytest.mark.parametrize(
        "entry",
        [
            _hook({"url": URL}, {"asset": True}),
            _hook({"url": URL}, {"asset": "true"}),
            _hook({"url": URL}, {"action": "read"}),
            _hook({"url": URL, "files_read": ["x"]}),
            _hook("Published " + URL),
            _hook({"url": "https://example.com/x"}),
            _hook({"url": URL}, event="PostToolUseFailure"),
            _hook({"url": URL}, tool="Read"),
        ],
        ids=[
            "asset",
            "asset-str",
            "read",
            "refused",
            "unstructured",
            "not-claude",
            "failure",
            "other-tool",
        ],
    )
    def test_rejected_rows(self, entry):
        assert publications_from_entry(entry, CATALOG) == []

    def test_malformed_payloads_never_raise(self):
        bad = SessionLogEntry(
            session_id=SID,
            seq=1,
            kind="claude_hook",
            payload={"type": "claude_hook", "hook_event_name": "PostToolUse", "payload": "nope"},
            ts=AT,
        )
        assert publications_from_entry(bad, CATALOG) == []
        weird = SessionLogEntry(session_id=SID, seq=1, kind="x", payload=[], ts=AT)  # type: ignore[arg-type]
        assert publications_from_entry(weird, CATALOG) == []
        assert (
            publications_from_entry(
                SessionLogEntry(session_id=SID, seq=1, kind="x", payload={"type": "other"}, ts=AT),
                CATALOG,
            )
            == []
        )


class TestTurnRows:
    def test_present_file_turn_any_engine(self):
        """The shared present-file turn (Codex writes the same one: judge seq 897, 982)."""
        turn = {
            "id": "t1",
            "role": "assistant",
            "content": "report.pdf",
            "parts": [
                {
                    "id": "pf_" + "a" * 32,
                    "name": "present_file",
                    "type": "tool_use",
                    "input": {
                        "file_id": "pf_" + "a" * 32,
                        "name": "report.pdf",
                        "mime": "application/pdf",
                        "size": 10,
                    },
                }
            ],
            "metadata": {"present_file": True},
        }
        pub = _one(_turn(turn))
        assert (pub.source, pub.provider, pub.provider_key) == (
            PublicationSource.PRESENT_FILE,
            Provider.FORGE_FILE,
            "pf_" + "a" * 32,
        )
        assert pub.artifact_type is ArtifactType.FILE and pub.mime == "application/pdf"
        assert pub.url.endswith("/files/presented/pf_" + "a" * 32)
        assert not pub.forms_bursts

    def test_present_file_without_file_id_is_ignored(self):
        turn = {
            "id": "t",
            "parts": [{"name": "present_file", "type": "tool_use", "id": "x", "input": {}}],
        }
        assert publications_from_entry(_turn(turn), CATALOG) == []

    def test_notification_artifact_link_is_recorded_but_never_bursts(self):
        draft = NotificationDraft(
            kind="milestone",
            title="Deck is up",
            links=[
                NotificationLink(label="Deck", url=URL, kind="artifact"),
                NotificationLink(label="PR", url="https://github.com/x/y/pull/1", kind="pr"),
                NotificationLink(label="Other", url="https://example.com", kind="artifact"),
            ],
        )
        turn = build_notification_turn(draft, turn_id="nt_1", session_id=SID, created_at="x")
        pub = _one(_turn(turn))
        assert pub.source is PublicationSource.NOTIFY_LINK and pub.provider_key == "AbC123"
        assert pub.title == "Deck" and not pub.forms_bursts

    def test_report_artifact_turn(self):
        report = ArtifactReport(
            url=URL + "?x=1",
            title="  Spec  v2 ",
            provider_version="5-ff",
            version_label="v2",
            type="doc",
        )
        turn = build_report_turn(report, turn_id="ar_1", created_at="x")
        pub = _one(_turn(turn))
        assert pub.source is PublicationSource.REPORT_ARTIFACT and pub.forms_bursts
        assert (pub.title, pub.provider_version, pub.version_label) == ("Spec v2", "5-ff", "v2")
        assert pub.artifact_type is ArtifactType.DOC

    def test_report_without_type_is_unknown(self):
        turn = build_report_turn(ArtifactReport(url=URL, title="T"), turn_id="ar_2", created_at="x")
        assert _one(_turn(turn)).artifact_type is ArtifactType.UNKNOWN

    def test_malformed_report_and_turn_are_ignored(self):
        assert (
            report_from_turn(
                {"metadata": {"kind": "artifact_report", "artifact_report": {"url": "nope"}}}
            )
            is None
        )
        assert report_from_turn({"metadata": {"kind": "artifact_report"}}) is None
        assert report_from_turn("x") is None  # type: ignore[arg-type]
        assert report_from_turn({"metadata": {"kind": "other"}}) is None
        bad = SessionLogEntry(
            session_id=SID,
            seq=1,
            kind="conversation.turn",
            payload={"type": "conversation.turn", "turn": "x"},
            ts=AT,
        )
        assert publications_from_entry(bad, CATALOG) == []

    def test_turn_with_several_parts_numbers_them(self):
        parts = [
            {
                "id": f"pf_{i:032x}",
                "name": "present_file",
                "type": "tool_use",
                "input": {"file_id": f"pf_{i:032x}", "name": f"f{i}"},
            }
            for i in range(2)
        ] + [{"type": "text", "text": "hi"}]
        found = publications_from_entry(_turn({"id": "t", "parts": parts}), CATALOG)
        assert [p.ordinal for p in found] == [0, 1]


class TestPrefilter:
    def test_prefilter_accepts_every_shape(self):
        assert is_publication_row(_hook({"url": URL}).payload)
        assert is_publication_row(
            {
                "type": "conversation.turn",
                "turn": {"parts": [{"type": "tool_use", "name": "present_file"}]},
            }
        )
        assert is_publication_row(
            {
                "type": "conversation.turn",
                "turn": {"parts": [{"type": "tool_use", "name": "report_artifact"}]},
            }
        )
        draft = NotificationDraft(
            kind="info", title="x", links=[NotificationLink(label="a", url=URL, kind="artifact")]
        )
        turn = build_notification_turn(draft, turn_id="nt", session_id=SID, created_at="x")
        assert is_publication_row({"type": "conversation.turn", "turn": turn})

    def test_prefilter_rejects_others(self):
        assert not is_publication_row(None)
        assert not is_publication_row(_hook({"url": URL}, tool="Read").payload)
        assert not is_publication_row({"type": "conversation.turn", "turn": "x"})
        assert not is_publication_row({"type": "conversation.turn", "turn": {"parts": []}})
        assert not is_publication_row({"type": "assistant"})


class TestIdentities:
    def test_urls(self):
        assert claude_artifact_key("https://claude.ai/code/artifact/Zz9#x") == "Zz9"
        assert claude_artifact_key(" https://claude.ai/artifact/Zz9/ ") == "Zz9"
        assert claude_artifact_key("https://claude.ai/artifact/") is None
        assert claude_artifact_key("https://evil.example/claude.ai/artifact/Zz9") is None
        assert claude_artifact_key(None) is None

    def test_ids_are_stable_and_follow_the_reference(self):
        assert occurrence("thor", SID, 5) == ("thor", "log", str(SID), 5)
        assert publication_id("thor", SID, 5, 0) == publication_id("thor", str(SID), 5, 0)
        assert publication_id("thor", SID, 5, 0) != publication_id("mini-m6", SID, 5, 0)
        expected = uuid.uuid5(
            uuid.UUID("6f7d2c1e-8a5b-4c3d-9e2f-1a0b3c4d5e6f"),
            f"thor|K|{('thor', 'log', str(SID), 5)}",
        )
        assert burst_id_for("thor", "K", SID, 5) == expected

    def test_revision_ids(self):
        a = _one(_hook({"url": URL, "version": "1-a"}))
        b = _one(
            hook_row(
                str(uuid.uuid4()),
                99,
                AT,
                {
                    "tool_name": "Artifact",
                    "tool_input": {},
                    "tool_use_id": "toolu_2",
                    "tool_response": {"url": URL, "version": "1-a"},
                },
            )
        )
        assert revision_id(a, "thor") == revision_id(b, "mini-m6")  # a republish: one revision
        c = _one(_hook({"url": URL}))
        assert revision_id(c, "thor") != revision_id(c, "mini-m6")
        assert revision_id(a, "thor").startswith("rev_")

    def test_origin(self):
        lag = timedelta(minutes=10)
        assert classify_origin(AT, AT + lag, lag) is Origin.LIVE
        assert classify_origin(AT, AT + lag + timedelta(seconds=1), lag) is Origin.BACKFILL

    def test_markers(self):
        assert [artifact_marker(t) for t in ArtifactType] == [
            "deck",
            "design",
            "doc",
            None,
            None,
            "file",
            None,
        ]

    def test_catalog_from_config(self):
        catalog = TypeCatalog.from_config({"raw-key": "design"}, {"Slides": "deck"})
        assert catalog.classify("raw-key", None) is ArtifactType.DESIGN
        assert catalog.classify(None, "slides") is ArtifactType.DECK
        assert catalog.classify(None, None) is ArtifactType.PAGE


class TestRule:
    def _burst(self, bid: str, start: int, last: int) -> Burst:
        return Burst(
            burst_id=uuid.UUID(int=int(bid)),
            host="thor",
            provider_key="K",
            origin=Origin.LIVE,
            started_at=AT + timedelta(minutes=start),
            last_at=AT + timedelta(minutes=last),
            publications=1,
            seed_session_id=SID,
            seed_session_seq=1,
            due_at=None,
        )

    def test_choose_joins_the_most_recent_and_records_the_bridge(self):
        older, newer = self._burst("1", 0, 0), self._burst("2", 50, 50)
        choice = choose_burst([older, newer], AT + timedelta(minutes=25), timedelta(minutes=30))
        assert choice.burst is newer and choice.bridged == (older.burst_id,)

    def test_choose_none_outside_every_window(self):
        assert (
            choose_burst(
                [self._burst("1", 0, 0)], AT + timedelta(minutes=31), timedelta(minutes=30)
            ).burst
            is None
        )

    def test_announces(self):
        burst = self._burst("1", 0, 10)
        window = timedelta(minutes=30)
        base = dict(
            correlation_id=None, link_urls=[URL.replace("AbC123", "K")], burst=burst, window=window
        )
        assert announces(source="agent", created_at=AT + timedelta(minutes=40), **base)
        assert not announces(source="agent", created_at=AT + timedelta(minutes=41), **base)
        assert not announces(source="agent", created_at=AT - timedelta(minutes=31), **base)
        assert not announces(source="system", created_at=AT, **base)
        assert not announces(
            source="agent",
            created_at=AT + timedelta(minutes=5),
            until=AT + timedelta(minutes=2),
            **base,
        )
        assert not announces(
            **{**base, "correlation_id": "art-burst:x"}, source="agent", created_at=AT
        )
        assert not announces(**{**base, "link_urls": [URL]}, source="agent", created_at=AT)


def test_present_file_from_the_brokers_own_producer():
    """Engine parity: Skuld's present-file turn (what Claude and Codex sessions both write
    through the `present-file` shim; judge-session seq 897 and 982 were Codex's) lands."""
    from dataclasses import asdict

    from skuld.broker_api import _build_present_file_turn

    file_id = "pf_770bc1cc1d614ff9bab7f3808cae0708"
    turn = asdict(_build_present_file_turn(file_id, "plan.md", "text/markdown", 120, "Plan"))
    entry = SessionLogEntry(
        session_id=SID,
        seq=897,
        kind="conversation.turn",
        payload={"type": "conversation.turn", "turn": turn},
        ts=AT,
    )
    assert is_publication_row(entry.payload)
    [pub] = publications_from_entry(entry, CATALOG)
    assert (pub.provider_key, pub.title, pub.mime, pub.session_seq) == (
        file_id,
        "plan.md",
        "text/markdown",
        897,
    )
    assert pub.artifact_type is ArtifactType.FILE


def test_naive_timestamps_are_read_as_utc():
    entry = _hook({"url": URL})
    naive = SessionLogEntry(
        session_id=entry.session_id,
        seq=entry.seq,
        kind=entry.kind,
        payload=entry.payload,
        ts=AT.replace(tzinfo=None),
    )
    assert _one(naive).at == AT


def test_a_report_turn_records_one_report_however_many_parts():
    turn = build_report_turn(ArtifactReport(url=URL, title="T"), turn_id="ar_3", created_at="x")
    turn["parts"] = turn["parts"] * 2
    assert len(publications_from_entry(_turn(turn), CATALOG)) == 1
