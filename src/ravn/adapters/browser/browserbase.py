"""BrowserbaseAdapter — cloud browser backend via Browserbase.

Selected explicitly with ``browser.backend: browserbase``. Credentials are resolved
by composition from the configured secret environment names and passed to the adapter.
The adapter fails explicitly if credentials or the ``playwright`` package are unavailable.

See: https://docs.browserbase.com/reference/api/create-a-session
"""

from __future__ import annotations

import logging
from typing import Any

from ravn.adapters.browser._base import _PlaywrightBrowserBase

logger = logging.getLogger(__name__)

_DEFAULT_CONNECT_URL = "wss://connect.browserbase.com"


class BrowserbaseAdapter(_PlaywrightBrowserBase):
    """Browser backend that executes in Browserbase's cloud infrastructure.

    Supports stealth mode and CAPTCHA solving (configured via the Browserbase
    project settings — no extra kwargs required here).

    Args:
        api_key:    Browserbase API key injected by composition.
        project_id: Browserbase project ID injected by composition.
        stealth:    Enable stealth mode (anti-bot fingerprint masking).
        headless:   Local browser headless flag (used for the CDP proxy connection).
        timeout_ms: Default navigation / action timeout in milliseconds.
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        project_id: str = "",
        stealth: bool = False,
        headless: bool = True,
        timeout_ms: int = 30_000,
    ) -> None:
        super().__init__(timeout_ms=timeout_ms)
        self._api_key = api_key
        self._project_id = project_id
        self._stealth = stealth
        self._headless = headless
        self._session_id: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _ensure_browser(self) -> None:
        """Create a Browserbase session and connect via CDP."""
        if self._page is not None:
            return

        if not self._api_key:
            raise RuntimeError(
                "Browserbase API key is not set. "
                "Configure browser.browserbase.api_key_env and inject the resolved "
                "secret at composition."
            )

        try:
            import httpx
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright is not installed. "
                "Install it with: pip install 'ravn[browser]' && playwright install chromium"
            ) from exc

        # Create a remote Browserbase session via the REST API.
        async with httpx.AsyncClient(timeout=30.0) as client:
            payload: dict[str, Any] = {"projectId": self._project_id}
            if self._stealth:
                payload["browserSettings"] = {"fingerprint": {"devices": ["desktop"]}}
            resp = await client.post(
                "https://www.browserbase.com/v1/sessions",
                json=payload,
                headers={
                    "X-BB-API-Key": self._api_key,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            session_data = resp.json()

        self._session_id = session_data["id"]
        connect_url = session_data.get("connectUrl") or (
            f"{_DEFAULT_CONNECT_URL}?apiKey={self._api_key}&sessionId={self._session_id}"
        )

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(connect_url)
        if self._browser.contexts:
            context = self._browser.contexts[0]
        else:
            context = await self._browser.new_context()
        self._page = context.pages[0] if context.pages else await context.new_page()
        self._page.set_default_timeout(self._timeout_ms)
        logger.debug(
            "Browserbase session created (id=%s, stealth=%s)", self._session_id, self._stealth
        )

    async def close(self) -> None:
        """Close the Browserbase session and clean up resources."""
        if self._page is not None:
            await self._page.close()
            self._page = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._handle_map = {}
        logger.debug("Browserbase session closed (id=%s)", self._session_id)
