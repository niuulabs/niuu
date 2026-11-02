"""Tests for on-demand vision ports, adapters, and ToolPorts."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import respx
from httpx import ConnectError, Request, Response

from ravn.adapters.tools.browser import BrowserScreenshotTool, BrowserSessionManager
from ravn.adapters.tools.vision import VisionAnalyseTool, VisionCompareTool, VisionOcrTool
from ravn.adapters.vision.anthropic import AnthropicVisionAdapter
from ravn.adapters.vision.ollama import OllamaVisionAdapter
from ravn.cli.tool_builders import _build_tools
from ravn.config import Settings
from ravn.domain.models import Session

_PNG = b"\x89PNG\r\n\x1a\n" + b"image"


def _tool(tool_type: type[VisionAnalyseTool | VisionCompareTool | VisionOcrTool], tmp_path, vision):
    return tool_type(vision, workspace=tmp_path, max_image_bytes=len(_PNG) + 1)


@pytest.mark.asyncio
async def test_analyse_accepts_browser_screenshot_data_uri_without_a_temp_file(tmp_path) -> None:
    browser = AsyncMock()
    browser.screenshot.return_value = _PNG
    sessions = BrowserSessionManager()
    sessions.set("task", browser)
    screenshot = await BrowserScreenshotTool(sessions, "task").execute({})
    vision = AsyncMock()
    vision.analyse.return_value = "The page says ready."

    result = await _tool(VisionAnalyseTool, tmp_path, vision).execute(
        {"image_source": screenshot.content, "prompt": "Read the status."}
    )

    assert json.loads(result.content) == {
        "operation": "vision_analyse",
        "result": "The page says ready.",
    }
    vision.analyse.assert_awaited_once_with(_PNG, "Read the status.")


@pytest.mark.asyncio
async def test_compare_reads_workspace_images_and_ocr_accepts_raw_screenshot_bytes(
    tmp_path,
) -> None:
    (tmp_path / "before.png").write_bytes(_PNG)
    (tmp_path / "after.png").write_bytes(_PNG + b"2")
    vision = AsyncMock()
    vision.compare.return_value = "The second image has an extra marker."
    vision.ocr.return_value = "READY"

    compare = await _tool(VisionCompareTool, tmp_path, vision).execute(
        {"image_a": "before.png", "image_b": "after.png"}
    )
    ocr = await _tool(VisionOcrTool, tmp_path, vision).execute({"image_source": _PNG})

    assert json.loads(compare.content)["result"] == "The second image has an extra marker."
    vision.compare.assert_awaited_once_with(
        _PNG, _PNG + b"2", "Describe the meaningful differences between these images."
    )
    assert json.loads(ocr.content)["result"] == "READY"
    vision.ocr.assert_awaited_once_with(_PNG)


@pytest.mark.asyncio
async def test_oversize_image_fails_without_sending_it_to_the_model(tmp_path) -> None:
    vision = AsyncMock()
    tool = VisionAnalyseTool(vision, workspace=tmp_path, max_image_bytes=len(_PNG) - 1)

    result = await tool.execute(
        {"image_source": f"data:image/png;base64,{base64.b64encode(_PNG).decode()}"}
    )

    assert result.is_error
    assert "vision.max_image_bytes" in result.content
    assert "not sent" in result.content
    vision.analyse.assert_not_awaited()


@pytest.mark.asyncio
async def test_url_images_use_web_fetch_ssrf_validation(tmp_path, monkeypatch) -> None:
    vision = AsyncMock()
    tool = _tool(VisionAnalyseTool, tmp_path, vision)
    monkeypatch.setattr(
        "ravn.adapters.tools.vision._validate_url",
        lambda url, *, allow_private_addresses: (
            "Blocked: image.example resolves to a private/reserved address"
        ),
    )

    result = await tool.execute({"image_source": "https://image.example/chart.png"})

    assert result.is_error
    assert "Blocked:" in result.content
    vision.analyse.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_transport_error_does_not_leak_a_source_url(tmp_path) -> None:
    vision = AsyncMock()
    vision.analyse.side_effect = ConnectError(
        "connection failed",
        request=Request("POST", "https://vision.example/?token=secret-value"),
    )

    result = await _tool(VisionAnalyseTool, tmp_path, vision).execute({"image_source": _PNG})

    assert result.is_error
    assert result.content == "vision model request failed; check the configured vision adapter"
    assert "secret-value" not in result.content


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_adapter_sends_image_to_bifrost_with_configured_alias() -> None:
    route = respx.post("http://bifrost.test/v1/messages").mock(
        return_value=Response(200, json={"content": [{"type": "text", "text": "chart summary"}]})
    )

    result = await AnthropicVisionAdapter(
        base_url="http://bifrost.test", model="vision-alias"
    ).analyse(
        _PNG,
        "Describe it.",
    )

    assert result == "chart summary"
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "vision-alias"
    assert body["messages"][0]["content"][0]["source"]["data"] == base64.b64encode(_PNG).decode()


@pytest.mark.asyncio
@respx.mock
async def test_ollama_adapter_sends_multimodal_chat_request() -> None:
    route = respx.post("http://ollama.test/api/chat").mock(
        return_value=Response(200, json={"message": {"content": "visible text"}})
    )

    result = await OllamaVisionAdapter(base_url="http://ollama.test", model="local-vl").ocr(_PNG)

    assert result == "visible text"
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "local-vl"
    assert body["messages"][0]["images"] == [base64.b64encode(_PNG).decode()]


def test_tool_builder_registers_all_three_vision_tool_ports(tmp_path) -> None:
    settings = Settings()
    settings.tools.enabled = ["vision_analyse", "vision_compare", "vision_ocr"]

    tools = _build_tools(settings, tmp_path, Session(), MagicMock(), None, None)

    assert {tool.name for tool in tools} == {"vision_analyse", "vision_compare", "vision_ocr"}
