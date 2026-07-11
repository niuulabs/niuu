"""Browser automation tools — ToolPort implementations for browser control.

All 8 tools share a single BrowserPort session scoped to the current task_id.
The session is auto-closed when ``browser_session_close`` is called or when the
adapter is garbage-collected.

Permission tiers (from the design doc):
- READ_ONLY  : browser_navigate, browser_snapshot, browser_screenshot
- WORKSPACE_WRITE : browser_click, browser_type, browser_scroll
- FULL_ACCESS : browser_evaluate (with full_js=true)
"""

from __future__ import annotations

import base64
import fnmatch
import json
import logging
from typing import Any
from urllib.parse import urlparse

from ravn.adapters.tools._url_security import check_ssrf
from ravn.domain.models import ToolResult
from ravn.ports.browser import BrowserPort, PageSummary
from ravn.ports.tool import ToolPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URL security
# ---------------------------------------------------------------------------

# Schemes permitted by browser_navigate.
_ALLOWED_SCHEMES = {"http", "https"}

# JS expressions that are always considered safe (read-only exact matches).
_SAFE_JS_PATTERNS: frozenset[str] = frozenset(
    {
        "document.title",
        "document.URL",
        "window.location",
        "document.body.innerText",
        "document.readyState",
    }
)


def _validate_browser_url(
    url: str,
    allowed_origins: list[str],
    blocked_origins: list[str],
) -> str | None:
    """Return an error string if *url* is blocked, else None.

    Checks:
    1. Scheme must be http or https.
    2. URL must not match any ``blocked_origins`` glob pattern.
    3. If ``allowed_origins`` is non-empty, URL must match at least one pattern.
    4. Hostname must not resolve to a private/reserved IP range (SSRF protection).
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return f"Blocked: only http/https URLs are allowed (got '{parsed.scheme}')"

    hostname = parsed.hostname or ""
    if not hostname:
        return "Blocked: URL has no hostname"

    for pattern in blocked_origins:
        if fnmatch.fnmatch(hostname, pattern):
            return f"Blocked: '{hostname}' matches blocked origin pattern '{pattern}'"

    if allowed_origins:
        if not any(fnmatch.fnmatch(hostname, p) for p in allowed_origins):
            return (
                f"Blocked: '{hostname}' is not in the allowed origins list. "
                f"Allowed: {allowed_origins}"
            )

    return check_ssrf(hostname)


def _is_safe_js(js: str) -> bool:
    """Return True if the JS expression exactly matches a read-only safe expression."""
    return js.strip() in _SAFE_JS_PATTERNS


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------


class BrowserSessionManager:
    """Holds one BrowserPort instance per task_id.

    Ensures sessions don't bleed across concurrent agents.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, BrowserPort] = {}

    def get(self, task_id: str) -> BrowserPort | None:
        return self._sessions.get(task_id)

    def set(self, task_id: str, browser: BrowserPort) -> None:
        self._sessions[task_id] = browser

    async def close(self, task_id: str) -> None:
        browser = self._sessions.pop(task_id, None)
        if browser is not None:
            await browser.close()

    async def close_all(self) -> None:
        for task_id in list(self._sessions):
            await self.close(task_id)


# ---------------------------------------------------------------------------
# Shared base for all browser tools
# ---------------------------------------------------------------------------


