"""Read-only HTTP probe tool for capability discovery dry-runs."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from ravn.adapters.tools._url_security import check_ssrf
from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort

_DEFAULT_TIMEOUT = 20.0
_DEFAULT_USER_AGENT = "Ravn/1.0 (+https://github.com/niuulabs/volundr)"


class HttpProbeTool(ToolPort):
    """Fetch URL metadata without interpreting page content."""

    def __init__(
        self,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> None:
        self._timeout = timeout
        self._user_agent = user_agent

    @property
    def name(self) -> str:
        return "http_probe"

    @property
    def description(self) -> str:
        return "Read HTTP status, final URL, content type, and byte length for a URL."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "HTTP or HTTPS URL to probe."},
            },
            "required": ["url"],
        }

    @property
    def required_permission(self) -> str:
        return "web:fetch"

    async def execute(self, input: dict) -> ToolResult:
        url = str(input.get("url") or "").strip()
        error = _validate_url(url)
        if error:
            return ToolResult(tool_call_id="", content=error, is_error=True)
        headers = {"User-Agent": self._user_agent}
        async with httpx.AsyncClient(
            timeout=self._timeout,
            headers=headers,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
        content_type = response.headers.get("content-type", "")
        content_length = len(response.content)
        return ToolResult(
            tool_call_id="",
            content=(
                f"status={response.status_code}\n"
                f"final_url={response.url}\n"
                f"content_type={content_type}\n"
                f"content_length={content_length}"
            ),
            is_error=response.status_code >= 400,
        )


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return f"Blocked: only http and https URLs are allowed (got '{parsed.scheme}')"
    if not parsed.hostname:
        return "Blocked: URL has no hostname"
    return check_ssrf(parsed.hostname) or ""
