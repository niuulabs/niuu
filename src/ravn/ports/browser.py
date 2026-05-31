"""BrowserPort — interface for browser automation backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class PageSummary:
    """Summary of a navigated page."""

    url: str
    title: str
    status: int
    text_preview: str  # First ~500 chars of visible text


@runtime_checkable
class BrowserPort(Protocol):
    """Abstract interface for a browser automation backend.

    Two backends are provided:
    - LocalPlaywrightAdapter: headless Chromium via Playwright (zero external deps).
    - BrowserbaseAdapter: cloud execution with stealth / CAPTCHA support.

    Sessions are scoped to a task_id; the agent never sees which backend is active.
    """

    async def navigate(self, url: str, *, wait_for: str = "domcontentloaded") -> PageSummary:
        """Navigate to *url* and return a page summary."""
        raise NotImplementedError  # pragma: no cover

    async def snapshot(self) -> str:
        """Return the page accessibility tree as compact text.

        Each element is formatted as::

            [role "label" @eN]

        Handles are referenced by ``@eN`` in subsequent tool calls.
        Typical output < 2 K tokens.
        """
        raise NotImplementedError  # pragma: no cover

    async def click(self, selector: str) -> None:
        """Click the element identified by *selector* or ``@eN`` handle."""
        raise NotImplementedError  # pragma: no cover

    async def type(self, selector: str, text: str) -> None:
        """Type *text* into the element identified by *selector* or ``@eN`` handle."""
        raise NotImplementedError  # pragma: no cover

    async def scroll(self, direction: str, amount: int = 3) -> None:
        """Scroll the page.

        Args:
            direction: ``"up"`` | ``"down"`` | ``"left"`` | ``"right"``
            amount:    Number of scroll steps (default 3).
        """
        raise NotImplementedError  # pragma: no cover

    async def screenshot(self) -> bytes:
        """Capture a full-page screenshot and return raw PNG bytes."""
        raise NotImplementedError  # pragma: no cover

    async def evaluate(self, js: str) -> Any:
        """Evaluate a JavaScript expression and return the result."""
        raise NotImplementedError  # pragma: no cover

    async def close(self) -> None:
        """Close the browser session and release all resources."""
        raise NotImplementedError  # pragma: no cover
