"""WebSearchTool — search the web via a configurable provider adapter."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort
from ravn.ports.web_search import SearchResult, WebSearchPort

_DEFAULT_NUM_RESULTS = 5

class _DuckDuckGoHTMLParser(HTMLParser):
    """Extract result links and snippets from DuckDuckGo's simple HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._current_href = ""
        self._current_title: list[str] = []
        self._current_snippet: list[str] = []
        self._capture_title = False
        self._capture_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        classes = set(attr.get("class", "").split())
        if tag == "a" and {"result__a", "result-link"} & classes:
            self._current_href = attr.get("href", "")
            self._current_title = []
            self._current_snippet = []
            self._capture_title = True
            return
        if tag in {"a", "div"} and {"result__snippet", "result-snippet"} & classes:
            self._capture_snippet = True

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._capture_title:
            self._current_title.append(text)
        elif self._capture_snippet:
            self._current_snippet.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title:
            title = " ".join(self._current_title).strip()
            url = _unwrap_duckduckgo_url(self._current_href)
            if title and url:
                self.results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=" ".join(self._current_snippet).strip(),
                    )
                )
            self._capture_title = False
            return
        if tag in {"a", "div"}:
            self._capture_snippet = False


class DuckDuckGoLiteSearchProvider(WebSearchPort):
    """Real web search provider using DuckDuckGo's lightweight HTML endpoint."""

    def __init__(
        self,
        *,
        base_url: str = "https://html.duckduckgo.com/html/",
        timeout: float = 15.0,
        user_agent: str = "Ravn/1.0 (+https://github.com/niuulabs/volundr)",
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._user_agent = user_agent

    async def search(self, query: str, *, num_results: int) -> list[SearchResult]:
        headers = {"User-Agent": self._user_agent}
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            response = await client.get(self._base_url, params={"q": query})
            response.raise_for_status()

        parser = _DuckDuckGoHTMLParser()
        parser.feed(response.text)
        return parser.results[:num_results]


def _unwrap_duckduckgo_url(url: str) -> str:
    if url.startswith("//"):
        url = f"https:{url}"
    parsed = urlparse(url)
    if "duckduckgo.com" not in parsed.netloc:
        return url
    target = parse_qs(parsed.query).get("uddg", [""])[0]
    return unquote(target) if target else url


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------


class WebSearchTool(ToolPort):
    """Search the web and return a list of results.

    The search provider is injected at construction time following the
    dynamic adapter pattern — configure via the ``adapter`` key in YAML.
    """

    def __init__(
        self,
        provider: WebSearchPort | None = None,
        *,
        num_results: int = _DEFAULT_NUM_RESULTS,
    ) -> None:
        self._provider: WebSearchPort = provider or DuckDuckGoLiteSearchProvider()
        self._num_results = num_results

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web for information. "
            "Returns a list of results with titles, URLs, and snippets. "
            "Use this to find current information, documentation, or references."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "num_results": {
                    "type": "integer",
                    "description": (
                        f"Number of results to return (default: {_DEFAULT_NUM_RESULTS})."
                    ),
                },
            },
            "required": ["query"],
        }

    @property
    def required_permission(self) -> str:
        return "web:search"

    async def execute(self, input: dict) -> ToolResult:
        query = input.get("query", "").strip()
        num_results = int(input.get("num_results", self._num_results))

        if not query:
            return ToolResult(tool_call_id="", content="query is required", is_error=True)

        results = await self._provider.search(query, num_results=num_results)

        if not results:
            return ToolResult(tool_call_id="", content="No results found.")

        lines = [f"{i + 1}. {r.title}\n   {r.url}\n   {r.snippet}" for i, r in enumerate(results)]
        return ToolResult(tool_call_id="", content="\n\n".join(lines))
