"""Tests for on-demand vision ports, adapters, and ToolPorts."""

from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import respx
from httpx import ConnectError, Request, Response

from ravn.adapters.permission.allow_deny import AllowAllPermission, AllowListPermission
from ravn.adapters.tools.browser import BrowserScreenshotTool, BrowserSessionManager
from ravn.adapters.tools.hooks import HookPipeline, PermissionHook
from ravn.adapters.tools.vision import VisionAnalyseTool, VisionCompareTool, VisionOcrTool
from ravn.adapters.vision.anthropic import AnthropicVisionAdapter
from ravn.adapters.vision.ollama import OllamaVisionAdapter
from ravn.cap_telemetry import cap_hit_counts
from ravn.cli.tool_builders import _build_tools
from ravn.config import Settings
from ravn.domain.models import Session
from ravn.registry import ToolRegistry

_PNG = b"\x89PNG\r\n\x1a\n" + b"image"


def _tool(
    tool_type: type[VisionAnalyseTool | VisionCompareTool | VisionOcrTool],
    tmp_path,
    vision,
    *,
    permission=None,
    timeout: float = 30.0,
):
    return tool_type(
        vision,
        workspace=tmp_path,
        max_image_bytes=len(_PNG) + 1,
        permission=permission,
        timeout=timeout,
    )


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
    tool = VisionAnalyseTool(
        vision,
        workspace=tmp_path,
        max_image_bytes=len(_PNG) - 1,
    )

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
    tool = _tool(VisionAnalyseTool, tmp_path, vision, permission=AllowAllPermission())
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
async def test_url_images_require_web_fetch_permission_even_with_file_read(tmp_path) -> None:
    vision = AsyncMock()
    tool = _tool(
        VisionAnalyseTool,
        tmp_path,
        vision,
        permission=AllowListPermission({"file:read"}),
    )

    result = await tool.execute({"image_source": "https://image.example/chart.png"})

    assert result.is_error
    assert result.content == "Permission denied: vision URL input requires web:fetch"
    vision.analyse.assert_not_awaited()


@pytest.mark.asyncio
@respx.mock
async def test_url_images_fetch_when_web_fetch_permission_is_granted(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "ravn.adapters.tools.vision._validate_url",
        lambda url, *, allow_private_addresses: None,
    )
    respx.get("https://image.example/chart.png").mock(return_value=Response(200, content=_PNG))
    vision = AsyncMock()
    vision.analyse.return_value = "chart"
    tool = _tool(
        VisionAnalyseTool,
        tmp_path,
        vision,
        permission=AllowListPermission({"web:fetch"}),
    )

    result = await tool.execute({"image_source": "https://image.example/chart.png"})

    assert not result.is_error
    vision.analyse.assert_awaited_once_with(_PNG, "Describe this image in detail.")


@pytest.mark.asyncio
@respx.mock
async def test_registry_dispatch_uses_web_fetch_permission_for_url_images(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "ravn.adapters.tools.vision._validate_url",
        lambda url, *, allow_private_addresses: None,
    )
    respx.get("https://image.example/chart.png").mock(return_value=Response(200, content=_PNG))
    vision = AsyncMock()
    vision.analyse.return_value = "chart"
    tool = _tool(
        VisionAnalyseTool,
        tmp_path,
        vision,
        permission=AllowListPermission({"web:fetch"}),
    )
    registry = ToolRegistry(
        HookPipeline(pre_hooks=[PermissionHook(AllowListPermission({"web:fetch"}))])
    )
    registry.register(tool)

    result = await registry.dispatch(
        "vision_analyse",
        {"image_source": "https://image.example/chart.png"},
        call_id="vision-url",
    )

    assert not result.is_error
    vision.analyse.assert_awaited_once_with(_PNG, "Describe this image in detail.")


@pytest.mark.asyncio
async def test_registry_dispatch_denies_url_images_with_file_read_only(tmp_path) -> None:
    vision = AsyncMock()
    tool = _tool(
        VisionAnalyseTool,
        tmp_path,
        vision,
        permission=AllowListPermission({"file:read"}),
    )
    registry = ToolRegistry(
        HookPipeline(pre_hooks=[PermissionHook(AllowListPermission({"file:read"}))])
    )
    registry.register(tool)

    result = await registry.dispatch(
        "vision_analyse",
        {"image_source": "https://image.example/chart.png"},
        call_id="vision-url",
    )

    assert result.is_error
    assert "web:fetch" in result.content
    vision.analyse.assert_not_awaited()


