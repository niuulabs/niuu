"""Tests for cli.tui.app — Textual TUI app with mode system and widgets."""

from __future__ import annotations

import pytest
from textual.widgets import Static

from cli.registry import PluginRegistry, TUIPageSpec
from cli.tui.app import NiuuTUI, build_tui
from cli.tui.mode import MODE_COLORS, MODE_COLORS_HEX, InputMode
from cli.tui.widgets.command_palette import (
    CommandPalette,
    PaletteItem,
    PaletteItemType,
    _fuzzy_match,
)
from cli.tui.widgets.footer import (
    DEFAULT_HINTS,
    INSERT_HINTS,
    KeyHint,
    NiuuFooter,
)
from cli.tui.widgets.header import ConnectionState, NiuuHeader
from cli.tui.widgets.help_overlay import DEFAULT_BINDINGS, HelpOverlay, KeyBinding
from cli.tui.widgets.mention_menu import MentionItem, MentionMenu, _fuzzy_contains
from cli.tui.widgets.metric_card import DEFAULT_METRIC_COLOR, MetricCard, MetricRow
from cli.tui.widgets.modal import NiuuModal
from cli.tui.widgets.sidebar import (
    SIDEBAR_COLLAPSED_WIDTH,
    SIDEBAR_EXPANDED_WIDTH,
    NiuuSidebar,
    SidebarPage,
)
from cli.tui.widgets.status_badge import _STATUS_MAP, StatusBadge
from cli.tui.widgets.tabs import NiuuTabs
from tests.test_cli.conftest import FakePlugin

# ── Fixtures ─────────────────────────────────────────────────


SAMPLE_PAGES = [
    TUIPageSpec(name="Sessions", icon="S", widget_class=Static),
    TUIPageSpec(name="Sagas", icon="T", widget_class=Static),
]


@pytest.fixture
def tui_app() -> NiuuTUI:
    return NiuuTUI(pages=SAMPLE_PAGES)


@pytest.fixture
def empty_app() -> NiuuTUI:
    return NiuuTUI()


# ── InputMode tests ──────────────────────────────────────────


class TestInputMode:
    def test_mode_values(self) -> None:
        assert InputMode.NORMAL.value == "NORMAL"
        assert InputMode.INSERT.value == "INSERT"
        assert InputMode.SEARCH.value == "SEARCH"
        assert InputMode.COMMAND.value == "COMMAND"

    def test_mode_str(self) -> None:
        assert str(InputMode.NORMAL) == "NORMAL"
        assert str(InputMode.INSERT) == "INSERT"

    def test_mode_colors_mapping(self) -> None:
        for mode in InputMode:
            assert mode in MODE_COLORS
            assert mode in MODE_COLORS_HEX

    def test_mode_colors_hex_format(self) -> None:
        for color in MODE_COLORS_HEX.values():
            assert color.startswith("#")
            assert len(color) == 7

    def test_mode_is_enum(self) -> None:
        assert len(InputMode) == 4

    def test_mode_comparison(self) -> None:
        assert InputMode.NORMAL == "NORMAL"
        assert InputMode.INSERT != InputMode.NORMAL


# ── NiuuTUI app tests ───────────────────────────────────────


