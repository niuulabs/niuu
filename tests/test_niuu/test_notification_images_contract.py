"""The shared notification image contract: the reference, its limits and its URLs."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from niuu.domain.notifications import (
    MAX_IMAGE_ALT_CHARS,
    MAX_IMAGE_URL_CHARS,
    NOTIFICATION_IMAGE_PATH,
    NotificationDraft,
    NotificationImageRef,
    NotificationImageSourceKind,
    build_notification_turn,
    draft_from_log_payload,
    draft_payload,
    notification_image_url,
)

FILE_ID = "pf_" + "a" * 32
SESSION_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


class TestImageRef:
    def test_file_id_reference(self) -> None:
        ref = NotificationImageRef(file_id=FILE_ID, alt="  Login   screen \n")
        assert ref.source_kind is NotificationImageSourceKind.FILE_ID
        assert ref.source_ref == FILE_ID
        assert ref.alt == "Login screen"
        assert ref.wire() == {"file_id": FILE_ID, "alt": "Login screen"}

    def test_url_reference_without_alt(self) -> None:
        ref = NotificationImageRef(url="https://cdn.example.com/chart.png?v=2")
        assert ref.source_kind is NotificationImageSourceKind.URL
        assert ref.source_ref == "https://cdn.example.com/chart.png?v=2"
        assert ref.wire() == {"url": "https://cdn.example.com/chart.png?v=2"}

    def test_blank_alt_is_absent(self) -> None:
        assert NotificationImageRef(file_id=FILE_ID, alt="   ").alt is None

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            ({}, "exactly one of file_id"),
            ({"file_id": FILE_ID, "url": "https://x.example/a.png"}, "exactly one of file_id"),
            ({"alt": "only alt"}, "exactly one of file_id"),
            ({"url": "http://x.example/a.png"}, "https://"),
            ({"url": "https:///nohost.png"}, "https://"),
            ({"url": "ftp://x.example/a.png"}, "https://"),
            ({"url": "https://user:pw@x.example/a.png"}, "credentials"),
            ({"url": "https://x.example/" + "a" * MAX_IMAGE_URL_CHARS}, "at most"),
            ({"url": ""}, "at least"),
            ({"file_id": "pf_ABC"}, "pattern"),
            ({"file_id": "../../etc/passwd"}, "pattern"),
            ({"file_id": FILE_ID, "alt": "x" * (MAX_IMAGE_ALT_CHARS + 1)}, "at most"),
            ({"file_id": FILE_ID, "caption": "extra"}, "Extra inputs"),
        ],
    )
    def test_invalid_references_are_refused(self, data, message) -> None:
        with pytest.raises(ValidationError, match=message):
            NotificationImageRef.model_validate(data)


class TestDraftWithImage:
    def test_draft_accepts_an_image_and_validates_it(self) -> None:
        draft = NotificationDraft.model_validate(
            {"kind": "milestone", "title": "UI done", "image": {"file_id": FILE_ID}}
        )
        assert draft.image == NotificationImageRef(file_id=FILE_ID)
        with pytest.raises(ValidationError, match="exactly one"):
            NotificationDraft.model_validate({"kind": "milestone", "title": "x", "image": {}})

    def test_payload_omits_an_absent_image(self) -> None:
        draft = NotificationDraft(kind="milestone", title="Done")
        payload = draft_payload(draft)
        assert "image" not in payload
        # Unset question signals and speak stay absent too: the shape older producers wrote.
        assert payload == draft.model_dump(
            mode="json",
            exclude={"image", "speak", "question", "about_question", "ask", "resolves"},
        )

    def test_payload_carries_the_image_wire_form(self) -> None:
        draft = NotificationDraft(
            kind="milestone", title="Done", image={"url": "https://x.example/a.png", "alt": "A"}
        )
        assert draft_payload(draft)["image"] == {"url": "https://x.example/a.png", "alt": "A"}

    def test_turn_round_trip_keeps_the_image(self) -> None:
        draft = NotificationDraft(
            kind="milestone", title="Chart", image={"file_id": FILE_ID, "alt": "Latency"}
        )
        turn = build_notification_turn(
            draft, turn_id="nt_1", session_id=SESSION_ID, created_at="2026-09-30T10:00:00Z"
        )
        assert turn["metadata"]["notification"]["image"] == {"file_id": FILE_ID, "alt": "Latency"}
        assert turn["parts"][0]["input"]["image"] == {"file_id": FILE_ID, "alt": "Latency"}
        parsed = draft_from_log_payload({"type": "conversation.turn", "turn": turn})
        assert parsed is not None
        assert parsed[1].image == NotificationImageRef(file_id=FILE_ID, alt="Latency")

    def test_turn_without_image_keeps_its_old_shape(self) -> None:
        turn = build_notification_turn(
            NotificationDraft(kind="milestone", title="Done"),
            turn_id="nt_2",
            session_id=SESSION_ID,
            created_at="2026-09-30T10:00:00Z",
        )
        assert "image" not in turn["metadata"]["notification"]

    def test_a_turn_with_a_malformed_image_is_ignored(self) -> None:
        turn = build_notification_turn(
            NotificationDraft(kind="milestone", title="Done"),
            turn_id="nt_3",
            session_id=SESSION_ID,
            created_at="2026-09-30T10:00:00Z",
        )
        turn["metadata"]["notification"]["image"] = {"url": "http://insecure.example/a.png"}
        assert draft_from_log_payload({"type": "conversation.turn", "turn": turn}) is None


class TestImageUrls:
    def test_plain_and_thumbnail_urls(self) -> None:
        nid = uuid.uuid4()
        assert notification_image_url(nid) == NOTIFICATION_IMAGE_PATH.format(notification_id=nid)
        assert notification_image_url(nid) == f"/api/v1/forge/notifications/{nid}/image"
        assert notification_image_url(nid, thumbnail=True) == (
            f"/api/v1/forge/notifications/{nid}/image?variant=thumb"
        )

    def test_guild_urls_name_the_instance(self) -> None:
        nid = "n-1"
        assert notification_image_url(nid, instance_id="node a") == (
            "/api/v1/forge/notifications/n-1/image?instance_id=node+a"
        )
        assert notification_image_url(nid, thumbnail=True, instance_id="b") == (
            "/api/v1/forge/notifications/n-1/image?variant=thumb&instance_id=b"
        )