class _BrowserToolBase(ToolPort):
    """Shared state and helper methods for all 8 browser tools."""

    def __init__(
        self,
        session_manager: BrowserSessionManager,
        task_id: str,
        *,
        allowed_origins: list[str] | None = None,
        blocked_origins: list[str] | None = None,
        full_js: bool = False,
    ) -> None:
        self._session_manager = session_manager
        self._task_id = task_id
        self._allowed_origins: list[str] = allowed_origins if allowed_origins is not None else []
        self._blocked_origins: list[str] = blocked_origins if blocked_origins is not None else []
        self._full_js = full_js

    @property
    def parallelisable(self) -> bool:
        # Browser tools operate on a single shared page — never parallelise.
        return False

    def _get_browser(self) -> BrowserPort | None:
        return self._session_manager.get(self._task_id)

    def _ok(self, content: str) -> ToolResult:
        return ToolResult(tool_call_id="", content=content)

    def _err(self, content: str) -> ToolResult:
        return ToolResult(tool_call_id="", content=content, is_error=True)

    def _format_summary(self, summary: PageSummary) -> str:
        lines = [
            f"URL: {summary.url}",
            f"Title: {summary.title}",
            f"Status: {summary.status}",
        ]
        if summary.text_preview:
            lines.append(f"\n{summary.text_preview}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: browser_navigate
# ---------------------------------------------------------------------------


class BrowserNavigateTool(_BrowserToolBase):
    """Navigate the browser to a URL."""

    def __init__(
        self,
        session_manager: BrowserSessionManager,
        task_id: str,
        browser_factory: Any,
        *,
        allowed_origins: list[str] | None = None,
        blocked_origins: list[str] | None = None,
        full_js: bool = False,
    ) -> None:
        super().__init__(
            session_manager,
            task_id,
            allowed_origins=allowed_origins,
            blocked_origins=blocked_origins,
            full_js=full_js,
        )
        self._browser_factory = browser_factory

    @property
    def name(self) -> str:
        return "browser_navigate"

    @property
    def description(self) -> str:
        return (
            "Navigate the browser to a URL. Returns a summary of the loaded page "
            "including title, status code, and a text preview. "
            "Use browser_snapshot() after navigation to see interactive elements."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to navigate to."},
                "wait_for": {
                    "type": "string",
                    "enum": ["load", "domcontentloaded", "networkidle"],
                    "description": (
                        "Event to wait for before returning (default: domcontentloaded)."
                    ),
                },
            },
            "required": ["url"],
        }

    @property
    def required_permission(self) -> str:
        return "browser:read"

    async def execute(self, input: dict) -> ToolResult:
        url = input.get("url", "").strip()
        wait_for = input.get("wait_for", "domcontentloaded")

        if not url:
            return self._err("url is required")

        err = _validate_browser_url(url, self._allowed_origins, self._blocked_origins)
        if err:
            return self._err(err)

        browser = self._get_browser()
        if browser is None:
            browser = self._browser_factory()
            self._session_manager.set(self._task_id, browser)

        try:
            summary = await browser.navigate(url, wait_for=wait_for)
            return self._ok(self._format_summary(summary))
        except Exception as exc:  # noqa: BLE001
            logger.warning("browser_navigate failed: %s", exc)
            return self._err(f"Navigation error: {exc}")


# ---------------------------------------------------------------------------
# Tool: browser_snapshot
# ---------------------------------------------------------------------------


class BrowserSnapshotTool(_BrowserToolBase):
    """Capture the current page accessibility tree."""

    @property
    def name(self) -> str:
        return "browser_snapshot"

    @property
    def description(self) -> str:
        return (
            "Return the accessibility tree of the current page as compact text. "
            'Elements are listed as [role "label" @eN]. '
            "Use @eN handles in browser_click and browser_type to reference elements. "
            "Call browser_navigate first to load a page."
        )

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def required_permission(self) -> str:
        return "browser:read"

    async def execute(self, input: dict) -> ToolResult:
        browser = self._get_browser()
        if browser is None:
            return self._err("No active browser session. Call browser_navigate first.")
        try:
            snapshot = await browser.snapshot()
            return self._ok(snapshot)
        except Exception as exc:  # noqa: BLE001
            logger.warning("browser_snapshot failed: %s", exc)
            return self._err(f"Snapshot error: {exc}")


# ---------------------------------------------------------------------------
# Tool: browser_click
# ---------------------------------------------------------------------------


class BrowserClickTool(_BrowserToolBase):
    """Click an element on the current page."""

    @property
    def name(self) -> str:
        return "browser_click"

    @property
    def description(self) -> str:
        return (
            "Click an element on the current page. "
            "Use an @eN handle from browser_snapshot, or a CSS selector. "
            "Requires an active browser session (call browser_navigate first)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "An @eN handle from browser_snapshot, or a CSS selector.",
                },
            },
            "required": ["selector"],
        }

    @property
    def required_permission(self) -> str:
        return "browser:write"

    async def execute(self, input: dict) -> ToolResult:
        selector = input.get("selector", "").strip()
        if not selector:
            return self._err("selector is required")

        browser = self._get_browser()
        if browser is None:
            return self._err("No active browser session. Call browser_navigate first.")

        try:
            await browser.click(selector)
            return self._ok(f"Clicked '{selector}'")
        except Exception as exc:  # noqa: BLE001
            logger.warning("browser_click failed for selector=%r: %s", selector, exc)
            return self._err(f"Click error for '{selector}': {exc}")


# ---------------------------------------------------------------------------
# Tool: browser_type
# ---------------------------------------------------------------------------


