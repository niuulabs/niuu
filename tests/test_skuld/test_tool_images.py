"""Image events survive Codex transport and shallow history without loading bytes."""

import base64
import json
from unittest.mock import AsyncMock

import pytest

from skuld.conversation_shallow import elide_tool_result_block
from skuld.tool_images import image_metadata, image_payloads
from skuld.tool_result_preview import extract_image_bytes
from skuld.transports.codex_ws import CodexWebSocketTransport


@pytest.mark.parametrize(
    "block",
    [
        {"type": "image", "source": {"media_type": "image/png", "data": "AQID"}},
        {"type": "image", "mimeType": "image/png", "data": "AQID"},
        {"type": "input_image", "image_url": "data:image/png;base64,AQID"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AQID"}},
        {"type": "Image", "imageUrl": "data:image/png;base64,AQID"},
    ],
)
def test_recognizes_typed_image_content(block):
    content = [{"type": "text", "text": "Description"}, block]
    assert image_metadata(content) == [{"index": 0, "mime_type": "image/png"}]
    assert extract_image_bytes(json.dumps(content)) == (b"\x01\x02\x03", "image/png")


@pytest.mark.parametrize(
    "content",
    [
        None,
        4,
        "chart.png",
        "{not json",
        {"file": "a.png"},
        [{"type": "text", "text": '{"type":"image","source":{"data":"AQID"}}'}],
        [{"type": "input_image", "image_url": "https://remote.test/file.png"}],
        [{"type": "input_image", "image_url": "data:image/png,not-base64"}],
    ],
)
def test_does_not_invent_images_or_fetch_remote_urls(content):
    assert image_payloads(content) == []


def test_shallow_history_has_one_hint_per_image_and_no_encoded_pixels():
    content = [
        {"type": "input_text", "text": "Two images"},
        {
            "type": "input_image",
            "image_url": "data:image/png;base64," + base64.b64encode(b"a" * 2000).decode(),
        },
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64," + base64.b64encode(b"b" * 2000).decode(),
        },
    ]
    part = elide_tool_result_block(
        {"type": "tool_result", "tool_use_id": "read", "content": content}
    )
    assert part["is_image"] is True
    assert part["mime_type"] == "image/png"
    assert part["image_previews"] == [
        {"index": 0, "mime_type": "image/png"},
        {"index": 1, "mime_type": "image/jpeg"},
    ]
    assert "content" not in part
    assert extract_image_bytes(content, image_index=1) == (b"b" * 2000, "image/jpeg")
    assert extract_image_bytes(content, image_index=2) is None
    assert extract_image_bytes(content, image_index=-1) is None


async def test_codex_mixed_raw_tool_result_keeps_images_and_text(tmp_path):
    transport = CodexWebSocketTransport(
        workspace_dir=str(tmp_path), model="o4-mini", codex_port=19999
    )
    transport._emit = AsyncMock()
    content = [
        {"type": "input_text", "text": "Script completed"},
        {"type": "input_image", "image_url": "data:image/png;base64,AQID"},
    ]
    await transport._handle_server_message(
        {
            "method": "rawResponseItem/completed",
            "params": {
                "item": {"type": "custom_tool_call_output", "call_id": "images", "output": content}
            },
        }
    )
    events = [call.args[0] for call in transport._emit.call_args_list]
    result = next(
        event["content_block"]
        for event in events
        if event.get("content_block", {}).get("type") == "tool_result"
    )
    assert json.loads(result["content"]) == content
    persisted = next(event["message"]["content"][0] for event in events if event["type"] == "user")
    assert json.loads(persisted["content"]) == content
