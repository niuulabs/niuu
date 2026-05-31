"""Tests for volundr TUI pages — Textual pilot tests with mock data."""

from __future__ import annotations

from textual.app import App, ComposeResult

from volundr.tui._utils import format_count
from volundr.tui.admin import (
    AdminPage,
    StatsData,
    Tenant,
    UserInfo,
    _render_bar,
    _user_status_style,
)
from volundr.tui.chat import (
    SLASH_COMMANDS,
    ChatMessage,
    ChatPage,
    MessageBubble,
    _role_color,
    _role_icon,
)
from volundr.tui.chronicles import (
    CHRONICLE_FILTERS,
    EVENT_STYLES,
    ChronicleEvent,
    ChroniclesPage,
    _count_by_type,
    _event_matches,
    _format_elapsed,
)
from volundr.tui.diffs import (
    DiffFile,
    DiffsPage,
    _colorize_diff_line,
    _status_color,
    _truncate_path,
)
from volundr.tui.sessions import (
    SESSION_FILTERS,
    SessionData,
    SessionsPage,
    _demo_sessions,
    _session_matches_search,
)
from volundr.tui.settings import (
    IntegrationEntry,
    SettingsPage,
    UserProfile,
    _mask_token,
)
from volundr.tui.terminal import (
    MAX_SCROLLBACK_LINES,
    TerminalPage,
    TerminalTab,
)

# ── Helpers ─────────────────────────────────────────────────


class PageTestApp(App):
    """Wrapper app for testing a single page widget."""

    def __init__(self, page_widget_class: type, **page_kwargs: object) -> None:
        super().__init__()
        self._page_cls = page_widget_class
        self._page_kwargs = page_kwargs

    def compose(self) -> ComposeResult:
        yield self._page_cls(**self._page_kwargs)


def _sample_sessions() -> list[SessionData]:
    return [
        SessionData(
            id="s1",
            name="feat/auth",
            status="running",
            model="claude-sonnet-4",
            repo="niuu/volundr",
            tokens_used=128_000,
            context_key="prod",
        ),
        SessionData(
            id="s2",
            name="fix/bug",
            status="stopped",
            model="claude-opus-4",
            repo="niuu/ting",
            tokens_used=50_000,
            context_key="dev",
        ),
        SessionData(
            id="s3",
            name="test/ci",
            status="error",
            model="claude-haiku-3.5",
            repo="niuu/docs",
            tokens_used=5_000,
            context_key="prod",
        ),
    ]


def _sample_events() -> list[ChronicleEvent]:
    return [
        ChronicleEvent(event_type="session", label="Session started", elapsed=0),
        ChronicleEvent(event_type="message", label="User prompt", elapsed=5, tokens=1200),
        ChronicleEvent(
            event_type="file",
            label="src/main.py",
            action="modified",
            insertions=15,
            deletions=3,
            elapsed=30,
        ),
        ChronicleEvent(
            event_type="git", label="feat: add auth", git_hash="abc12345def", elapsed=120
        ),
        ChronicleEvent(event_type="terminal", label="pytest", action="exit 0", elapsed=150),
        ChronicleEvent(event_type="error", label="OOM killed", elapsed=200),
    ]


def _sample_diffs() -> list[DiffFile]:
    return [
        DiffFile(
            path="src/main.py",
            status="M",
            additions=15,
            deletions=3,
            diff="+added line\n-removed line\n context\n@@hunk@@\n+new",
        ),
        DiffFile(
            path="src/new_file.py", status="A", additions=50, deletions=0, diff="+entire new file"
        ),
        DiffFile(
            path="src/old_file.py", status="D", additions=0, deletions=30, diff="-deleted content"
        ),
    ]


def _sample_users() -> list[UserInfo]:
    return [
        UserInfo(
            display_name="Alice", email="alice@niuu.dev", status="active", created_at="2026-01-01"
        ),
        UserInfo(
            display_name="Bob", email="bob@niuu.dev", status="inactive", created_at="2026-02-01"
        ),
    ]


