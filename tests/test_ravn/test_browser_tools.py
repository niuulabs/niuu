"""Tests for the browser automation tool layer.

All tests use a mock BrowserPort — no real browser or Playwright required.
Integration tests that require a real browser are marked @pytest.mark.browser.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravn.adapters.browser._base import _ax_node_to_line, _serialise_ax_tree
from ravn.adapters.tools.browser import (
    BrowserClickTool,
    BrowserEvaluateTool,
    BrowserNavigateTool,
    BrowserScreenshotTool,
    BrowserScrollTool,
    BrowserSessionCloseTool,
    BrowserSessionManager,
    BrowserSnapshotTool,
    BrowserTypeTool,
    _is_safe_js,
    _validate_browser_url,
    build_browser_tools,
)
from ravn.ports.browser import PageSummary

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_mock_browser(
    *,
    nav_summary: PageSummary | None = None,
    snapshot_text: str = '[button "Submit" @e1]',
    screenshot_bytes: bytes = b"\x89PNG\r\n",
    evaluate_result: Any = "result",
) -> AsyncMock:
    """Return a mock BrowserPort with configurable return values."""
    browser = AsyncMock()
    browser.navigate.return_value = nav_summary or PageSummary(
        url="https://example.com",
        title="Example",
        status=200,
        text_preview="Hello world",
    )
    browser.snapshot.return_value = snapshot_text
    browser.click.return_value = None
    browser.type.return_value = None
    browser.scroll.return_value = None
    browser.screenshot.return_value = screenshot_bytes
    browser.evaluate.return_value = evaluate_result
    browser.close.return_value = None
    return browser


def make_session_manager(browser: AsyncMock, task_id: str = "task-1") -> BrowserSessionManager:
    """Return a session manager pre-loaded with *browser* for *task_id*."""
    sm = BrowserSessionManager()
    sm.set(task_id, browser)
    return sm


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


class TestValidateBrowserUrl:
    @pytest.fixture(autouse=True)
    def _mock_dns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patch socket.getaddrinfo so URL validation tests don't need real DNS."""

        def _fake_getaddrinfo(host: str, port: object, *args: object, **kwargs: object) -> list:
            # Return a benign public IP for any hostname lookup.
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(
            "ravn.adapters.tools._url_security.socket.getaddrinfo", _fake_getaddrinfo
        )

    def test_http_allowed(self) -> None:
        assert _validate_browser_url("http://example.com", [], []) is None

    def test_https_allowed(self) -> None:
        assert _validate_browser_url("https://example.com", [], []) is None

    def test_non_http_scheme_blocked(self) -> None:
        err = _validate_browser_url("ftp://example.com", [], [])
        assert err is not None
        assert "ftp" in err

    def test_file_scheme_blocked(self) -> None:
        err = _validate_browser_url("file:///etc/passwd", [], [])
        assert err is not None

    def test_blocked_origin_glob(self) -> None:
        err = _validate_browser_url("https://internal.corp", [], ["*.corp"])
        assert err is not None
        assert "internal.corp" in err

    def test_blocked_exact_host(self) -> None:
        err = _validate_browser_url("https://bad.example.com", [], ["bad.example.com"])
        assert err is not None

    def test_blocked_origin_does_not_match_other(self) -> None:
        assert _validate_browser_url("https://good.example.com", [], ["bad.example.com"]) is None

    def test_allowed_origins_permits_match(self) -> None:
        assert _validate_browser_url("https://allowed.com", ["allowed.com"], []) is None

    def test_allowed_origins_blocks_non_match(self) -> None:
        err = _validate_browser_url("https://other.com", ["allowed.com"], [])
        assert err is not None
        assert "allowed origins" in err

    def test_no_hostname_blocked(self) -> None:
        err = _validate_browser_url("https://", [], [])
        assert err is not None

    def test_blocked_takes_precedence_over_allowed(self) -> None:
        err = _validate_browser_url("https://bad.corp", ["bad.corp"], ["bad.corp"])
        assert err is not None


# ---------------------------------------------------------------------------
# JS safety check
# ---------------------------------------------------------------------------


