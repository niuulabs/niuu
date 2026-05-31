"""Textual TUI app — queries plugin registry for pages dynamically.

Uses the zinc theme, 4-mode keybinding system, and widget library.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Static

from cli.tui.mode import InputMode
from cli.tui.widgets.command_palette import CommandPalette, PaletteItem, PaletteItemType
from cli.tui.widgets.footer import NiuuFooter
from cli.tui.widgets.header import NiuuHeader
from cli.tui.widgets.help_overlay import HelpOverlay
from cli.tui.widgets.sidebar import NiuuSidebar, SidebarPage

if TYPE_CHECKING:
    from cli.registry import PluginRegistry, TUIPageSpec


class NiuuTUI(App[str]):
    """Main niuu TUI application.

    Dynamically shows pages registered by plugins via the PluginRegistry.
    Four input modes control keybinding routing.
    """

    TITLE = "Niuu"
    SUB_TITLE = "Platform Control"
    CSS_PATH = "theme.tcss"

    CSS = """
    #content {
        width: 1fr;
        height: 1fr;
        padding: 1;
    }
    #welcome {
        margin: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit_app", "Quit", priority=True),
        Binding("question_mark", "toggle_help", "Help", priority=True),
        Binding("left_square_bracket", "toggle_sidebar", "Sidebar", priority=True),
        Binding("slash", "enter_search", "Search", priority=True),
        Binding("ctrl+k", "open_palette", "Palette", priority=True),
        Binding("1", "go_page('0')", "Page 1", show=False, priority=True),
        Binding("2", "go_page('1')", "Page 2", show=False, priority=True),
        Binding("3", "go_page('2')", "Page 3", show=False, priority=True),
        Binding("4", "go_page('3')", "Page 4", show=False, priority=True),
        Binding("5", "go_page('4')", "Page 5", show=False, priority=True),
        Binding("6", "go_page('5')", "Page 6", show=False, priority=True),
        Binding("7", "go_page('6')", "Page 7", show=False, priority=True),
    ]

    mode: reactive[InputMode] = reactive(InputMode.NORMAL)

    def __init__(
        self,
        pages: list[TUIPageSpec] | None = None,
        theme_name: str = "textual-dark",
        server_url: str = "",
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._pages: list[TUIPageSpec] = pages or []
        self._theme_name = theme_name
        self._server_url = server_url

    # ── Compose ──────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        sidebar_pages = [
            SidebarPage(name=p.name, icon=p.icon, key=str(i + 1)) for i, p in enumerate(self._pages)
        ]
        yield NiuuHeader(
            server_url=self._server_url,
            id="niuu-header",
        )
        yield NiuuSidebar(sidebar_pages, id="niuu-sidebar")
        with Vertical(id="content"):
            yield Static("Welcome to Niuu", id="welcome")
        yield NiuuFooter(id="niuu-footer")

    def on_mount(self) -> None:
        self.theme = self._theme_name

    # ── Mode management ──────────────────────────────────────

    def watch_mode(self, new_mode: InputMode) -> None:
        with suppress(Exception):
            self.query_one("#niuu-header", NiuuHeader).mode = new_mode
        with suppress(Exception):
            self.query_one("#niuu-footer", NiuuFooter).mode = new_mode

    def set_mode(self, new_mode: InputMode) -> None:
        self.mode = new_mode

    # ── Key routing (mode-aware) ─────────────────────────────

    def check_action(self, action: str, _parameters: tuple[object, ...]) -> bool | None:
        """Block normal-mode keys when in INSERT/SEARCH/COMMAND mode."""
        if self.mode == InputMode.NORMAL:
            return True

        # Always allow these regardless of mode.
        always_allowed = {
            "open_palette",
            "quit_app",
        }
        if action in always_allowed:
            return True

        # In INSERT mode, suppress most global keys.
        if self.mode == InputMode.INSERT:
            insert_allowed = {"go_page", "toggle_help"}
            return action in insert_allowed or None

        # SEARCH/COMMAND: allow escape-like actions.
        if self.mode in (InputMode.SEARCH, InputMode.COMMAND):
            search_allowed = {"enter_search", "toggle_help"}
            return action in search_allowed or None

        return True

    # ── Actions ──────────────────────────────────────────────

    def action_quit_app(self) -> None:
        self.exit()

    def action_toggle_help(self) -> None:
        self.push_screen(HelpOverlay())
        self.set_mode(InputMode.NORMAL)

    def action_toggle_sidebar(self) -> None:
        with suppress(Exception):
            self.query_one("#niuu-sidebar", NiuuSidebar).toggle()

    def action_enter_search(self) -> None:
        if self.mode == InputMode.SEARCH:
            self.set_mode(InputMode.NORMAL)
            return
        self.set_mode(InputMode.SEARCH)

    def action_open_palette(self) -> None:
        items = self._build_palette_items()
        self.set_mode(InputMode.COMMAND)
        self.push_screen(
            CommandPalette(items),
            callback=self._on_palette_dismissed,
        )

    def _on_palette_dismissed(self, result: PaletteItem | None) -> None:
        self.set_mode(InputMode.NORMAL)
        if result is None:
            return
        if result.item_type == PaletteItemType.PAGE:
            with suppress(ValueError, IndexError):
                idx = int(result.action_id)
                self._navigate_to_page(idx)

    def action_go_page(self, index_str: str) -> None:
        try:
            idx = int(index_str)
        except ValueError:
            return
        self._navigate_to_page(idx)

    def _navigate_to_page(self, index: int) -> None:
        try:
            sidebar = self.query_one("#niuu-sidebar", NiuuSidebar)
        except Exception:
            return
        sidebar.select_page(index)

    def _build_palette_items(self) -> list[PaletteItem]:
        items: list[PaletteItem] = []
        for i, page in enumerate(self._pages):
            items.append(
                PaletteItem(
                    label=page.name,
                    description=f"Go to {page.name}",
                    item_type=PaletteItemType.PAGE,
                    icon=page.icon,
                    action_id=str(i),
                )
            )
        items.append(
            PaletteItem(
                label="Toggle sidebar",
                description="Show/hide sidebar",
                item_type=PaletteItemType.ACTION,
                icon="[",
                action_id="toggle_sidebar",
            )
        )
        items.append(
            PaletteItem(
                label="Help",
                description="Show keybinding help",
                item_type=PaletteItemType.ACTION,
                icon="?",
                action_id="toggle_help",
            )
        )
        return items

    # ── Sidebar message handling ─────────────────────────────

    def on_niuu_sidebar_page_selected(self, message: NiuuSidebar.PageSelected) -> None:
        """Switch content area to the selected page widget."""
        try:
            content = self.query_one("#content", Vertical)
        except Exception:
            return
        content.remove_children()
        idx = message.page_index if hasattr(message, "page_index") else -1
        # Find the matching page spec by name.
        for i, spec in enumerate(self._pages):
            if spec.name == message.page.name:
                idx = i
                break
        if 0 <= idx < len(self._pages):
            page_widget = self._pages[idx].widget_class(id=f"page-{idx}")
            content.mount(page_widget)
        else:
            content.mount(Static(f"[bold]{message.page.icon} {message.page.name}[/]", id="welcome"))


def build_tui(
    registry: PluginRegistry | None = None,
    theme: str = "textual-dark",
    server_url: str = "",
) -> NiuuTUI:
    """Build the TUI app from a plugin registry."""
    pages: list[TUIPageSpec] = []
    if registry:
        for plugin in registry.plugins.values():
            pages.extend(plugin.tui_pages())
    return NiuuTUI(pages=pages, theme_name=theme, server_url=server_url)