class TestNiuuTUI:
    async def test_app_starts(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            assert tui_app.title == "Niuu"
            assert tui_app.sub_title == "Platform Control"
            await pilot.pause()

    async def test_initial_mode_is_normal(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            assert tui_app.mode == InputMode.NORMAL

    async def test_header_present(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            header = tui_app.query_one("#niuu-header", NiuuHeader)
            assert header is not None

    async def test_sidebar_present(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            sidebar = tui_app.query_one("#niuu-sidebar", NiuuSidebar)
            assert sidebar is not None

    async def test_footer_present(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            footer = tui_app.query_one("#niuu-footer", NiuuFooter)
            assert footer is not None

    async def test_welcome_message(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            welcome = tui_app.query_one("#welcome", Static)
            assert welcome is not None

    async def test_quit_binding(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("q")

    async def test_theme_applied(self) -> None:
        app = NiuuTUI(theme_name="textual-light")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.theme == "textual-light"

    async def test_toggle_sidebar(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            sidebar = tui_app.query_one("#niuu-sidebar", NiuuSidebar)
            assert not sidebar.collapsed
            await pilot.press("left_square_bracket")
            await pilot.pause()
            assert sidebar.collapsed

    async def test_set_mode(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            tui_app.set_mode(InputMode.INSERT)
            assert tui_app.mode == InputMode.INSERT

    async def test_enter_search_mode(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("slash")
            await pilot.pause()
            assert tui_app.mode == InputMode.SEARCH

    async def test_exit_search_mode(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            tui_app.set_mode(InputMode.SEARCH)
            await pilot.press("slash")
            await pilot.pause()
            assert tui_app.mode == InputMode.NORMAL

    async def test_page_navigation(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            sidebar = tui_app.query_one("#niuu-sidebar", NiuuSidebar)
            assert sidebar.active_index == 0

    async def test_page_navigation_key_2(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            sidebar = tui_app.query_one("#niuu-sidebar", NiuuSidebar)
            assert sidebar.active_index == 1

    async def test_mode_propagates_to_header(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            tui_app.set_mode(InputMode.INSERT)
            await pilot.pause()
            header = tui_app.query_one("#niuu-header", NiuuHeader)
            assert header.mode == InputMode.INSERT

    async def test_mode_propagates_to_footer(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            tui_app.set_mode(InputMode.INSERT)
            await pilot.pause()
            footer = tui_app.query_one("#niuu-footer", NiuuFooter)
            assert footer.mode == InputMode.INSERT

    async def test_insert_mode_blocks_normal_keys(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            tui_app.set_mode(InputMode.INSERT)
            result = tui_app.check_action("toggle_sidebar", ())
            assert result is None

    async def test_insert_mode_allows_go_page(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            tui_app.set_mode(InputMode.INSERT)
            result = tui_app.check_action("go_page", ("0",))
            assert result is True

    async def test_normal_mode_allows_all(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            result = tui_app.check_action("toggle_sidebar", ())
            assert result is True

    async def test_command_mode_blocks_sidebar(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            tui_app.set_mode(InputMode.COMMAND)
            result = tui_app.check_action("toggle_sidebar", ())
            assert result is None

    async def test_search_mode_allows_enter_search(self, tui_app: NiuuTUI) -> None:
        async with tui_app.run_test() as pilot:
            await pilot.pause()
            tui_app.set_mode(InputMode.SEARCH)
            result = tui_app.check_action("enter_search", ())
            assert result is True


# ── build_tui tests ──────────────────────────────────────────


class TestBuildTUI:
    def test_without_registry(self) -> None:
        app = build_tui()
        assert isinstance(app, NiuuTUI)

    def test_with_registry(self) -> None:
        registry = PluginRegistry()
        registry.register(FakePlugin(name="test"))
        app = build_tui(registry=registry)
        assert isinstance(app, NiuuTUI)

    def test_with_server_url(self) -> None:
        app = build_tui(server_url="https://niuu.dev")
        assert app._server_url == "https://niuu.dev"

    def test_with_theme(self) -> None:
        app = build_tui(theme="textual-light")
        assert app._theme_name == "textual-light"


# ── Header tests ─────────────────────────────────────────────


class TestNiuuHeader:
    async def test_header_renders(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            await pilot.pause()
            header = app.query_one("#niuu-header", NiuuHeader)
            assert header.mode == InputMode.NORMAL
            assert header.connection == ConnectionState.CONNECTING

    async def test_header_server_url(self) -> None:
        app = NiuuTUI(server_url="https://niuu.dev")
        async with app.run_test() as pilot:
            await pilot.pause()
            header = app.query_one("#niuu-header", NiuuHeader)
            assert header.server_url == "https://niuu.dev"

    async def test_header_mode_change(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            await pilot.pause()
            header = app.query_one("#niuu-header", NiuuHeader)
            header.mode = InputMode.SEARCH
            await pilot.pause()
            assert header.mode == InputMode.SEARCH

    async def test_header_connection_states(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            await pilot.pause()
            header = app.query_one("#niuu-header", NiuuHeader)
            for state in ConnectionState:
                header.connection = state
                await pilot.pause()
                assert header.connection == state

    def test_connection_state_values(self) -> None:
        assert ConnectionState.CONNECTING == "connecting"
        assert ConnectionState.CONNECTED == "connected"
        assert ConnectionState.DISCONNECTED == "disconnected"

    def test_render_bar_content(self) -> None:
        header = NiuuHeader(server_url="https://niuu.dev")
        bar = header._render_bar()
        assert "Niuu" in bar
        assert "niuu.dev" in bar
        assert "NORMAL" in bar


# ── Sidebar tests ────────────────────────────────────────────


class TestNiuuSidebar:
    async def test_sidebar_renders_pages(self) -> None:
        app = NiuuTUI(pages=SAMPLE_PAGES)
        async with app.run_test() as pilot:
            await pilot.pause()
            sidebar = app.query_one("#niuu-sidebar", NiuuSidebar)
            assert len(sidebar.pages) == 2

    async def test_sidebar_toggle(self) -> None:
        app = NiuuTUI(pages=SAMPLE_PAGES)
        async with app.run_test() as pilot:
            await pilot.pause()
            sidebar = app.query_one("#niuu-sidebar", NiuuSidebar)
            assert not sidebar.collapsed
            sidebar.toggle()
            await pilot.pause()
            assert sidebar.collapsed
            sidebar.toggle()
            await pilot.pause()
            assert not sidebar.collapsed

    async def test_sidebar_select_page(self) -> None:
        app = NiuuTUI(pages=SAMPLE_PAGES)
        async with app.run_test() as pilot:
            await pilot.pause()
            sidebar = app.query_one("#niuu-sidebar", NiuuSidebar)
            sidebar.select_page(1)
            await pilot.pause()
            assert sidebar.active_index == 1

    async def test_sidebar_select_invalid_page(self) -> None:
        app = NiuuTUI(pages=SAMPLE_PAGES)
        async with app.run_test() as pilot:
            await pilot.pause()
            sidebar = app.query_one("#niuu-sidebar", NiuuSidebar)
            sidebar.select_page(99)
            await pilot.pause()
            assert sidebar.active_index == 0

    async def test_sidebar_set_pages(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            await pilot.pause()
            sidebar = app.query_one("#niuu-sidebar", NiuuSidebar)
            new_pages = [SidebarPage(name="New", icon="N", key="1")]
            sidebar.set_pages(new_pages)
            await pilot.pause()
            assert len(sidebar.pages) == 1

    def test_sidebar_constants(self) -> None:
        assert SIDEBAR_EXPANDED_WIDTH == 24
        assert SIDEBAR_COLLAPSED_WIDTH == 5

    def test_sidebar_page_dataclass(self) -> None:
        page = SidebarPage(name="Test", icon="T", key="1")
        assert page.name == "Test"
        assert page.icon == "T"
        assert page.key == "1"

    def test_sidebar_page_frozen(self) -> None:
        page = SidebarPage(name="Test", icon="T", key="1")
        with pytest.raises(AttributeError):
            page.name = "Other"  # type: ignore[misc]


# ── Footer tests ─────────────────────────────────────────────


class TestNiuuFooter:
    async def test_footer_renders(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            await pilot.pause()
            footer = app.query_one("#niuu-footer", NiuuFooter)
            assert footer.mode == InputMode.NORMAL

    async def test_footer_mode_changes_hints(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            await pilot.pause()
            footer = app.query_one("#niuu-footer", NiuuFooter)
            footer.mode = InputMode.INSERT
            await pilot.pause()
            assert footer._active_hints() == INSERT_HINTS

    async def test_footer_custom_hints(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            await pilot.pause()
            footer = app.query_one("#niuu-footer", NiuuFooter)
            custom = [KeyHint("x", "custom")]
            footer.set_hints(custom)
            await pilot.pause()
            assert footer._active_hints() == custom

    async def test_footer_insert_mode_overrides_page_hints(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            await pilot.pause()
            footer = app.query_one("#niuu-footer", NiuuFooter)
            footer.set_hints([KeyHint("x", "custom")])
            footer.mode = InputMode.INSERT
            assert footer._active_hints() == INSERT_HINTS

    def test_default_hints(self) -> None:
        assert len(DEFAULT_HINTS) > 0
        assert any(h.key == "q" for h in DEFAULT_HINTS)

    def test_key_hint_dataclass(self) -> None:
        hint = KeyHint("Ctrl+K", "command")
        assert hint.key == "Ctrl+K"
        assert hint.description == "command"

    def test_render_hints_format(self) -> None:
        footer = NiuuFooter()
        rendered = footer._render_hints()
        assert "help" in rendered
        assert "quit" in rendered


# ── Tabs tests ───────────────────────────────────────────────


class TestNiuuTabs:
    async def test_tabs_render(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            tabs = NiuuTabs(items=["All", "Active", "Completed"])
            app.mount(tabs)
            await pilot.pause()
            assert tabs.active_tab == 0
            assert tabs.items == ["All", "Active", "Completed"]

    async def test_tabs_select(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            tabs = NiuuTabs(items=["A", "B", "C"])
            app.mount(tabs)
            await pilot.pause()
            tabs.select(2)
            await pilot.pause()
            assert tabs.active_tab == 2

    async def test_tabs_select_invalid(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            tabs = NiuuTabs(items=["A", "B"])
            app.mount(tabs)
            await pilot.pause()
            tabs.select(99)
            await pilot.pause()
            assert tabs.active_tab == 0

    async def test_tabs_set_items(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            tabs = NiuuTabs(items=["A"])
            app.mount(tabs)
            await pilot.pause()
            tabs.set_items(["X", "Y", "Z"])
            await pilot.pause()
            assert tabs.items == ["X", "Y", "Z"]

    def test_tabs_render_content(self) -> None:
        tabs = NiuuTabs(items=["A", "B"])
        rendered = tabs._render_tabs()
        assert "A" in rendered
        assert "B" in rendered


# ── StatusBadge tests ────────────────────────────────────────


class TestStatusBadge:
    def test_badge_properties(self) -> None:
        badge = StatusBadge("running")
        assert badge.status == "running"

    def test_badge_set_status(self) -> None:
        badge = StatusBadge("stopped")
        badge.set_status("error")
        assert badge.status == "error"

    def test_badge_unknown_status(self) -> None:
        badge = StatusBadge("unknown_state")
        assert badge.status == "unknown_state"

    def test_all_known_statuses(self) -> None:
        for status in [
            "running",
            "connected",
            "starting",
            "provisioning",
            "stopped",
            "disconnected",
            "error",
            "failed",
            "completed",
            "pending",
        ]:
            assert status in _STATUS_MAP

    def test_render_running(self) -> None:
        badge = StatusBadge("running")
        rendered = badge._render_badge()
        assert "●" in rendered
        assert "running" in rendered
        assert "#10b981" in rendered

    def test_render_error(self) -> None:
        badge = StatusBadge("error")
        rendered = badge._render_badge()
        assert "#ef4444" in rendered

    def test_render_default(self) -> None:
        badge = StatusBadge("unknown")
        rendered = badge._render_badge()
        assert "○" in rendered


# ── MetricCard tests ─────────────────────────────────────────


class TestMetricCard:
    def test_card_properties(self) -> None:
        card = MetricCard(label="Sessions", value="42", icon="📊")
        assert card.label == "Sessions"
        assert card.value == "42"

    def test_card_set_value(self) -> None:
        card = MetricCard(label="Count", value="0")
        card.set_value("99")
        assert card.value == "99"

    def test_card_default_color(self) -> None:
        assert DEFAULT_METRIC_COLOR == "#06b6d4"

    def test_card_custom_color(self) -> None:
        card = MetricCard(label="X", value="1", color="#ff0000")
        assert card._color == "#ff0000"

    async def test_metric_row_renders(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            row = MetricRow()
            app.mount(row)
            await pilot.pause()
            assert row is not None


# ── Modal tests ──────────────────────────────────────────────


class TestNiuuModal:
    def test_modal_properties(self) -> None:
        modal = NiuuModal(title="T", content="C")
        assert modal.title == "T"
        assert modal.content == "C"

    def test_modal_default_hidden(self) -> None:
        modal = NiuuModal()
        assert not modal.is_visible

    def test_modal_show_sets_visible(self) -> None:
        modal = NiuuModal()
        modal.show(title="New")
        assert modal.is_visible
        assert modal.title == "New"

    def test_modal_hide(self) -> None:
        modal = NiuuModal()
        modal.show()
        modal.hide()
        assert not modal.is_visible

    def test_modal_update_content(self) -> None:
        modal = NiuuModal(title="Old", content="Old body")
        modal.show(title="New", content="New body")
        assert modal.title == "New"
        assert modal.content == "New body"

    async def test_modal_visibility_class(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            modal = NiuuModal(title="Test")
            app.mount(modal)
            await pilot.pause()
            assert not modal.has_class("visible")
            modal.is_visible = True
            await pilot.pause()
            assert modal.has_class("visible")


# ── HelpOverlay tests ───────────────────────────────────────


class TestHelpOverlay:
    async def test_help_mounts(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("question_mark")
            await pilot.pause()
            assert app.screen.__class__.__name__ == "HelpOverlay"

    async def test_help_dismiss_escape(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("question_mark")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen.__class__.__name__ != "HelpOverlay"

    def test_help_custom_bindings(self) -> None:
        custom = [KeyBinding("x", "do thing")]
        overlay = HelpOverlay(bindings=custom)
        assert overlay.bindings_list == custom

    def test_default_bindings_exist(self) -> None:
        assert len(DEFAULT_BINDINGS) > 0
        sections = [b for b in DEFAULT_BINDINGS if b.section]
        assert len(sections) >= 3

    def test_key_binding_dataclass(self) -> None:
        kb = KeyBinding("Ctrl+K", "palette", section="Modes")
        assert kb.key == "Ctrl+K"
        assert kb.description == "palette"
        assert kb.section == "Modes"

    def test_key_binding_default_section(self) -> None:
        kb = KeyBinding("q", "quit")
        assert kb.section == ""

    def test_build_rows(self) -> None:
        overlay = HelpOverlay()
        rows = overlay._build_rows()
        assert len(rows) > 0


# ── CommandPalette tests ─────────────────────────────────────


class TestCommandPalette:
    def test_fuzzy_match_basic(self) -> None:
        assert _fuzzy_match("ses", "Sessions")
        assert _fuzzy_match("SES", "Sessions")
        assert not _fuzzy_match("xyz", "Sessions")

    def test_fuzzy_match_subsequence(self) -> None:
        assert _fuzzy_match("sn", "Sessions")
        assert _fuzzy_match("ss", "Sessions")

    def test_fuzzy_match_empty(self) -> None:
        assert _fuzzy_match("", "anything")

    def test_fuzzy_match_no_match(self) -> None:
        assert not _fuzzy_match("zzz", "abc")

    async def test_palette_opens(self) -> None:
        app = NiuuTUI(pages=SAMPLE_PAGES)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+k")
            await pilot.pause()
            assert app.mode == InputMode.COMMAND
            assert app.screen.__class__.__name__ == "CommandPalette"

    async def test_palette_dismiss(self) -> None:
        app = NiuuTUI(pages=SAMPLE_PAGES)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+k")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert app.mode == InputMode.NORMAL

    def test_palette_item_dataclass(self) -> None:
        item = PaletteItem(
            label="Test",
            description="A test",
            item_type=PaletteItemType.ACTION,
            icon="!",
            action_id="test",
        )
        assert item.label == "Test"
        assert item.item_type == PaletteItemType.ACTION

    def test_palette_item_types(self) -> None:
        assert PaletteItemType.SESSION == "session"
        assert PaletteItemType.PAGE == "page"
        assert PaletteItemType.ACTION == "action"

    def test_palette_item_defaults(self) -> None:
        item = PaletteItem(label="X")
        assert item.description == ""
        assert item.item_type == PaletteItemType.ACTION
        assert item.icon == ""
        assert item.action_id == ""

    async def test_palette_filtering(self) -> None:
        items = [
            PaletteItem(label="Sessions", item_type=PaletteItemType.PAGE),
            PaletteItem(label="Settings", item_type=PaletteItemType.PAGE),
            PaletteItem(label="Help", item_type=PaletteItemType.ACTION),
        ]
        palette = CommandPalette(items)
        app = NiuuTUI()
        async with app.run_test() as pilot:
            app.push_screen(palette)
            await pilot.pause()
            assert len(palette.matched) == 3
            palette._filter("ses")
            assert len(palette.matched) == 2  # Sessions + Settings (s-e-s subsequence)
            palette._filter("help")
            assert len(palette.matched) == 1

    async def test_palette_cursor_movement(self) -> None:
        items = [
            PaletteItem(label="A"),
            PaletteItem(label="B"),
            PaletteItem(label="C"),
        ]
        palette = CommandPalette(items)
        app = NiuuTUI()
        async with app.run_test() as pilot:
            app.push_screen(palette)
            await pilot.pause()
            assert palette.cursor == 0
            palette._move_cursor(1)
            assert palette.cursor == 1
            palette._move_cursor(-1)
            assert palette.cursor == 0

    async def test_palette_cursor_wraps(self) -> None:
        items = [PaletteItem(label="A"), PaletteItem(label="B")]
        palette = CommandPalette(items)
        app = NiuuTUI()
        async with app.run_test() as pilot:
            app.push_screen(palette)
            await pilot.pause()
            palette._move_cursor(-1)
            assert palette.cursor == 1

    async def test_palette_filter_empty(self) -> None:
        items = [PaletteItem(label="A")]
        palette = CommandPalette(items)
        app = NiuuTUI()
        async with app.run_test() as pilot:
            app.push_screen(palette)
            await pilot.pause()
            palette._filter("zzz")
            assert len(palette.matched) == 0


# ── MentionMenu tests ───────────────────────────────────────


class TestMentionMenu:
    def test_fuzzy_contains(self) -> None:
        assert _fuzzy_contains("test", "Testing")
        assert _fuzzy_contains("TEST", "testing")
        assert not _fuzzy_contains("xyz", "testing")

    def test_fuzzy_contains_empty(self) -> None:
        assert _fuzzy_contains("", "anything")

    async def test_menu_hidden_by_default(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            menu = MentionMenu(trigger="@")
            app.mount(menu)
            await pilot.pause()
            assert not menu.active

    async def test_menu_open_close(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            menu = MentionMenu(trigger="@")
            app.mount(menu)
            await pilot.pause()
            items = [MentionItem(label="file.py", value="file.py")]
            menu.open(items)
            await pilot.pause()
            assert menu.active
            assert len(menu.items) == 1
            menu.close()
            await pilot.pause()
            assert not menu.active

    async def test_menu_navigation(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            menu = MentionMenu()
            app.mount(menu)
            await pilot.pause()
            items = [
                MentionItem(label="a", value="a"),
                MentionItem(label="b", value="b"),
                MentionItem(label="c", value="c"),
            ]
            menu.open(items)
            await pilot.pause()
            assert menu.selected == 0
            menu.move_down()
            assert menu.selected == 1
            menu.move_up()
            assert menu.selected == 0

    async def test_menu_wrap_around(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            menu = MentionMenu()
            app.mount(menu)
            await pilot.pause()
            items = [
                MentionItem(label="a", value="a"),
                MentionItem(label="b", value="b"),
            ]
            menu.open(items)
            await pilot.pause()
            menu.move_up()
            assert menu.selected == 1

    async def test_menu_select_current(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            menu = MentionMenu()
            app.mount(menu)
            await pilot.pause()
            items = [MentionItem(label="file.py", value="file.py")]
            menu.open(items)
            await pilot.pause()
            result = menu.select_current()
            assert result is not None
            assert result.label == "file.py"
            assert not menu.active

    async def test_menu_select_empty(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            menu = MentionMenu()
            app.mount(menu)
            await pilot.pause()
            menu.open([])
            result = menu.select_current()
            assert result is None

    async def test_menu_set_query(self) -> None:
        app = NiuuTUI()
        async with app.run_test() as pilot:
            menu = MentionMenu()
            app.mount(menu)
            await pilot.pause()
            items = [
                MentionItem(label="alpha", value="a"),
                MentionItem(label="beta", value="b"),
            ]
            menu.open(items)
            menu.set_query("alp")
            assert len(menu.filtered) == 1

    def test_mention_item_dataclass(self) -> None:
        item = MentionItem(
            label="test.py",
            value="test.py",
            detail="src/",
            icon="📄",
            category="files",
        )
        assert item.label == "test.py"
        assert item.category == "files"

    def test_trigger_property(self) -> None:
        menu = MentionMenu(trigger="/")
        assert menu.trigger == "/"

    def test_filtered_empty_query(self) -> None:
        menu = MentionMenu()
        menu._items = [MentionItem(label="a", value="a")]
        assert len(menu.filtered) == 1

    def test_filtered_with_query(self) -> None:
        menu = MentionMenu()
        menu._items = [
            MentionItem(label="alpha", value="a"),
            MentionItem(label="beta", value="b"),
        ]
        menu._query = "alp"
        assert len(menu.filtered) == 1


# ── Theme constants tests ────────────────────────────────────


class TestThemeConstants:
    def test_theme_module_imports(self) -> None:
        from cli.tui.theme import (
            ACCENT_AMBER,
            ACCENT_CYAN,
            ACCENT_EMERALD,
            ACCENT_INDIGO,
            ACCENT_ORANGE,
            ACCENT_PURPLE,
            ACCENT_RED,
            BG_ELEVATED,
            BG_PRIMARY,
            BG_SECONDARY,
            BG_TERTIARY,
            BORDER,
            BORDER_SUBTLE,
            TEXT_MUTED,
            TEXT_PRIMARY,
            TEXT_SECONDARY,
        )

        assert BG_PRIMARY == "#09090b"
        assert BG_SECONDARY == "#18181b"
        assert BG_TERTIARY == "#27272a"
        assert BG_ELEVATED == "#3f3f46"
        assert TEXT_PRIMARY == "#fafafa"
        assert TEXT_SECONDARY == "#a1a1aa"
        assert TEXT_MUTED == "#71717a"
        assert BORDER == "#3f3f46"
        assert BORDER_SUBTLE == "#27272a"
        assert ACCENT_AMBER == "#f59e0b"
        assert ACCENT_CYAN == "#06b6d4"
        assert ACCENT_EMERALD == "#10b981"
        assert ACCENT_PURPLE == "#a855f7"
        assert ACCENT_RED == "#ef4444"
        assert ACCENT_INDIGO == "#6366f1"
        assert ACCENT_ORANGE == "#f97316"