def _sample_tenants() -> list[Tenant]:
    return [
        Tenant(name="Niuu Labs", tenant_id="t-001", created_at="2025-01-01"),
        Tenant(name="Acme Corp", tenant_id="t-002", created_at="2025-06-01"),
    ]


# ── Sessions page tests ─────────────────────────────────────


class TestSessionsPage:
    def test_demo_sessions(self) -> None:
        demos = _demo_sessions()
        assert len(demos) >= 5
        assert all(isinstance(s, SessionData) for s in demos)

    def test_format_count(self) -> None:
        assert format_count(500) == "500"
        assert format_count(1_500) == "1.5K"
        assert format_count(2_500_000) == "2.5M"

    def test_session_matches_search(self) -> None:
        s = SessionData(name="feat/auth", repo="niuu/volundr", model="sonnet", context_key="prod")
        assert _session_matches_search(s, "auth")
        assert _session_matches_search(s, "volundr")
        assert _session_matches_search(s, "sonnet")
        assert _session_matches_search(s, "prod")
        assert not _session_matches_search(s, "xyz")

    def test_filter_constants(self) -> None:
        assert SESSION_FILTERS == ("All", "Running", "Stopped", "Error")

    async def test_sessions_page_renders(self) -> None:
        app = PageTestApp(SessionsPage, sessions=_sample_sessions())
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SessionsPage)
            assert page is not None

    async def test_sessions_page_filter(self) -> None:
        sessions = _sample_sessions()
        app = PageTestApp(SessionsPage, sessions=sessions)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SessionsPage)
            page.filter_index = 1  # Running
            page._apply_filter()
            assert len(page._filtered) == 1
            assert page._filtered[0].status == "running"

    async def test_sessions_page_search(self) -> None:
        sessions = _sample_sessions()
        app = PageTestApp(SessionsPage, sessions=sessions)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SessionsPage)
            page._search_term = "auth"
            page._apply_filter()
            assert len(page._filtered) == 1
            assert page._filtered[0].name == "feat/auth"

    async def test_sessions_cursor_navigation(self) -> None:
        sessions = _sample_sessions()
        app = PageTestApp(SessionsPage, sessions=sessions)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SessionsPage)
            assert page.cursor == 0
            page.action_cursor_down()
            assert page.cursor == 1
            page.action_cursor_down()
            assert page.cursor == 2
            page.action_cursor_down()
            assert page.cursor == 2  # Clamped
            page.action_cursor_top()
            assert page.cursor == 0
            page.action_cursor_bottom()
            assert page.cursor == 2

    async def test_sessions_context_cycle(self) -> None:
        sessions = _sample_sessions()
        app = PageTestApp(SessionsPage, sessions=sessions)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SessionsPage)
            assert page._context_filter == ""
            page.action_cycle_context()
            assert page._context_filter != ""

    async def test_sessions_set_sessions(self) -> None:
        app = PageTestApp(SessionsPage, sessions=[])
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SessionsPage)
            assert len(page._filtered) == 0
            page.set_sessions(_sample_sessions())
            assert len(page._filtered) == 3

    async def test_sessions_action_messages(self) -> None:
        sessions = _sample_sessions()
        app = PageTestApp(SessionsPage, sessions=sessions)
        messages: list[SessionsPage.SessionAction] = []
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SessionsPage)
            page.on_mount()

            # Verify selected session returns correctly
            selected = page._selected_session()
            assert selected is not None
            assert selected.id == "s1"


# ── Chat page tests ──────────────────────────────────────────