@pytest.mark.asyncio
async def test_registry_dispatch_denies_mixed_sources_without_file_read(tmp_path) -> None:
    (tmp_path / "local.png").write_bytes(_PNG)
    vision = AsyncMock()
    tool = _tool(
        VisionCompareTool,
        tmp_path,
        vision,
        permission=AllowListPermission({"web:fetch"}),
    )
    registry = ToolRegistry(
        HookPipeline(pre_hooks=[PermissionHook(AllowListPermission({"web:fetch"}))])
    )
    registry.register(tool)

    result = await registry.dispatch(
        "vision_compare",
        {"image_a": "local.png", "image_b": "https://image.example/chart.png"},
        call_id="vision-mixed",
    )

    assert result.is_error
    assert "file:read" in result.content
    vision.compare.assert_not_awaited()


@pytest.mark.asyncio
@respx.mock
async def test_registry_dispatch_allows_mixed_sources_with_both_permissions(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / "local.png").write_bytes(_PNG)
    monkeypatch.setattr(
        "ravn.adapters.tools.vision._validate_url",
        lambda url, *, allow_private_addresses: None,
    )
    respx.get("https://image.example/chart.png").mock(return_value=Response(200, content=_PNG))
    vision = AsyncMock()
    vision.compare.return_value = "same"
    permission = AllowListPermission({"file:read", "web:fetch"})
    tool = _tool(VisionCompareTool, tmp_path, vision, permission=permission)
    registry = ToolRegistry(HookPipeline(pre_hooks=[PermissionHook(permission)]))
    registry.register(tool)

    result = await registry.dispatch(
        "vision_compare",
        {"image_a": "local.png", "image_b": "https://image.example/chart.png"},
        call_id="vision-mixed",
    )

    assert not result.is_error
    vision.compare.assert_awaited_once_with(
        _PNG,
        _PNG,
        "Describe the meaningful differences between these images.",
    )


@pytest.mark.asyncio
@respx.mock
async def test_redirect_to_private_url_is_rejected_before_request(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "ravn.adapters.tools.vision._validate_url",
        lambda url, *, allow_private_addresses: (
            "Blocked: 127.0.0.1 resolves to a private/reserved address"
            if "127.0.0.1" in url
            else None
        ),
    )
    respx.get("https://image.example/chart.png").mock(
        return_value=Response(302, headers={"location": "http://127.0.0.1/private.png"})
    )
    private = respx.get("http://127.0.0.1/private.png").mock(
        return_value=Response(200, content=_PNG)
    )
    vision = AsyncMock()
    tool = _tool(VisionAnalyseTool, tmp_path, vision, permission=AllowAllPermission())

    result = await tool.execute({"image_source": "https://image.example/chart.png"})

    assert result.is_error
    assert result.content.startswith("Blocked:")
    assert not private.called
    vision.analyse.assert_not_awaited()


@pytest.mark.asyncio
@respx.mock
async def test_redirect_cycle_fails_without_a_guessed_redirect_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "ravn.adapters.tools.vision._validate_url",
        lambda url, *, allow_private_addresses: None,
    )
    redirect = respx.get("https://image.example/chart.png").mock(
        return_value=Response(302, headers={"location": "/chart.png"})
    )
    vision = AsyncMock()
    tool = _tool(VisionAnalyseTool, tmp_path, vision, permission=AllowAllPermission())

    result = await tool.execute({"image_source": "https://image.example/chart.png"})

    assert result.is_error
    assert result.content == "could not fetch image URL: redirect cycle detected"
    assert redirect.call_count == 1
    vision.analyse.assert_not_awaited()


@pytest.mark.asyncio
@respx.mock
async def test_distinct_redirect_chain_obeys_one_operation_timeout(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "ravn.adapters.tools.vision._validate_url",
        lambda url, *, allow_private_addresses: None,
    )

    async def delayed_redirect(location: str) -> Response:
        await asyncio.sleep(0.02)
        return Response(302, headers={"location": location})

    respx.get("https://image.example/one.png").mock(
        side_effect=lambda request: delayed_redirect("https://image.example/two.png")
    )
    respx.get("https://image.example/two.png").mock(
        side_effect=lambda request: delayed_redirect("https://image.example/three.png")
    )
    vision = AsyncMock()
    tool = _tool(
        VisionAnalyseTool,
        tmp_path,
        vision,
        permission=AllowAllPermission(),
        timeout=0.03,
    )

    result = await tool.execute({"image_source": "https://image.example/one.png"})

    assert result.is_error
    assert result.content == "vision URL retrieval timed out after 0.03s"
    assert cap_hit_counts() == {"tools.web.fetch.timeout": 1}
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
