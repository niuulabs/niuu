"""Admin page — user/tenant tables and stats dashboard with search."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Static

from cli.tui.theme import (
    ACCENT_AMBER,
    ACCENT_CYAN,
    ACCENT_EMERALD,
    ACCENT_PURPLE,
    ACCENT_RED,
    BG_TERTIARY,
    BORDER_SUBTLE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from cli.tui.widgets.metric_card import MetricCard, MetricRow
from cli.tui.widgets.tabs import NiuuTabs
from volundr.tui._utils import format_count

ADMIN_TABS = ("Users", "Tenants", "Stats")


def _render_bar(value: int, maximum: int, width: int = 25) -> str:
    """Render an ASCII progress bar."""

    if maximum <= 0:
        return f"[{TEXT_MUTED}]{'░' * width}[/]"
    filled = min(width, int(value / maximum * width))
    empty = width - filled
    return f"[{ACCENT_EMERALD}]{'█' * filled}[/][{BG_TERTIARY}]{'░' * empty}[/]"


@dataclass
class UserInfo:
    """Admin user info."""

    display_name: str = ""
    email: str = ""
    status: str = "active"
    created_at: str = ""


@dataclass
class Tenant:
    """Admin tenant info."""

    name: str = ""
    tenant_id: str = ""
    created_at: str = ""


@dataclass
class StatsData:
    """Admin stats response."""

    active_sessions: int = 0
    total_sessions: int = 0
    tokens_today: int = 0
    cost_today: float = 0.0
    local_tokens: int = 0
    cloud_tokens: int = 0


class AdminPage(Widget):
    """Admin panel with Users, Tenants, and Stats tabs.

    Keybindings:
        tab/shift+tab   switch tab
        j/k             navigate rows
        /               search
        G/g             jump to last/first
        r               refresh
    """

    DEFAULT_CSS = """
    AdminPage { width: 1fr; height: 1fr; }
    AdminPage #admin-tabs { height: auto; }
    AdminPage #admin-search { height: auto; display: none; }
    AdminPage #admin-search.visible { display: block; }
    AdminPage #admin-content { height: 1fr; overflow-y: auto; }
    AdminPage #admin-empty { color: #71717a; padding: 2; }
    """

    tab_index: reactive[int] = reactive(0)
    cursor: reactive[int] = reactive(0)
    searching: reactive[bool] = reactive(False)

    def __init__(
        self,
        users: list[UserInfo] | None = None,
        tenants: list[Tenant] | None = None,
        stats: StatsData | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._users: list[UserInfo] = users or []
        self._tenants: list[Tenant] = tenants or []
        self._stats = stats
        self._search_term = ""
        self._load_error: str = ""
        self._mounted = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield NiuuTabs(list(ADMIN_TABS), id="admin-tabs")
            with Horizontal(id="admin-search"):
                yield Input(placeholder="Search…", id="admin-search-input")
            yield Vertical(id="admin-content")

    def on_mount(self) -> None:
        self._mounted = True
        self._rebuild_content()

    # ── Data setters ────────────────────────────────────────

    def set_users(self, users: list[UserInfo]) -> None:
        self._users = list(users)
        if self.tab_index == 0:
            self._rebuild_content()

    def set_tenants(self, tenants: list[Tenant]) -> None:
        self._tenants = list(tenants)
        if self.tab_index == 1:
            self._rebuild_content()

    def set_stats(self, stats: StatsData) -> None:
        self._stats = stats
        if self.tab_index == 2:
            self._rebuild_content()

    def set_error(self, error: str) -> None:
        self._load_error = error
        self._rebuild_content()

    # ── Tab selection ────────────────────────────────────────

    def on_niuu_tabs_tab_selected(self, event: NiuuTabs.TabSelected) -> None:
        self.tab_index = event.index
        self.cursor = 0
        self._rebuild_content()

    # ── Content rendering ────────────────────────────────────

    def _rebuild_content(self) -> None:
        if not self._mounted:
            return
        try:
            container = self.query_one("#admin-content", Vertical)
        except Exception:
            return
        container.remove_children()

        if self._load_error:
            container.mount(Static(f"[{ACCENT_RED}]  Error: {self._load_error}  (r to retry)[/]"))
            return

        match self.tab_index:
            case 0:
                self._mount_users(container)
            case 1:
                self._mount_tenants(container)
            case 2:
                self._mount_stats(container)

    def _mount_users(self, container: Vertical) -> None:
        users = self._filtered_users()
        if not users:
            msg = f"[{TEXT_MUTED}]  No users found (admin role required)[/]"
            container.mount(Static(msg))
            return
        # Header
        header = f"  [{TEXT_MUTED}]{'Name':<24} {'Email':<30} {'Status':<12} Created[/]"
        container.mount(Static(header))
        container.mount(Static(f"[{BORDER_SUBTLE}]  {'─' * 80}[/]"))
        for i, user in enumerate(users):
            status_dot, status_color = _user_status_style(user.status)
            row = (
                f"  [bold {TEXT_PRIMARY}]{user.display_name:<24}[/]"
                f"[{TEXT_SECONDARY}]{user.email:<30}[/]"
                f"[{status_color}]{status_dot} {user.status:<10}[/]"
                f"[{TEXT_MUTED}]{user.created_at}[/]"
            )
            widget = Static(row)
            container.mount(widget)
            if i == self.cursor:
                widget.add_class("selected")

    def _mount_tenants(self, container: Vertical) -> None:
        tenants = self._filtered_tenants()
        if not tenants:
            container.mount(Static(f"[{TEXT_MUTED}]  No tenants found[/]"))
            return
        header = f"  [{TEXT_MUTED}]{'Name':<24} {'ID':<38} Created[/]"
        container.mount(Static(header))
        container.mount(Static(f"[{BORDER_SUBTLE}]  {'─' * 80}[/]"))
        for i, tenant in enumerate(tenants):
            row = (
                f"  [bold {TEXT_PRIMARY}]{tenant.name:<24}[/]"
                f"[{TEXT_SECONDARY}]{tenant.tenant_id:<38}[/]"
                f"[{TEXT_MUTED}]{tenant.created_at}[/]"
            )
            widget = Static(row)
            container.mount(widget)
            if i == self.cursor:
                widget.add_class("selected")

    def _mount_stats(self, container: Vertical) -> None:
        if not self._stats:
            container.mount(Static(f"[{TEXT_MUTED}]  No stats available[/]"))
            return
        s = self._stats
        row = MetricRow()
        container.mount(row)
        row.mount(MetricCard("Active", str(s.active_sessions), icon="▶", color=ACCENT_EMERALD))
        row.mount(MetricCard("Total", str(s.total_sessions), icon="◉", color=ACCENT_AMBER))
        row.mount(
            MetricCard(
                "Tokens Today",
                format_count(s.tokens_today),
                icon="◈",
                color=ACCENT_PURPLE,
            )
        )
        row.mount(MetricCard("Cost Today", f"${s.cost_today:.2f}", icon="$", color=ACCENT_AMBER))

        # Token breakdown
        container.mount(Static(""))
        container.mount(Static(f"  [bold {TEXT_PRIMARY}]Token Breakdown[/]"))
        container.mount(Static(""))

        local_pct = (s.local_tokens * 100 // s.tokens_today) if s.tokens_today > 0 else 0
        cloud_pct = (s.cloud_tokens * 100 // s.tokens_today) if s.tokens_today > 0 else 0
        container.mount(
            Static(
                f"  [{TEXT_SECONDARY}]{'Local Tokens:':>16}[/]  "
                f"[bold {ACCENT_EMERALD}]{format_count(s.local_tokens)}[/]  "
                f"{_render_bar(local_pct, 100)}"
            )
        )
        container.mount(
            Static(
                f"  [{TEXT_SECONDARY}]{'Cloud Tokens:':>16}[/]  "
                f"[bold {ACCENT_CYAN}]{format_count(s.cloud_tokens)}[/]  "
                f"{_render_bar(cloud_pct, 100)}"
            )
        )

    # ── Search / filter ──────────────────────────────────────

    def _filtered_users(self) -> list[UserInfo]:
        if not self._search_term:
            return self._users
        s = self._search_term.lower()
        return [
            u
            for u in self._users
            if s in u.display_name.lower() or s in u.email.lower() or s in u.status.lower()
        ]

    def _filtered_tenants(self) -> list[Tenant]:
        if not self._search_term:
            return self._tenants
        s = self._search_term.lower()
        return [t for t in self._tenants if s in t.name.lower() or s in t.tenant_id.lower()]

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "admin-search-input":
            self._search_term = event.value
            self.cursor = 0
            self._rebuild_content()

    def action_toggle_search(self) -> None:
        self.searching = not self.searching

    def watch_searching(self, value: bool) -> None:
        try:
            box = self.query_one("#admin-search", Horizontal)
        except Exception:
            return
        if value:
            box.add_class("visible")
            with suppress(Exception):
                self.query_one("#admin-search-input", Input).focus()
        else:
            box.remove_class("visible")

    # ── Cursor ──────────────────────────────────────────────

    def watch_cursor(self) -> None:
        self._rebuild_content()

    def action_cursor_up(self) -> None:
        if self.cursor > 0:
            self.cursor -= 1

    def _max_cursor(self) -> int:
        """Return the maximum valid cursor index for the current tab."""
        match self.tab_index:
            case 0:
                return max(0, len(self._filtered_users()) - 1)
            case 1:
                return max(0, len(self._filtered_tenants()) - 1)
            case _:
                return 0
        raise AssertionError("Unreachable _max_cursor fallthrough")

    def action_cursor_down(self) -> None:
        if self.cursor < self._max_cursor():
            self.cursor += 1

    def action_cursor_top(self) -> None:
        self.cursor = 0

    def action_cursor_bottom(self) -> None:
        self.cursor = self._max_cursor()

    def action_refresh(self) -> None:
        self._rebuild_content()


def _user_status_style(status: str) -> tuple[str, str]:
    match status:
        case "active":
            return ("●", ACCENT_EMERALD)
        case "inactive" | "disabled":
            return ("○", TEXT_MUTED)
        case "pending":
            return ("◐", ACCENT_AMBER)
        case _:
            return ("○", TEXT_MUTED)
    raise AssertionError("Unreachable _user_status_style fallthrough")
