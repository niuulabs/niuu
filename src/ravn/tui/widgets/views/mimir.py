"""MimirView — navigable wiki browser with search, page content, and instance switching."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Input, RichLog, Static


class MimirView(Widget):
    """Browseable Mímir wiki.

    j/k navigate page list, Enter opens a page, Backspace goes back.
    / opens search, Tab switches between configured instances.
    """

    DEFAULT_CSS = """
    MimirView {
        height: 1fr;
        width: 1fr;
        background: #09090b;
        layout: vertical;
    }
    MimirView #mv-instance-bar {
        height: 1;
        padding: 0 1;
        background: #111113;
    }
    MimirView #mv-content {
        height: 1fr;
        background: #09090b;
        scrollbar-size: 1 1;
        scrollbar-color: #27272a;
        scrollbar-color-hover: #3f3f46;
    }
    MimirView #mv-info {
        height: 1;
        padding: 0 1;
        background: #111113;
        display: none;
    }
    MimirView #mv-info.visible {
        display: block;
    }
    MimirView #mv-search {
        height: 3;
        border-top: solid #3f3f46;
        background: #18181b;
        display: none;
    }
    MimirView #mv-search.visible {
        display: block;
    }
    """

    can_focus = True

    BINDINGS = [
        Binding("j", "select_next", show=False),
        Binding("k", "select_prev", show=False),
        Binding("enter", "open_selected", "Open"),
        Binding("backspace", "go_back", "Back"),
        Binding("tab", "next_instance", "Switch"),
        Binding("/", "search", "Search"),
        Binding("escape", "cancel_search", show=False),
    ]

    def __init__(
        self,
        connection: Any | None = None,
        mimir_urls: list[tuple[str, str]] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._connection = connection
        self._mimir_urls: list[tuple[str, str]] = mimir_urls or []
        self._instance_idx: int = 0
        self._pages: list[dict[str, Any]] = []
        self._filtered_pages: list[dict[str, Any]] = []
        self._selected_idx: int = 0
        self._mode: str = "list"  # "list" | "page" | "search"
        self._history: list[tuple[str, int]] = []

    def compose(self) -> ComposeResult:
        yield Static("[#52525b]  loading…[/]", id="mv-instance-bar")
        yield RichLog(id="mv-content", markup=True, wrap=True, highlight=False)
        yield Static("", id="mv-info")
        yield Input(placeholder="Search pages…  (Enter to search, Esc to cancel)", id="mv-search")

    def on_mount(self) -> None:
        asyncio.create_task(self._load_pages(), name="mimir-pages")

    # ------------------------------------------------------------------
    # Instance helpers
    # ------------------------------------------------------------------

    @property
    def _current_url(self) -> str | None:
        if self._mimir_urls:
            return self._mimir_urls[self._instance_idx][1]
        if self._connection:
            return self._connection.base_url
        return None

    @property
    def _current_name(self) -> str:
        if self._mimir_urls:
            return self._mimir_urls[self._instance_idx][0]
        if self._connection:
            return self._connection.name
        return "mimir"

    def _update_instance_bar(self, suffix: str = "") -> None:
        name = self._current_name
        n = len(self._mimir_urls)
        switch_hint = f"  [#3f3f46][tab] {self._instance_idx + 1}/{n}[/]" if n > 1 else ""
        if suffix:
            text = f"[#52525b]  {name}  ·  {suffix}[/]{switch_hint}"
        else:
            text = f"[#52525b]  {name}  ·  {len(self._pages)} pages[/]{switch_hint}"
        with suppress(Exception):
            self.query_one("#mv-instance-bar", Static).update(text)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    async def _load_pages(self) -> None:
        url = self._current_url
        if not url:
            self._show_no_instances()
            return
        self._update_instance_bar("loading…")
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{url}/mimir/pages")
            if resp.status_code == 200:
                self._pages = resp.json()
                self._selected_idx = 0
                self._mode = "list"
                self._history.clear()
                self._rebuild_list(self._pages)
                self._update_instance_bar()
            else:
                self._write(f"[#ef4444]  HTTP {resp.status_code}[/]")
                self._update_instance_bar(f"error {resp.status_code}")
        except Exception as exc:
            self._write(f"[#ef4444]  {exc}[/]")
            self._update_instance_bar("unreachable")

    def _show_no_instances(self) -> None:
        log = self.query_one("#mv-content", RichLog)
        log.clear()
        log.write("[#52525b]  No Mímir instances configured.[/]")
        log.write("")
        log.write("[#3f3f46]  Add instances to your ravn config:[/]")
        log.write("[#71717a]  mimir:[/]")
        log.write("[#71717a]    instances:[/]")
        log.write("[#71717a]      - name: shared[/]")
        log.write("[#71717a]        url: https://your-volundr-host[/]")
        self._update_instance_bar("not configured")

    # ------------------------------------------------------------------
    # List rendering
    # ------------------------------------------------------------------

    def _rebuild_list(self, pages: list[dict[str, Any]]) -> None:
        try:
            log = self.query_one("#mv-content", RichLog)
        except Exception:
            return
        log.clear()
        self._hide_info()
        if not pages:
            log.write("[#3f3f46]  (no pages)[/]")
            return
        for i, page in enumerate(pages):
            selected = i == self._selected_idx
            accent = "[bold #f59e0b]▌[/]" if selected else " "
            title = str(page.get("title", page.get("path", "?")))
            category = str(page.get("category", ""))
            summary = (str(page.get("summary", "") or ""))[:70]
            log.write(
                f"{accent}[bold #06b6d4]{title}[/]"
                + (f"  [#52525b]{category}[/]" if category else "")
            )
            if summary:
                log.write(f"  [#3f3f46]{summary}[/]")

    # ------------------------------------------------------------------
    # Page loading
    # ------------------------------------------------------------------

    async def _load_page(self, path: str) -> None:
        url = self._current_url
        if not url:
            return
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{url}/mimir/page", params={"path": path})
            if resp.status_code == 200:
                self._render_page(resp.json())
                self._mode = "page"
            else:
                self._write(f"[#ef4444]  {resp.status_code}: {path}[/]")
        except Exception as exc:
            self._write(f"[#ef4444]  {exc}[/]")

    def _render_page(self, page: dict[str, Any]) -> None:
        try:
            log = self.query_one("#mv-content", RichLog)
        except Exception:
            return
        log.clear()
        title = str(page.get("title", "?"))
        category = str(page.get("category", ""))
        updated = str(page.get("updated_at", ""))[:10]
        source_ids: list[str] = page.get("source_ids", [])
        content = str(page.get("content", ""))

        log.write(f"[bold #fafafa]{title}[/]" + (f"  [#52525b]{category}[/]" if category else ""))
        log.write(f"[#3f3f46]  updated {updated}  ·  {len(source_ids)} source(s)[/]")
        log.write("")

        for line in content.split("\n"):
            if line.startswith("### "):
                log.write(f"[bold #a1a1aa]{line[4:]}[/]")
            elif line.startswith("## "):
                log.write(f"[bold #d4d4d8]{line[3:]}[/]")
            elif line.startswith("# "):
                log.write(f"[bold #fafafa]{line[2:]}[/]")
            elif line.startswith(("- ", "* ")):
                log.write(f"  [#52525b]·[/] [#d4d4d8]{line[2:]}[/]")
            elif line.startswith("> "):
                log.write(f"  [italic #71717a]{line[2:]}[/]")
            elif line.startswith("```"):
                log.write(f"[#3f3f46]{line}[/]")
            elif line:
                log.write(f"[#c4c4c4]{line}[/]")
            else:
                log.write("")

        if source_ids:
            self._show_info(f"sources: {', '.join(source_ids[:4])}")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def _do_search(self, query: str) -> None:
        url = self._current_url
        if not url or not query:
            return
        self._update_instance_bar(f"searching '{query}'…")
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{url}/mimir/search", params={"q": query})
            if resp.status_code == 200:
                results = resp.json()
                self._filtered_pages = results
                self._selected_idx = 0
                self._mode = "search"
                self._rebuild_list(results)
                self._update_instance_bar(f"{len(results)} results for '{query}'")
            else:
                self._write(f"[#ef4444]  Search error: HTTP {resp.status_code}[/]")
                self._update_instance_bar()
        except Exception as exc:
            self._write(f"[#ef4444]  Search error: {exc}[/]")
            self._update_instance_bar()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_select_next(self) -> None:
        pages = self._filtered_pages if self._mode == "search" else self._pages
        if not pages or self._mode == "page":
            return
        self._selected_idx = (self._selected_idx + 1) % len(pages)
        self._rebuild_list(pages)

    def action_select_prev(self) -> None:
        pages = self._filtered_pages if self._mode == "search" else self._pages
        if not pages or self._mode == "page":
            return
        self._selected_idx = (self._selected_idx - 1) % len(pages)
        self._rebuild_list(pages)

    def action_open_selected(self) -> None:
        pages = self._filtered_pages if self._mode == "search" else self._pages
        if not pages or self._mode == "page":
            return
        page = pages[self._selected_idx]
        path = str(page.get("path", ""))
        if path:
            self._history.append((self._mode, self._selected_idx))
            asyncio.create_task(self._load_page(path), name="mimir-load-page")

    def action_go_back(self) -> None:
        if self._history:
            prev_mode, prev_idx = self._history.pop()
            self._mode = prev_mode
            self._selected_idx = prev_idx
            pages = self._filtered_pages if prev_mode == "search" else self._pages
            self._rebuild_list(pages)
            if prev_mode != "search":
                self._update_instance_bar()
            return
        if self._mode != "list":
            self._mode = "list"
            self._filtered_pages = []
            self._selected_idx = 0
            self._rebuild_list(self._pages)
            self._update_instance_bar()

    def action_next_instance(self) -> None:
        if len(self._mimir_urls) <= 1:
            return
        self._instance_idx = (self._instance_idx + 1) % len(self._mimir_urls)
        self._pages = []
        self._filtered_pages = []
        self._selected_idx = 0
        self._mode = "list"
        self._history.clear()
        asyncio.create_task(self._load_pages(), name="mimir-switch")

    def action_search(self) -> None:
        with suppress(Exception):
            search = self.query_one("#mv-search", Input)
            search.add_class("visible")
            search.focus()

    def action_cancel_search(self) -> None:
        with suppress(Exception):
            search = self.query_one("#mv-search", Input)
            search.remove_class("visible")
            search.clear()
        if self._mode == "search":
            self._mode = "list"
            self._filtered_pages = []
            self._selected_idx = 0
            self._rebuild_list(self._pages)
            self._update_instance_bar()
        self.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "mv-search":
            return
        query = event.value.strip()
        with suppress(Exception):
            self.query_one("#mv-search", Input).remove_class("visible")
        if query:
            asyncio.create_task(self._do_search(query), name="mimir-search")
        else:
            self.focus()

    # ------------------------------------------------------------------
    # Info bar
    # ------------------------------------------------------------------

    def _show_info(self, text: str) -> None:
        with suppress(Exception):
            info = self.query_one("#mv-info", Static)
            info.update(f"[#3f3f46]  {text}[/]")
            info.add_class("visible")

    def _hide_info(self) -> None:
        with suppress(Exception):
            self.query_one("#mv-info", Static).remove_class("visible")

    def _write(self, text: str) -> None:
        with suppress(Exception):
            self.query_one("#mv-content", RichLog).write(text)