class TestIsSafeJs:
    def test_document_title_safe(self) -> None:
        assert _is_safe_js("document.title") is True

    def test_document_url_safe(self) -> None:
        assert _is_safe_js("document.URL") is True

    def test_innertext_safe(self) -> None:
        assert _is_safe_js("document.body.innerText") is True

    def test_arbitrary_js_not_safe(self) -> None:
        assert _is_safe_js("fetch('https://evil.com')") is False

    def test_delete_call_not_safe(self) -> None:
        assert _is_safe_js("delete window.alert") is False

    def test_startswith_bypass_not_safe(self) -> None:
        # Exact-match guard: chaining after a safe prefix must be rejected.
        assert _is_safe_js("document.title; fetch('https://evil.com')") is False


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------


class TestSsrfProtection:
    """Tests for IP-based SSRF blocking in _validate_browser_url.

    No DNS mock needed — loopback/private IPs are resolved by getaddrinfo
    without any network activity.
    """

    def test_loopback_ip_blocked(self) -> None:
        err = _validate_browser_url("http://127.0.0.1", [], [])
        assert err is not None
        assert "private" in err.lower() or "reserved" in err.lower()

    def test_private_class_a_blocked(self) -> None:
        err = _validate_browser_url("http://10.0.0.1", [], [])
        assert err is not None

    def test_ipv6_loopback_blocked(self) -> None:
        err = _validate_browser_url("http://[::1]", [], [])
        assert err is not None

    def test_link_local_blocked(self) -> None:
        err = _validate_browser_url("http://169.254.1.1", [], [])
        assert err is not None


# ---------------------------------------------------------------------------
# Accessibility tree helpers
# ---------------------------------------------------------------------------


class TestAxNodeToLine:
    def test_button_node(self) -> None:
        counter = [0]
        node = {"role": "button", "name": "Log in"}
        line = _ax_node_to_line(node, counter)
        assert line == '[button "Log in" @e1]'
        assert counter[0] == 1

    def test_input_node_with_placeholder(self) -> None:
        counter = [0]
        node = {
            "role": "textbox",
            "name": "Email",
            "type": "email",
            "placeholder": "you@example.com",
        }
        line = _ax_node_to_line(node, counter)
        assert line is not None
        assert "@e1" in line
        assert 'placeholder="you@example.com"' in line

    def test_heading_with_level(self) -> None:
        counter = [0]
        node = {"role": "heading", "name": "Sign in", "level": 1}
        line = _ax_node_to_line(node, counter)
        assert "level=1" in line
        assert '"Sign in"' in line

    def test_generic_no_name_skipped(self) -> None:
        counter = [0]
        node = {"role": "generic"}
        line = _ax_node_to_line(node, counter)
        assert line is None
        assert counter[0] == 0

    def test_none_role_skipped(self) -> None:
        counter = [0]
        node = {"role": "none"}
        line = _ax_node_to_line(node, counter)
        assert line is None

    def test_disabled_attribute(self) -> None:
        counter = [0]
        node = {"role": "button", "name": "Submit", "disabled": True}
        line = _ax_node_to_line(node, counter)
        assert "disabled" in line


class TestSerialiseAxTree:
    def test_flat_tree(self) -> None:
        tree = {
            "role": "WebArea",
            "name": "Page",
            "children": [
                {"role": "button", "name": "Click me"},
                {"role": "textbox", "name": "Search", "placeholder": "Type here"},
            ],
        }
        text, handles = _serialise_ax_tree(tree)
        assert "@e1" in handles
        assert "@e2" in handles
        lines = text.splitlines()
        assert len(lines) == 3  # WebArea + 2 children

    def test_empty_tree(self) -> None:
        tree = {"role": "WebArea", "name": "Empty"}
        text, handles = _serialise_ax_tree(tree)
        assert len(handles) == 1

    def test_nested_tree(self) -> None:
        tree = {
            "role": "main",
            "name": "Content",
            "children": [
                {
                    "role": "navigation",
                    "name": "Nav",
                    "children": [
                        {"role": "link", "name": "Home"},
                    ],
                }
            ],
        }
        text, handles = _serialise_ax_tree(tree)
        assert len(handles) == 3


# ---------------------------------------------------------------------------
# BrowserSessionManager
# ---------------------------------------------------------------------------


