"""The Forge MCP ``notify`` tool's ``image`` input: schema, guidance and forwarding."""

from __future__ import annotations

import json

import httpx
import pytest

from niuu.domain.notifications import (
    MAX_IMAGE_ALT_CHARS,
    PRESENTED_FILE_ID_PATTERN,
    NotificationDraft,
    NotificationImageRef,
)
from niuu.forge_mcp.guidance import NOTIFICATION_IMAGE_GUIDANCE
from niuu.forge_mcp.rest_client import RestForgeClient
from niuu.forge_mcp.tools import _FEED_NOTIFY_DESCRIPTION, _notification_summary
from skuld.forge_mcp.skill import forge_notify_skill_markdown
from tests.test_niuu.test_forge_mcp_tools import SELF, _feed_toolbox, _toolbox

FILE_ID = "pf_" + "0123456789abcdef" * 2


def _notify_tool(toolbox) -> dict:
    return next(tool for tool in toolbox.list_tools() if tool["name"] == "notify")


class TestSchemaAndGuidance:
    def test_transcript_and_feed_schemas_describe_the_image(self) -> None:
        for toolbox in (_toolbox()[0], _feed_toolbox()[0]):
            image = _notify_tool(toolbox)["inputSchema"]["properties"]["image"]
            assert image["type"] == "object" and image["additionalProperties"] is False
            assert set(image["properties"]) == {"file_id", "url", "alt"}
            assert image["properties"]["file_id"]["pattern"] == PRESENTED_FILE_ID_PATTERN
            assert image["properties"]["url"]["pattern"] == "^https://"
            assert image["properties"]["alt"]["maxLength"] == MAX_IMAGE_ALT_CHARS
            assert "exactly one of file_id" in image["description"]

    def test_descriptions_and_skill_teach_images(self) -> None:
        notify = _notify_tool(_toolbox()[0])["description"]
        for text in (notify, _FEED_NOTIFY_DESCRIPTION):
            assert "`image: {file_id}` from" in text and "`alt`" in text
            assert NOTIFICATION_IMAGE_GUIDANCE in text
        assert NOTIFICATION_IMAGE_GUIDANCE in forge_notify_skill_markdown("forge")

    def test_guidance_states_the_protocol_limits(self) -> None:
        assert "GIF, JPEG, PNG, WEBP" in NOTIFICATION_IMAGE_GUIDANCE
        assert "at most 10 MB" in NOTIFICATION_IMAGE_GUIDANCE
        assert 'image: {"file_id": "<id>"}' in NOTIFICATION_IMAGE_GUIDANCE


class TestTranscriptNotify:
    async def test_file_id_image_reaches_the_host(self) -> None:
        toolbox, _, host = _toolbox()
        result = await toolbox.call(
            "notify",
            {
                "kind": "milestone",
                "title": "Login screen done",
                "image": {"file_id": FILE_ID, "alt": "The new login screen"},
            },
        )
        assert not result.is_error
        assert host.drafts[0].image == NotificationImageRef(
            file_id=FILE_ID, alt="The new login screen"
        )

    async def test_url_image_reaches_the_host(self) -> None:
        toolbox, _, host = _toolbox()
        result = await toolbox.call(
            "notify",
            {"kind": "info", "title": "Chart", "image": {"url": "https://x.example/c.png"}},
        )
        assert not result.is_error
        assert host.drafts[0].image.url == "https://x.example/c.png"

    @pytest.mark.parametrize(
        ("image", "message"),
        [
            ({}, "exactly one of file_id"),
            ({"file_id": FILE_ID, "url": "https://x.example/a.png"}, "exactly one of file_id"),
            ({"url": "http://x.example/a.png"}, "https://"),
            ({"file_id": "not-an-id"}, "image.file_id"),
            ({"file_id": FILE_ID, "caption": "x"}, "image.caption"),
        ],
    )
    async def test_bad_images_are_refused_with_the_reason(self, image, message) -> None:
        toolbox, _, host = _toolbox()
        result = await toolbox.call("notify", {"kind": "info", "title": "x", "image": image})
        assert result.is_error
        assert message in result.text
        assert host.drafts == []


class TestFeedNotify:
    async def test_image_reaches_the_submit(self) -> None:
        toolbox, client = _feed_toolbox()
        result = await toolbox.call(
            "notify",
            {
                "kind": "milestone",
                "title": "Done",
                "idempotency_key": "k-img",
                "image": {"file_id": FILE_ID},
            },
        )
        assert not result.is_error
        name, (session_id, draft, key, _) = client.calls[-1]
        assert (name, session_id, key) == ("submit_notification", SELF, "k-img")
        assert draft.image == NotificationImageRef(file_id=FILE_ID)


class TestRestClientBody:
    @staticmethod
    def _client(seen: list[dict]) -> RestForgeClient:
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(201, json={"id": "n1", "seq": 1})

        async def factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.MockTransport(handler), base_url="http://forge"
            )

        return RestForgeClient(http_client=factory, headers={}, timeout_s=5)

    async def test_submit_sends_the_image_wire_form(self) -> None:
        seen: list[dict] = []
        draft = NotificationDraft(kind="milestone", title="Done", image={"file_id": FILE_ID})
        await self._client(seen).submit_notification(
            "s-1", draft, idempotency_key="k", instance_id=None
        )
        assert seen[0]["image"] == {"file_id": FILE_ID}
        assert seen[0]["idempotency_key"] == "k"

    async def test_submit_without_image_keeps_the_old_body(self) -> None:
        seen: list[dict] = []
        draft = NotificationDraft(kind="milestone", title="Done")
        await self._client(seen).submit_notification(
            "s-1", draft, idempotency_key="k", instance_id=None
        )
        assert "image" not in seen[0]


def test_list_summaries_report_the_image_state() -> None:
    summary = _notification_summary(
        {
            "id": "n1",
            "seq": 3,
            "kind": "milestone",
            "title": "t",
            "image": {"url": "/api/v1/forge/notifications/n1/image"},
            "image_state": "failed",
            "image_error": "the session broker answered HTTP 404",
        },
        max_body_chars=10,
    )
    assert summary["image_state"] == "failed"
    assert summary["image_error"] == "the session broker answered HTTP 404"
    assert "image" not in summary