class TestChatPage:
    def test_role_color(self) -> None:
        assert _role_color("user") != _role_color("assistant")
        assert _role_color("system") != _role_color("unknown")

    def test_role_icon(self) -> None:
        assert _role_icon("user") == "◆"
        assert _role_icon("assistant") == "◈"
        assert _role_icon("system") == "◉"

    def test_slash_commands(self) -> None:
        assert len(SLASH_COMMANDS) == 8
        labels = {c.label for c in SLASH_COMMANDS}
        assert "help" in labels
        assert "commit" in labels

    async def test_chat_page_renders(self) -> None:
        app = PageTestApp(ChatPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(ChatPage)
            assert page is not None

    async def test_chat_add_message(self) -> None:
        app = PageTestApp(ChatPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(ChatPage)
            assert len(page._messages) == 0
            page.add_message(ChatMessage(role="user", content="Hello"))
            assert len(page._messages) == 1
            assert page._messages[0].content == "Hello"

    async def test_chat_streaming(self) -> None:
        app = PageTestApp(ChatPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(ChatPage)
            page.start_streaming()
            assert len(page._messages) == 1
            assert page._messages[0].status == "running"
            page.append_stream_delta("Hello ")
            page.append_stream_delta("world")
            assert page._messages[0].content == "Hello world"
            page.finish_streaming(tokens=100, cost=0.01)
            assert page._messages[0].status == "complete"
            assert page._total_tokens == 100

    async def test_chat_model_display(self) -> None:
        app = PageTestApp(ChatPage, model="claude-opus-4")
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(ChatPage)
            assert page._model == "claude-opus-4"

    def test_message_bubble_compose(self) -> None:
        msg = ChatMessage(role="user", content="test", status="complete")
        bubble = MessageBubble(msg)
        assert bubble._message.content == "test"


# ── Terminal page tests ──────────────────────────────────────


class TestTerminalPage:
    def test_terminal_tab_defaults(self) -> None:
        tab = TerminalTab()
        assert tab.label == "shell"
        assert tab.conn_state == "disconnected"
        assert tab.lines == []

    async def test_terminal_page_renders(self) -> None:
        app = PageTestApp(TerminalPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(TerminalPage)
            assert page is not None
            assert len(page._tabs) == 1

    async def test_terminal_new_tab(self) -> None:
        app = PageTestApp(TerminalPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(TerminalPage)
            assert len(page._tabs) == 1
            page.action_new_tab()
            assert len(page._tabs) == 2
            assert page.active_tab == 1

    async def test_terminal_close_tab(self) -> None:
        app = PageTestApp(TerminalPage, tabs=[TerminalTab(label="t1"), TerminalTab(label="t2")])
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(TerminalPage)
            assert len(page._tabs) == 2
            page.active_tab = 1
            page.action_close_tab()
            assert len(page._tabs) == 1
            assert page.active_tab == 0

    async def test_terminal_close_last_tab_noop(self) -> None:
        app = PageTestApp(TerminalPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(TerminalPage)
            page.action_close_tab()
            assert len(page._tabs) == 1

    async def test_terminal_tab_cycling(self) -> None:
        tabs = [TerminalTab(label="t1"), TerminalTab(label="t2"), TerminalTab(label="t3")]
        app = PageTestApp(TerminalPage, tabs=tabs)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(TerminalPage)
            assert page.active_tab == 0
            page.action_next_tab()
            assert page.active_tab == 1
            page.action_next_tab()
            assert page.active_tab == 2
            page.action_next_tab()
            assert page.active_tab == 0  # Wraps
            page.action_prev_tab()
            assert page.active_tab == 2  # Wraps back

    async def test_terminal_insert_mode(self) -> None:
        app = PageTestApp(TerminalPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(TerminalPage)
            assert not page.insert_mode
            page.action_enter_insert()
            assert page.insert_mode
            page.action_exit_insert()
            assert not page.insert_mode

    async def test_terminal_append_output(self) -> None:
        app = PageTestApp(TerminalPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(TerminalPage)
            page.append_output("hello\nworld")
            assert page._tabs[0].lines == ["hello", "world"]
            page.append_output(" more")
            assert page._tabs[0].lines == ["hello", "world more"]

    async def test_terminal_scrollback_limit(self) -> None:
        app = PageTestApp(TerminalPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(TerminalPage)
            big_text = "\n".join(f"line-{i}" for i in range(MAX_SCROLLBACK_LINES + 100))
            page.append_output(big_text)
            assert len(page._tabs[0].lines) == MAX_SCROLLBACK_LINES

    async def test_terminal_set_tab_state(self) -> None:
        app = PageTestApp(TerminalPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(TerminalPage)
            page.set_tab_state(0, "connected")
            assert page._tabs[0].conn_state == "connected"

    async def test_terminal_scroll_actions(self) -> None:
        app = PageTestApp(TerminalPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(TerminalPage)
            lines = "\n".join(f"line-{i}" for i in range(50))
            page.append_output(lines)
            page.action_scroll_up()
            assert page._scroll_offset == 1
            page.action_scroll_down()
            assert page._scroll_offset == 0
            page.action_scroll_top()
            assert page._scroll_offset > 0
            page.action_scroll_bottom()
            assert page._scroll_offset == 0


# ── Diffs page tests ────────────────────────────────────────


class TestDiffsPage:
    def test_status_color(self) -> None:
        assert _status_color("M") != _status_color("A")
        assert _status_color("A") != _status_color("D")

    def test_truncate_path(self) -> None:
        assert _truncate_path("short.py", 20) == "short.py"
        long_path = "a/very/long/path/to/some/deep/file.py"
        result = _truncate_path(long_path, 15)
        assert result.startswith("...")
        assert len(result) == 15

    def test_colorize_diff_line(self) -> None:
        assert "+" in _colorize_diff_line("+added")
        assert "-" in _colorize_diff_line("-removed")
        assert "@@" in _colorize_diff_line("@@hunk@@")

    def test_colorize_diff_line_escapes_markup(self) -> None:
        result = _colorize_diff_line("+line with [bold]markup[/bold]")
        # Rich markup chars should be escaped so they render as literal text
        assert "\\[bold]" in result or "[bold]" not in result.split("[/]")[0].split("]", 2)[-1]
        # Simpler check: the escaped line shouldn't contain unescaped [bold]
        from rich.markup import escape

        assert escape("[bold]markup[/bold]") in result

    async def test_diffs_page_renders(self) -> None:
        app = PageTestApp(DiffsPage, files=_sample_diffs())
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(DiffsPage)
            assert page is not None
            assert len(page._filtered) == 3

    async def test_diffs_cursor(self) -> None:
        app = PageTestApp(DiffsPage, files=_sample_diffs())
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(DiffsPage)
            assert page.cursor == 0
            page.action_cursor_down()
            assert page.cursor == 1
            page.action_cursor_bottom()
            assert page.cursor == 2
            page.action_cursor_top()
            assert page.cursor == 0

    async def test_diffs_search(self) -> None:
        app = PageTestApp(DiffsPage, files=_sample_diffs())
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(DiffsPage)
            page._search_term = "main"
            page._apply_filter()
            assert len(page._filtered) == 1
            assert page._filtered[0].path == "src/main.py"

    async def test_diffs_scroll(self) -> None:
        app = PageTestApp(DiffsPage, files=_sample_diffs())
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(DiffsPage)
            page.action_scroll_diff_down()
            assert page.scroll_pos == 1
            page.action_scroll_diff_up()
            assert page.scroll_pos == 0
            page.action_scroll_diff_up()
            assert page.scroll_pos == 0  # Can't go below 0

    async def test_diffs_set_files(self) -> None:
        app = PageTestApp(DiffsPage, files=[])
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(DiffsPage)
            assert len(page._filtered) == 0
            page.set_files(_sample_diffs())
            assert len(page._filtered) == 3


# ── Chronicles page tests ────────────────────────────────────


class TestChroniclesPage:
    def test_format_elapsed(self) -> None:
        assert _format_elapsed(5) == "5s"
        assert _format_elapsed(65) == "1m05s"
        assert _format_elapsed(3665) == "1h01m"

    def test_count_by_type(self) -> None:
        events = _sample_events()
        counts = _count_by_type(events)
        assert counts["session"] == 1
        assert counts["message"] == 1
        assert counts["file"] == 1
        assert counts["git"] == 1
        assert counts["terminal"] == 1
        assert counts["error"] == 1

    def test_event_matches(self) -> None:
        e = ChronicleEvent(event_type="git", label="feat: auth", git_hash="abc123")
        assert _event_matches(e, "auth")
        assert _event_matches(e, "abc")
        assert _event_matches(e, "git")
        assert not _event_matches(e, "xyz")

    def test_event_styles(self) -> None:
        for event_type in ("session", "message", "file", "git", "terminal", "error"):
            assert event_type in EVENT_STYLES

    def test_chronicle_filters(self) -> None:
        assert CHRONICLE_FILTERS[0] == "All"
        assert len(CHRONICLE_FILTERS) == 7

    async def test_chronicles_page_renders(self) -> None:
        app = PageTestApp(ChroniclesPage, events=_sample_events())
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(ChroniclesPage)
            assert page is not None
            assert len(page._filtered) == 6

    async def test_chronicles_filter_by_type(self) -> None:
        app = PageTestApp(ChroniclesPage, events=_sample_events())
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(ChroniclesPage)
            page.filter_index = 4  # "Git"
            page._apply_filter()
            assert len(page._filtered) == 1
            assert page._filtered[0].event_type == "git"

    async def test_chronicles_search(self) -> None:
        app = PageTestApp(ChroniclesPage, events=_sample_events())
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(ChroniclesPage)
            page._search_term = "pytest"
            page._apply_filter()
            assert len(page._filtered) == 1

    async def test_chronicles_cursor(self) -> None:
        app = PageTestApp(ChroniclesPage, events=_sample_events())
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(ChroniclesPage)
            assert page.cursor == 0
            page.action_cursor_down()
            assert page.cursor == 1
            page.action_cursor_bottom()
            assert page.cursor == 5
            page.action_cursor_top()
            assert page.cursor == 0

    async def test_chronicles_set_events(self) -> None:
        app = PageTestApp(ChroniclesPage, events=[])
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(ChroniclesPage)
            assert len(page._filtered) == 0
            page.set_events(_sample_events())
            assert len(page._filtered) == 6


# ── Settings page tests ──────────────────────────────────────


class TestSettingsPage:
    def test_mask_token(self) -> None:
        assert _mask_token("") == "(not set)"
        assert _mask_token("short") == "●●●●●●●●"
        result = _mask_token("a-very-long-token-12345")
        assert result.startswith("●")
        assert result.endswith("2345")

    async def test_settings_page_renders(self) -> None:
        app = PageTestApp(SettingsPage, server_url="http://localhost:8080", token="my-token")
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SettingsPage)
            assert page is not None
            assert page.section == 0

    async def test_settings_profile_tab(self) -> None:
        profile = UserProfile(
            user_id="u-1",
            display_name="Alice",
            email="alice@niuu.dev",
            tenant_id="t-1",
            roles=["admin"],
            status="active",
        )
        app = PageTestApp(SettingsPage, profile=profile)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SettingsPage)
            page.section = 1
            page._rebuild_content()
            assert page._profile is not None
            assert page._profile.display_name == "Alice"

    async def test_settings_integrations_tab(self) -> None:
        integrations = [
            IntegrationEntry(name="GitHub", slug="github", enabled=True, icon="⊕"),
            IntegrationEntry(name="Linear", slug="linear", enabled=False, icon="◈"),
        ]
        app = PageTestApp(SettingsPage, integrations=integrations)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SettingsPage)
            page.section = 2
            page._rebuild_content()
            assert len(page._integrations) == 2

    async def test_settings_editing(self) -> None:
        app = PageTestApp(SettingsPage, server_url="http://old-url")
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SettingsPage)
            page.cursor = 0
            page.action_start_edit()
            assert page.editing
            page._edit_buf = "http://new-url"
            page.action_save_edit()
            assert not page.editing
            assert page._server_url == "http://new-url"

    async def test_settings_cancel_edit(self) -> None:
        app = PageTestApp(SettingsPage, server_url="http://old-url")
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SettingsPage)
            page.cursor = 0
            page.action_start_edit()
            assert page.editing
            page.action_cancel_edit()
            assert not page.editing
            assert page._server_url == "http://old-url"

    async def test_settings_cursor(self) -> None:
        app = PageTestApp(SettingsPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SettingsPage)
            assert page.cursor == 0
            page.action_cursor_down()
            assert page.cursor == 1
            page.action_cursor_top()
            assert page.cursor == 0

    async def test_settings_set_profile(self) -> None:
        app = PageTestApp(SettingsPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SettingsPage)
            profile = UserProfile(display_name="Bob")
            page.set_profile(profile)
            assert page._profile.display_name == "Bob"

    async def test_settings_set_integrations(self) -> None:
        app = PageTestApp(SettingsPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(SettingsPage)
            page.set_integrations([IntegrationEntry(name="Slack")])
            assert len(page._integrations) == 1


# ── Admin page tests ─────────────────────────────────────────


class TestAdminPage:
    def test_render_bar(self) -> None:
        bar = _render_bar(50, 100, width=10)
        assert "█" in bar
        assert "░" in bar

    def test_render_bar_zero(self) -> None:
        bar = _render_bar(0, 0, width=10)
        assert "░" in bar

    def test_user_status_style(self) -> None:
        dot, color = _user_status_style("active")
        assert dot == "●"
        dot2, _ = _user_status_style("inactive")
        assert dot2 == "○"
        dot3, _ = _user_status_style("pending")
        assert dot3 == "◐"

    async def test_admin_page_renders(self) -> None:
        app = PageTestApp(AdminPage, users=_sample_users(), tenants=_sample_tenants())
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(AdminPage)
            assert page is not None

    async def test_admin_user_search(self) -> None:
        app = PageTestApp(AdminPage, users=_sample_users())
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(AdminPage)
            page._search_term = "alice"
            filtered = page._filtered_users()
            assert len(filtered) == 1
            assert filtered[0].display_name == "Alice"

    async def test_admin_tenant_search(self) -> None:
        app = PageTestApp(AdminPage, tenants=_sample_tenants())
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(AdminPage)
            page.tab_index = 1
            page._search_term = "acme"
            filtered = page._filtered_tenants()
            assert len(filtered) == 1
            assert filtered[0].name == "Acme Corp"

    async def test_admin_stats(self) -> None:
        stats = StatsData(
            active_sessions=5,
            total_sessions=20,
            tokens_today=100_000,
            cost_today=1.50,
            local_tokens=60_000,
            cloud_tokens=40_000,
        )
        app = PageTestApp(AdminPage, stats=stats)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(AdminPage)
            page.tab_index = 2
            page._rebuild_content()
            assert page._stats is not None
            assert page._stats.active_sessions == 5

    async def test_admin_error_display(self) -> None:
        app = PageTestApp(AdminPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(AdminPage)
            page.set_error("403 Forbidden")
            assert page._load_error == "403 Forbidden"

    async def test_admin_cursor(self) -> None:
        app = PageTestApp(AdminPage, users=_sample_users())
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(AdminPage)
            page.action_cursor_down()
            assert page.cursor == 1
            page.action_cursor_top()
            assert page.cursor == 0
            page.action_cursor_bottom()
            assert page.cursor == 1

    async def test_admin_set_data(self) -> None:
        app = PageTestApp(AdminPage)
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(AdminPage)
            page.set_users(_sample_users())
            assert len(page._users) == 2
            page.set_tenants(_sample_tenants())
            assert len(page._tenants) == 2


# ── Plugin registration test ─────────────────────────────────


class TestVolundrPluginPages:
    def test_tui_pages_registered(self) -> None:
        from volundr.plugin import VolundrPlugin

        plugin = VolundrPlugin()
        pages = plugin.tui_pages()
        assert len(pages) == 7
        names = [p.name for p in pages]
        assert "Sessions" in names
        assert "Chat" in names
        assert "Terminal" in names
        assert "Diffs" in names
        assert "Chronicles" in names
        assert "Settings" in names
        assert "Admin" in names

    def test_page_specs_have_widget_classes(self) -> None:
        from volundr.plugin import VolundrPlugin

        plugin = VolundrPlugin()
        for spec in plugin.tui_pages():
            assert spec.name
            assert spec.icon
            assert spec.widget_class is not None