class BrowserTypeTool(_BrowserToolBase):
    """Type text into an input element on the current page."""

    @property
    def name(self) -> str:
        return "browser_type"

    @property
    def description(self) -> str:
        return (
            "Type text into an input field on the current page. "
            "Use an @eN handle from browser_snapshot, or a CSS selector. "
            "Clears existing content before typing."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "An @eN handle from browser_snapshot, or a CSS selector.",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type into the element.",
                },
            },
            "required": ["selector", "text"],
        }

    @property
    def required_permission(self) -> str:
        return "browser:write"

    async def execute(self, input: dict) -> ToolResult:
        selector = input.get("selector", "").strip()
        text = input.get("text", "")

        if not selector:
            return self._err("selector is required")

        browser = self._get_browser()
        if browser is None:
            return self._err("No active browser session. Call browser_navigate first.")

        try:
            await browser.type(selector, text)
            return self._ok(f"Typed into '{selector}'")
        except Exception as exc:  # noqa: BLE001
            logger.warning("browser_type failed for selector=%r: %s", selector, exc)
            return self._err(f"Type error for '{selector}': {exc}")


# ---------------------------------------------------------------------------
# Tool: browser_scroll
# ---------------------------------------------------------------------------


class BrowserScrollTool(_BrowserToolBase):
    """Scroll the current page."""

    @property
    def name(self) -> str:
        return "browser_scroll"

    @property
    def description(self) -> str:
        return (
            "Scroll the current page. "
            "Direction must be one of: up, down, left, right. "
            "Amount is the number of scroll steps (default 3)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                    "description": "Scroll direction.",
                },
                "amount": {
                    "type": "integer",
                    "description": "Number of scroll steps (default 3).",
                    "minimum": 1,
                },
            },
            "required": ["direction"],
        }

    @property
    def required_permission(self) -> str:
        return "browser:write"

    async def execute(self, input: dict) -> ToolResult:
        direction = input.get("direction", "").strip()
        amount = int(input.get("amount", 3))

        if direction not in ("up", "down", "left", "right"):
            return self._err(f"Invalid direction '{direction}'. Must be up/down/left/right.")

        browser = self._get_browser()
        if browser is None:
            return self._err("No active browser session. Call browser_navigate first.")

        try:
            await browser.scroll(direction, amount)
            return self._ok(f"Scrolled {direction} ({amount} steps)")
        except Exception as exc:  # noqa: BLE001
            logger.warning("browser_scroll failed: %s", exc)
            return self._err(f"Scroll error: {exc}")


# ---------------------------------------------------------------------------
# Tool: browser_screenshot
# ---------------------------------------------------------------------------


class BrowserScreenshotTool(_BrowserToolBase):
    """Capture a screenshot of the current page."""

    @property
    def name(self) -> str:
        return "browser_screenshot"

    @property
    def description(self) -> str:
        return (
            "Capture a full-page screenshot of the current page. "
            "Returns a base64-encoded PNG suitable for vision-capable models."
        )

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def required_permission(self) -> str:
        return "browser:read"

    async def execute(self, input: dict) -> ToolResult:
        browser = self._get_browser()
        if browser is None:
            return self._err("No active browser session. Call browser_navigate first.")

        try:
            png_bytes = await browser.screenshot()
            b64 = base64.b64encode(png_bytes).decode()
            return self._ok(f"data:image/png;base64,{b64}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("browser_screenshot failed: %s", exc)
            return self._err(f"Screenshot error: {exc}")


# ---------------------------------------------------------------------------
# Tool: browser_evaluate
# ---------------------------------------------------------------------------


class BrowserEvaluateTool(_BrowserToolBase):
    """Execute a JavaScript expression in the current page context."""

    @property
    def name(self) -> str:
        return "browser_evaluate"

    @property
    def description(self) -> str:
        return (
            "Evaluate a JavaScript expression in the current page. "
            "By default only read-only expressions are allowed. "
            "Set full_js=true to allow unrestricted execution (requires FULL_ACCESS permission)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "js": {
                    "type": "string",
                    "description": "JavaScript expression to evaluate.",
                },
                "full_js": {
                    "type": "boolean",
                    "description": (
                        "Set true to allow unrestricted JS execution. "
                        "Requires FULL_ACCESS permission mode."
                    ),
                },
            },
            "required": ["js"],
        }

    @property
    def required_permission(self) -> str:
        return "browser:read"

    async def execute(self, input: dict) -> ToolResult:
        js = input.get("js", "").strip()
        wants_full = bool(input.get("full_js", False))

        if not js:
            return self._err("js is required")

        if wants_full and not self._full_js:
            return self._err(
                "full_js=true requires FULL_ACCESS permission mode. "
                "The agent is not running in FULL_ACCESS mode."
            )

        if not wants_full and not _is_safe_js(js):
            return self._err(
                "This JavaScript expression is not in the read-only allow-list. "
                "Use full_js=true (requires FULL_ACCESS permission) for arbitrary JS."
            )

        browser = self._get_browser()
        if browser is None:
            return self._err("No active browser session. Call browser_navigate first.")

        try:
            result = await browser.evaluate(js)
            return self._ok(json.dumps(result, default=str))
        except Exception as exc:  # noqa: BLE001
            logger.warning("browser_evaluate failed: %s", exc)
            return self._err(f"Evaluate error: {exc}")