class TestBrowserSessionManager:
    @pytest.mark.asyncio
    async def test_set_and_get(self) -> None:
        browser = make_mock_browser()
        sm = BrowserSessionManager()
        sm.set("t1", browser)
        assert sm.get("t1") is browser

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self) -> None:
        sm = BrowserSessionManager()
        assert sm.get("missing") is None

    @pytest.mark.asyncio
    async def test_close_calls_browser_close(self) -> None:
        browser = make_mock_browser()
        sm = BrowserSessionManager()
        sm.set("t1", browser)
        await sm.close("t1")
        browser.close.assert_awaited_once()
        assert sm.get("t1") is None

    @pytest.mark.asyncio
    async def test_close_all(self) -> None:
        b1, b2 = make_mock_browser(), make_mock_browser()
        sm = BrowserSessionManager()
        sm.set("t1", b1)
        sm.set("t2", b2)
        await sm.close_all()
        b1.close.assert_awaited_once()
        b2.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_nonexistent_task_is_noop(self) -> None:
        sm = BrowserSessionManager()
        await sm.close("nonexistent")  # Should not raise


# ---------------------------------------------------------------------------
# BrowserNavigateTool
# ---------------------------------------------------------------------------


class TestBrowserNavigateTool:
    def _make_tool(self, browser: AsyncMock, task_id: str = "task-1") -> BrowserNavigateTool:
        sm = BrowserSessionManager()
        return BrowserNavigateTool(
            session_manager=sm,
            task_id=task_id,
            browser_factory=lambda: browser,
        )

    @pytest.mark.asyncio
    async def test_navigate_success(self) -> None:
        browser = make_mock_browser()
        tool = self._make_tool(browser)
        result = await tool.execute({"url": "https://example.com"})
        assert not result.is_error
        assert result.content.startswith("URL: https://example.com\n")
        assert "200" in result.content

    @pytest.mark.asyncio
    async def test_navigate_creates_session(self) -> None:
        browser = make_mock_browser()
        sm = BrowserSessionManager()
        tool = BrowserNavigateTool(
            session_manager=sm,
            task_id="task-1",
            browser_factory=lambda: browser,
        )
        await tool.execute({"url": "https://example.com"})
        assert sm.get("task-1") is browser

    @pytest.mark.asyncio
    async def test_navigate_blocked_url(self) -> None:
        browser = make_mock_browser()
        sm = BrowserSessionManager()
        tool = BrowserNavigateTool(
            session_manager=sm,
            task_id="t",
            browser_factory=lambda: browser,
            blocked_origins=["*.internal"],
        )
        result = await tool.execute({"url": "https://rancher.internal"})
        assert result.is_error
        assert "Blocked" in result.content

    @pytest.mark.asyncio
    async def test_navigate_missing_url(self) -> None:
        browser = make_mock_browser()
        tool = self._make_tool(browser)
        result = await tool.execute({})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_navigate_non_http_scheme(self) -> None:
        browser = make_mock_browser()
        tool = self._make_tool(browser)
        result = await tool.execute({"url": "ftp://example.com"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_navigate_browser_exception_returns_error(self) -> None:
        browser = AsyncMock()
        browser.navigate.side_effect = RuntimeError("connection refused")
        sm = BrowserSessionManager()
        tool = BrowserNavigateTool(
            session_manager=sm,
            task_id="t",
            browser_factory=lambda: browser,
        )
        result = await tool.execute({"url": "https://example.com"})
        assert result.is_error
        assert "Navigation error" in result.content

    def test_name(self) -> None:
        sm = BrowserSessionManager()
        tool = BrowserNavigateTool(sm, "t", browser_factory=lambda: None)
        assert tool.name == "browser_navigate"

    def test_not_parallelisable(self) -> None:
        sm = BrowserSessionManager()
        tool = BrowserNavigateTool(sm, "t", browser_factory=lambda: None)
        assert tool.parallelisable is False


# ---------------------------------------------------------------------------
# BrowserSnapshotTool
# ---------------------------------------------------------------------------


class TestBrowserSnapshotTool:
    @pytest.mark.asyncio
    async def test_snapshot_returns_ax_tree(self) -> None:
        browser = make_mock_browser(snapshot_text='[button "Login" @e1]')
        sm = make_session_manager(browser)
        tool = BrowserSnapshotTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({})
        assert not result.is_error
        assert '[button "Login" @e1]' in result.content

    @pytest.mark.asyncio
    async def test_snapshot_no_session_returns_error(self) -> None:
        sm = BrowserSessionManager()
        tool = BrowserSnapshotTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({})
        assert result.is_error
        assert "browser_navigate" in result.content

    @pytest.mark.asyncio
    async def test_snapshot_browser_exception(self) -> None:
        browser = AsyncMock()
        browser.snapshot.side_effect = RuntimeError("page crashed")
        sm = make_session_manager(browser)
        tool = BrowserSnapshotTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({})
        assert result.is_error

    def test_name(self) -> None:
        sm = BrowserSessionManager()
        assert BrowserSnapshotTool(sm, "t").name == "browser_snapshot"


# ---------------------------------------------------------------------------
# BrowserClickTool
# ---------------------------------------------------------------------------


class TestBrowserClickTool:
    @pytest.mark.asyncio
    async def test_click_success(self) -> None:
        browser = make_mock_browser()
        sm = make_session_manager(browser)
        tool = BrowserClickTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({"selector": "@e1"})
        assert not result.is_error
        assert "@e1" in result.content
        browser.click.assert_awaited_once_with("@e1")

    @pytest.mark.asyncio
    async def test_click_missing_selector(self) -> None:
        browser = make_mock_browser()
        sm = make_session_manager(browser)
        tool = BrowserClickTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_click_no_session(self) -> None:
        sm = BrowserSessionManager()
        tool = BrowserClickTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({"selector": "#btn"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_click_browser_exception(self) -> None:
        browser = AsyncMock()
        browser.click.side_effect = RuntimeError("element not found")
        sm = make_session_manager(browser)
        tool = BrowserClickTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({"selector": "#missing"})
        assert result.is_error

    def test_permission(self) -> None:
        sm = BrowserSessionManager()
        assert BrowserClickTool(sm, "t").required_permission == "browser:write"


# ---------------------------------------------------------------------------
# BrowserTypeTool
# ---------------------------------------------------------------------------


class TestBrowserTypeTool:
    @pytest.mark.asyncio
    async def test_type_success(self) -> None:
        browser = make_mock_browser()
        sm = make_session_manager(browser)
        tool = BrowserTypeTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({"selector": "@e2", "text": "hello@example.com"})
        assert not result.is_error
        browser.type.assert_awaited_once_with("@e2", "hello@example.com")

    @pytest.mark.asyncio
    async def test_type_missing_selector(self) -> None:
        browser = make_mock_browser()
        sm = make_session_manager(browser)
        tool = BrowserTypeTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({"text": "hello"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_type_no_session(self) -> None:
        sm = BrowserSessionManager()
        tool = BrowserTypeTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({"selector": "#q", "text": "hello"})
        assert result.is_error


# ---------------------------------------------------------------------------
# BrowserScrollTool
# ---------------------------------------------------------------------------


class TestBrowserScrollTool:
    @pytest.mark.asyncio
    async def test_scroll_down(self) -> None:
        browser = make_mock_browser()
        sm = make_session_manager(browser)
        tool = BrowserScrollTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({"direction": "down", "amount": 2})
        assert not result.is_error
        browser.scroll.assert_awaited_once_with("down", 2)

    @pytest.mark.asyncio
    async def test_scroll_default_amount(self) -> None:
        browser = make_mock_browser()
        sm = make_session_manager(browser)
        tool = BrowserScrollTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({"direction": "up"})
        assert not result.is_error
        browser.scroll.assert_awaited_once_with("up", 3)

    @pytest.mark.asyncio
    async def test_scroll_invalid_direction(self) -> None:
        browser = make_mock_browser()
        sm = make_session_manager(browser)
        tool = BrowserScrollTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({"direction": "sideways"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_scroll_no_session(self) -> None:
        sm = BrowserSessionManager()
        tool = BrowserScrollTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({"direction": "down"})
        assert result.is_error


# ---------------------------------------------------------------------------
# BrowserScreenshotTool
# ---------------------------------------------------------------------------


class TestBrowserScreenshotTool:
    @pytest.mark.asyncio
    async def test_screenshot_returns_base64(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        browser = make_mock_browser(screenshot_bytes=png)
        sm = make_session_manager(browser)
        tool = BrowserScreenshotTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({})
        assert not result.is_error
        assert result.content.startswith("data:image/png;base64,")
        # Verify it round-trips back to the original bytes.
        b64_part = result.content.split(",", 1)[1]
        assert base64.b64decode(b64_part) == png

    @pytest.mark.asyncio
    async def test_screenshot_no_session(self) -> None:
        sm = BrowserSessionManager()
        tool = BrowserScreenshotTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({})
        assert result.is_error

    def test_permission(self) -> None:
        sm = BrowserSessionManager()
        assert BrowserScreenshotTool(sm, "t").required_permission == "browser:read"


# ---------------------------------------------------------------------------
# BrowserEvaluateTool
# ---------------------------------------------------------------------------


class TestBrowserEvaluateTool:
    @pytest.mark.asyncio
    async def test_safe_js_allowed(self) -> None:
        browser = make_mock_browser(evaluate_result="My Page")
        sm = make_session_manager(browser)
        tool = BrowserEvaluateTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({"js": "document.title"})
        assert not result.is_error
        assert "My Page" in result.content

    @pytest.mark.asyncio
    async def test_unsafe_js_blocked_by_default(self) -> None:
        browser = make_mock_browser()
        sm = make_session_manager(browser)
        tool = BrowserEvaluateTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({"js": "fetch('https://evil.com')"})
        assert result.is_error
        assert "read-only" in result.content

    @pytest.mark.asyncio
    async def test_full_js_allowed_when_enabled(self) -> None:
        browser = make_mock_browser(evaluate_result=42)
        sm = make_session_manager(browser)
        tool = BrowserEvaluateTool(session_manager=sm, task_id="task-1", full_js=True)
        result = await tool.execute({"js": "1 + 1", "full_js": True})
        assert not result.is_error
        assert "42" in result.content

    @pytest.mark.asyncio
    async def test_full_js_flag_blocked_when_not_configured(self) -> None:
        browser = make_mock_browser()
        sm = make_session_manager(browser)
        # full_js=False (default)
        tool = BrowserEvaluateTool(session_manager=sm, task_id="task-1", full_js=False)
        result = await tool.execute({"js": "document.cookie", "full_js": True})
        assert result.is_error
        assert "FULL_ACCESS" in result.content

    @pytest.mark.asyncio
    async def test_missing_js_returns_error(self) -> None:
        browser = make_mock_browser()
        sm = make_session_manager(browser)
        tool = BrowserEvaluateTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_no_session_returns_error(self) -> None:
        sm = BrowserSessionManager()
        tool = BrowserEvaluateTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({"js": "document.title"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_json_serialisation(self) -> None:
        browser = make_mock_browser(evaluate_result={"key": "value"})
        sm = make_session_manager(browser)
        tool = BrowserEvaluateTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({"js": "document.URL"})
        assert not result.is_error
        assert '"key"' in result.content


# ---------------------------------------------------------------------------
# BrowserSessionCloseTool
# ---------------------------------------------------------------------------


class TestBrowserSessionCloseTool:
    @pytest.mark.asyncio
    async def test_close_active_session(self) -> None:
        browser = make_mock_browser()
        sm = make_session_manager(browser)
        tool = BrowserSessionCloseTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({})
        assert not result.is_error
        assert "closed" in result.content.lower()
        browser.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_no_session_is_ok(self) -> None:
        sm = BrowserSessionManager()
        tool = BrowserSessionCloseTool(session_manager=sm, task_id="task-1")
        result = await tool.execute({})
        assert not result.is_error
        assert "No active" in result.content

    def test_name(self) -> None:
        sm = BrowserSessionManager()
        assert BrowserSessionCloseTool(sm, "t").name == "browser_session_close"


# ---------------------------------------------------------------------------
# build_browser_tools factory
# ---------------------------------------------------------------------------


class TestBuildBrowserTools:
    def test_returns_eight_tools(self) -> None:
        tools = build_browser_tools("task-1")
        assert len(tools) == 8

    def test_tool_names_unique(self) -> None:
        tools = build_browser_tools("task-1")
        names = [t.name for t in tools]
        assert len(names) == len(set(names))

    def test_expected_names(self) -> None:
        tools = build_browser_tools("task-1")
        names = {t.name for t in tools}
        expected = {
            "browser_navigate",
            "browser_snapshot",
            "browser_click",
            "browser_type",
            "browser_scroll",
            "browser_screenshot",
            "browser_evaluate",
            "browser_session_close",
        }
        assert names == expected

    def test_all_have_input_schema(self) -> None:
        for tool in build_browser_tools("task-1"):
            schema = tool.input_schema
            assert isinstance(schema, dict)
            assert schema.get("type") == "object"

    def test_none_parallelisable(self) -> None:
        for tool in build_browser_tools("task-1"):
            assert tool.parallelisable is False, f"{tool.name} should not be parallelisable"


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestBrowserConfig:
    def test_default_config_loads(self) -> None:
        from ravn.config import BrowserConfig

        cfg = BrowserConfig()
        assert cfg.backend == "local"
        assert cfg.headless is True
        assert cfg.timeout_ms == 30_000
        assert cfg.allowed_origins == []
        assert cfg.blocked_origins == []

    def test_browserbase_config_defaults(self) -> None:
        from ravn.config import BrowserbaseConfig

        cfg = BrowserbaseConfig()
        assert cfg.api_key_env == "BROWSERBASE_API_KEY"
        assert cfg.stealth is False

    def test_settings_has_browser_field(self) -> None:
        from ravn.config import Settings

        s = Settings()
        assert hasattr(s, "browser")
        assert s.browser.backend == "local"


# ---------------------------------------------------------------------------
# LocalPlaywrightAdapter — mocked page state (no real browser required)
# ---------------------------------------------------------------------------


def _make_mock_page() -> MagicMock:
    """Return a MagicMock mimicking a Playwright page object."""
    page = MagicMock()
    page.url = "https://example.com"
    page.title = AsyncMock(return_value="Example")
    page.goto = AsyncMock(return_value=MagicMock(status=200))
    page.evaluate = AsyncMock(return_value="body text")
    page.accessibility = MagicMock()
    page.accessibility.snapshot = AsyncMock(
        return_value={
            "role": "WebArea",
            "name": "Page",
            "children": [{"role": "button", "name": "Click me"}],
        }
    )
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.mouse = MagicMock()
    page.mouse.wheel = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG")
    page.close = AsyncMock()
    page.set_default_timeout = MagicMock()

    first_mock = MagicMock()
    first_mock.click = AsyncMock()
    first_mock.fill = AsyncMock()
    locator_mock = MagicMock()
    locator_mock.first = first_mock
    locator_mock.fill = AsyncMock()
    page.get_by_role = MagicMock(return_value=locator_mock)
    page.get_by_placeholder = MagicMock(return_value=locator_mock)
    return page


def _inject_page(adapter: Any, page: Any) -> None:
    """Inject a mock page directly into the adapter, bypassing _ensure_browser."""
    adapter._page = page
    adapter._browser = MagicMock()
    adapter._browser.close = AsyncMock()
    adapter._playwright = MagicMock()
    adapter._playwright.stop = AsyncMock()


class TestLocalPlaywrightAdapter:
    @pytest.mark.asyncio
    async def test_navigate_success(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        page = _make_mock_page()
        _inject_page(adapter, page)
        summary = await adapter.navigate("https://example.com")
        assert summary.status == 200
        assert summary.url == "https://example.com"
        assert summary.title == "Example"

    @pytest.mark.asyncio
    async def test_navigate_evaluate_exception_still_returns(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        page = _make_mock_page()
        page.evaluate = AsyncMock(side_effect=RuntimeError("no body"))
        _inject_page(adapter, page)
        summary = await adapter.navigate("https://example.com")
        assert summary.text_preview == ""

    @pytest.mark.asyncio
    async def test_navigate_raises_when_playwright_missing(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        # No injected page — will try to import playwright.
        import sys

        orig = sys.modules.get("playwright.async_api")
        sys.modules["playwright.async_api"] = None  # type: ignore[assignment]
        try:
            with pytest.raises((RuntimeError, ImportError)):
                await adapter.navigate("https://example.com")
        finally:
            if orig is None:
                sys.modules.pop("playwright.async_api", None)
            else:
                sys.modules["playwright.async_api"] = orig

    @pytest.mark.asyncio
    async def test_snapshot_returns_ax_tree_text(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        _inject_page(adapter, _make_mock_page())
        snapshot = await adapter.snapshot()
        assert "button" in snapshot
        assert "@e" in snapshot

    @pytest.mark.asyncio
    async def test_snapshot_empty_page(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        page = _make_mock_page()
        page.accessibility.snapshot = AsyncMock(return_value=None)
        _inject_page(adapter, page)
        snapshot = await adapter.snapshot()
        assert snapshot == "(empty page)"

    @pytest.mark.asyncio
    async def test_close_releases_resources(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        page = _make_mock_page()
        _inject_page(adapter, page)
        await adapter.close()
        page.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_without_open_is_noop(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        await adapter.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_screenshot(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        _inject_page(adapter, _make_mock_page())
        result = await adapter.screenshot()
        assert result == b"\x89PNG"

    @pytest.mark.asyncio
    async def test_evaluate(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        page = _make_mock_page()
        page.evaluate = AsyncMock(return_value="42")
        _inject_page(adapter, page)
        result = await adapter.evaluate("1 + 1")
        assert result == "42"

    @pytest.mark.asyncio
    async def test_scroll_all_directions(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        page = _make_mock_page()
        _inject_page(adapter, page)
        for direction in ("up", "down", "left", "right"):
            await adapter.scroll(direction, 1)
        assert page.mouse.wheel.await_count == 4

    @pytest.mark.asyncio
    async def test_scroll_unknown_direction_defaults_to_down(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        page = _make_mock_page()
        _inject_page(adapter, page)
        await adapter.scroll("sideways", 1)
        page.mouse.wheel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_click_with_handle_uses_role_locator(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        page = _make_mock_page()
        _inject_page(adapter, page)
        # Load handle map via snapshot.
        # Default snapshot: WebArea "Page" @e1, button "Click me" @e2.
        await adapter.snapshot()
        await adapter.click("@e2")
        page.get_by_role.assert_called_once_with("button", name="Click me")

    @pytest.mark.asyncio
    async def test_click_with_css_selector(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        _inject_page(adapter, _make_mock_page())
        await adapter.click("#submit")
        adapter._page.click.assert_awaited_once_with("#submit")

    @pytest.mark.asyncio
    async def test_type_with_placeholder_handle(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        page = _make_mock_page()
        # Custom snapshot: textbox with placeholder.
        page.accessibility.snapshot = AsyncMock(
            return_value={
                "role": "WebArea",
                "name": "Page",
                "children": [
                    {
                        "role": "textbox",
                        "name": "Email",
                        "placeholder": "you@example.com",
                    }
                ],
            }
        )
        _inject_page(adapter, page)
        # After snapshot: WebArea @e1, textbox @e2.
        await adapter.snapshot()
        await adapter.type("@e2", "hello@test.com")
        page.get_by_placeholder.assert_called_once_with("you@example.com")

    @pytest.mark.asyncio
    async def test_type_with_name_handle(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        page = _make_mock_page()
        _inject_page(adapter, page)
        # Default snapshot: WebArea @e1, button "Click me" @e2.
        await adapter.snapshot()
        await adapter.type("@e2", "hello")
        # Should use get_by_role since button has name but no placeholder.
        page.get_by_role.assert_called_once_with("button", name="Click me")

    @pytest.mark.asyncio
    async def test_type_with_css_selector(self) -> None:
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        adapter = LocalPlaywrightAdapter()
        _inject_page(adapter, _make_mock_page())
        await adapter.type("#q", "hello")
        adapter._page.fill.assert_awaited_once_with("#q", "hello")


# ---------------------------------------------------------------------------
# build_browser_tools — factory backend selection
# ---------------------------------------------------------------------------


class TestBuildBrowserToolsBackendSelection:
    def test_local_backend_creates_local_adapter(self) -> None:
        tools = build_browser_tools("t", backend="local")
        navigate_tool = next(t for t in tools if t.name == "browser_navigate")
        with patch("ravn.adapters.browser.local.LocalPlaywrightAdapter") as mock_adapter:
            mock_adapter.return_value = make_mock_browser()
            navigate_tool._browser_factory()  # type: ignore[attr-defined]
        mock_adapter.assert_called_once()

    def test_browserbase_backend_creates_browserbase_adapter(self) -> None:
        tools = build_browser_tools("t", backend="browserbase")
        navigate_tool = next(t for t in tools if t.name == "browser_navigate")
        with patch("ravn.adapters.browser.browserbase.BrowserbaseAdapter") as mock_adapter:
            mock_adapter.return_value = make_mock_browser()
            navigate_tool._browser_factory()  # type: ignore[attr-defined]
        mock_adapter.assert_called_once()


# ---------------------------------------------------------------------------
# BrowserbaseAdapter — mocked page state (no real browser required)
# ---------------------------------------------------------------------------


def _make_bb_page() -> MagicMock:
    """Return a mock Playwright page suitable for Browserbase tests."""
    return _make_mock_page()


def _inject_bb_page(adapter: Any, page: Any) -> None:
    """Inject a mock page directly into a BrowserbaseAdapter."""
    adapter._page = page
    adapter._browser = MagicMock()
    adapter._browser.close = AsyncMock()
    adapter._playwright = MagicMock()
    adapter._playwright.stop = AsyncMock()
    adapter._session_id = "bb-session-123"


class TestBrowserbaseAdapter:
    @pytest.mark.asyncio
    async def test_navigate_success(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        _inject_bb_page(adapter, _make_bb_page())
        summary = await adapter.navigate("https://example.com")
        assert summary.status == 200
        assert summary.url == "https://example.com"

    @pytest.mark.asyncio
    async def test_snapshot_returns_ax_text(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        _inject_bb_page(adapter, _make_bb_page())
        snapshot = await adapter.snapshot()
        assert "button" in snapshot
        assert "@e" in snapshot

    @pytest.mark.asyncio
    async def test_snapshot_empty_page(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        page = _make_bb_page()
        page.accessibility.snapshot = AsyncMock(return_value=None)
        _inject_bb_page(adapter, page)
        snapshot = await adapter.snapshot()
        assert snapshot == "(empty page)"

    @pytest.mark.asyncio
    async def test_close_releases_resources(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        page = _make_bb_page()
        _inject_bb_page(adapter, page)
        await adapter.close()
        page.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_without_open_is_noop(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        await adapter.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_screenshot(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        _inject_bb_page(adapter, _make_bb_page())
        result = await adapter.screenshot()
        assert result == b"\x89PNG"

    @pytest.mark.asyncio
    async def test_evaluate(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        page = _make_bb_page()
        page.evaluate = AsyncMock(return_value="result")
        _inject_bb_page(adapter, page)
        result = await adapter.evaluate("document.title")
        assert result == "result"

    @pytest.mark.asyncio
    async def test_scroll_all_directions(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        page = _make_bb_page()
        _inject_bb_page(adapter, page)
        for direction in ("up", "down", "left", "right"):
            await adapter.scroll(direction, 1)
        assert page.mouse.wheel.await_count == 4

    @pytest.mark.asyncio
    async def test_scroll_unknown_direction_defaults(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        page = _make_bb_page()
        _inject_bb_page(adapter, page)
        await adapter.scroll("sideways", 1)
        page.mouse.wheel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_click_with_handle(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        page = _make_bb_page()
        _inject_bb_page(adapter, page)
        await adapter.snapshot()  # Load handle map; @e1=WebArea, @e2=button
        await adapter.click("@e2")
        page.get_by_role.assert_called_with("button", name="Click me")

    @pytest.mark.asyncio
    async def test_click_with_css_selector(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        _inject_bb_page(adapter, _make_bb_page())
        await adapter.click("#btn")
        adapter._page.click.assert_awaited_once_with("#btn")

    @pytest.mark.asyncio
    async def test_type_with_placeholder(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        page = _make_bb_page()
        page.accessibility.snapshot = AsyncMock(
            return_value={
                "role": "WebArea",
                "name": "Page",
                "children": [{"role": "textbox", "name": "Email", "placeholder": "email"}],
            }
        )
        _inject_bb_page(adapter, page)
        await adapter.snapshot()
        await adapter.type("@e2", "test@example.com")
        page.get_by_placeholder.assert_called_with("email")

    @pytest.mark.asyncio
    async def test_type_with_css_selector(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        _inject_bb_page(adapter, _make_bb_page())
        await adapter.type("#q", "hello")
        adapter._page.fill.assert_awaited_once_with("#q", "hello")

    @pytest.mark.asyncio
    async def test_ensure_browser_raises_without_api_key(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter()  # no key, no env var
        with pytest.raises(RuntimeError, match="API key"):
            await adapter.navigate("https://example.com")

    @pytest.mark.asyncio
    async def test_navigate_evaluate_exception_still_returns(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        page = _make_bb_page()
        page.evaluate = AsyncMock(side_effect=RuntimeError("no body"))
        _inject_bb_page(adapter, page)
        summary = await adapter.navigate("https://example.com")
        assert summary.text_preview == ""

    @pytest.mark.asyncio
    async def test_type_with_name_fallback(self) -> None:
        from ravn.adapters.browser.browserbase import BrowserbaseAdapter

        adapter = BrowserbaseAdapter(api_key="test-key")
        page = _make_bb_page()
        _inject_bb_page(adapter, page)
        await adapter.snapshot()
        # @e2 = button "Click me" — no placeholder, falls back to get_by_role
        await adapter.type("@e2", "hello")
        page.get_by_role.assert_called_with("button", name="Click me")
