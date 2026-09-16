"""End-to-end contract for native Claude ``SendUserFile`` delivery.

Replays the exact tool_use/tool_result shape captured from a Forge session and
proves the broker returns the referenced bytes through the opaque tool-id +
attachment-UUID route without exposing a host path to the client.
"""

import json

import pytest
from fastapi.testclient import TestClient

from skuld.broker import ConversationTurn, app, broker


@pytest.fixture(autouse=True)
def reset_conversation():
    broker._conversation_turns = []
    yield
    broker._conversation_turns = []


def test_send_user_file_tool_pair_serves_attachment_bytes(tmp_path):
    payload = b"# Conveyor Pick\n\nRendered from the native tool.\n"
    delivered = tmp_path / "conveyor-app-ui.md"
    delivered.write_bytes(payload)
    tool_id = "toolu_01KX1zuBPfnuxsWEPdLrkJEq"
    file_uuid = "feb5e108-7543-4660-a114-d61def541c2d"

    result = {
        "attachments": [
            {
                "file_uuid": file_uuid,
                "isImage": False,
                "media_type": "text/markdown",
                "path": str(delivered),
                "size": len(payload),
            }
        ],
        "caption": "The structured prompt behind the Conveyor Pick UI mock-up.",
    }
    broker._conversation_turns = [
        ConversationTurn(
            id="a1",
            role="assistant",
            content="Delivered.",
            parts=[
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "SendUserFile",
                    "input": {
                        "files": [str(delivered)],
                        "caption": result["caption"],
                        "status": "normal",
                    },
                },
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": json.dumps(result),
                    "is_error": False,
                },
                {"type": "text", "text": "Delivered."},
            ],
        )
    ]

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(f"/api/conversation/tool-result/{tool_id}/files/{file_uuid}")

    assert response.status_code == 200, response.text
    assert response.content == payload
    assert response.headers["content-type"].startswith("text/markdown")
    assert "conveyor-app-ui.md" in response.headers["content-disposition"]


def test_send_user_file_route_rejects_unpaired_path(tmp_path):
    delivered = tmp_path / "allowed.md"
    delivered.write_text("allowed")
    other = tmp_path / "not-requested.md"
    other.write_text("must not be served")
    tool_id = "toolu_pair_guard"
    file_uuid = "01234567-89ab-4cde-8fab-0123456789ab"

    broker._conversation_turns = [
        ConversationTurn(
            id="a1",
            role="assistant",
            content="",
            parts=[
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "SendUserFile",
                    "input": {"files": [str(delivered)]},
                },
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": json.dumps(
                        {"attachments": [{"file_uuid": file_uuid, "path": str(other)}]}
                    ),
                    "is_error": False,
                },
            ],
        )
    ]

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(f"/api/conversation/tool-result/{tool_id}/files/{file_uuid}")

    assert response.status_code == 404