# ---------------------------------------------------------------------------
# Tool: browser_session_close
# ---------------------------------------------------------------------------


class BrowserSessionCloseTool(_BrowserToolBase):
    """Close the active browser session."""

    @property
    def name(self) -> str:
        return "browser_session_close"

    @property
    def description(self) -> str:
        return (
            "Close the active browser session and release all resources. "
            "Sessions are also auto-closed when the agent turn ends. "
            "Call this explicitly to free resources during long tasks."
        )

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def required_permission(self) -> str:
        return "browser:read"

    async def execute(self, input: dict) -> ToolResult:
        browser = self._get_browser()
        if browser is None:
            return self._ok("No active browser session.")
        try:
            await self._session_manager.close(self._task_id)
            return self._ok("Browser session closed.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("browser_session_close failed: %s", exc)
            return self._err(f"Session close error: {exc}")


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------


def build_browser_tools(
    task_id: str,
    *,
    backend: str = "local",
    headless: bool = True,
    timeout_ms: int = 30_000,
    allowed_origins: list[str] | None = None,
    blocked_origins: list[str] | None = None,
    full_js: bool = False,
    browserbase_api_key: str = "",
    browserbase_project_id: str = "",
    browserbase_stealth: bool = False,
) -> list[ToolPort]:
    """Construct and return all 8 browser ToolPort instances.

    Args:
        task_id:            Session scope identifier (e.g. conversation/task ID).
        backend:            ``"local"`` (Playwright Chromium) or ``"browserbase"``.
        headless:           Launch browser headlessly (local backend only).
        timeout_ms:         Default navigation/action timeout in milliseconds.
        allowed_origins:    Glob patterns for allowed hostnames (empty = all).
        blocked_origins:    Glob patterns for blocked hostnames.
        full_js:            Allow unrestricted JS via browser_evaluate.
        browserbase_api_key:     Browserbase API key.
        browserbase_project_id:  Browserbase project ID.
        browserbase_stealth:     Enable Browserbase stealth mode.

    Returns:
        List of 8 ToolPort instances sharing a single BrowserSessionManager.
    """
    session_manager = BrowserSessionManager()

    def _browser_factory() -> BrowserPort:
        if backend == "browserbase":
            from ravn.adapters.browser.browserbase import BrowserbaseAdapter

            return BrowserbaseAdapter(
                api_key=browserbase_api_key,
                project_id=browserbase_project_id,
                stealth=browserbase_stealth,
                headless=headless,
                timeout_ms=timeout_ms,
            )
        from ravn.adapters.browser.local import LocalPlaywrightAdapter

        return LocalPlaywrightAdapter(headless=headless, timeout_ms=timeout_ms)

    common_kwargs: dict = {
        "session_manager": session_manager,
        "task_id": task_id,
        "allowed_origins": allowed_origins,
        "blocked_origins": blocked_origins,
        "full_js": full_js,
    }

    return [
        BrowserNavigateTool(
            session_manager=session_manager,
            task_id=task_id,
            browser_factory=_browser_factory,
            allowed_origins=allowed_origins,
            blocked_origins=blocked_origins,
            full_js=full_js,
        ),
        BrowserSnapshotTool(**common_kwargs),
        BrowserClickTool(**common_kwargs),
        BrowserTypeTool(**common_kwargs),
        BrowserScrollTool(**common_kwargs),
        BrowserScreenshotTool(**common_kwargs),
        BrowserEvaluateTool(**common_kwargs),
        BrowserSessionCloseTool(**common_kwargs),
    ]
