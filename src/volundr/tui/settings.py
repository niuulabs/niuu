"""Settings page — tabbed configuration for connection, profile, integrations, appearance."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from cli.tui.theme import (
    ACCENT_PURPLE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from cli.tui.widgets.tabs import NiuuTabs

SETTINGS_TABS = ("Connection", "Profile", "Integrations", "Appearance")


def _mask_token(token: str) -> str:
    """Partially mask a token for display."""

    if not token:
        return "(not set)"
    if len(token) <= 8:
        return "●●●●●●●●"
    return "●●●●●●●●●●●●" + token[-4:]


@dataclass
class SettingRow:
    """A single setting field."""

    label: str = ""
    value: str = ""
    description: str = ""
    editable: bool = False


@dataclass
class UserProfile:
    """User profile data."""

    user_id: str = ""
    display_name: str = ""
    email: str = ""
    tenant_id: str = ""
    roles: list[str] = field(default_factory=list)
    status: str = "active"


@dataclass
class IntegrationEntry:
    """Integration catalog/connection entry."""

    name: str = ""
    slug: str = ""
    description: str = ""
    icon: str = "◈"
    integration_type: str = ""
    enabled: bool = False


class SettingRowWidget(Widget):
    """Renders a single setting row with label, value, description."""

    DEFAULT_CSS = """
    SettingRowWidget { height: auto; padding: 0 0 1 0; }
    SettingRowWidget.selected { background: #27272a; }
    SettingRowWidget .sr-main { height: 1; }
    SettingRowWidget .sr-desc { height: 1; }
    """

    def __init__(
        self,
        row: SettingRow,
        *,
        selected: bool = False,
        editing: bool = False,
        edit_buf: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._row = row
        self._editing = editing
        self._edit_buf = edit_buf
        if selected:
            self.add_class("selected")

    def compose(self) -> ComposeResult:
        r = self._row
        value = self._edit_buf + "█" if self._editing else r.value
        yield Static(
            f"  [{TEXT_SECONDARY}]{r.label + ':':>16}[/]  [bold {TEXT_PRIMARY}]{value}[/]",
            classes="sr-main",
        )
        yield Static(
            f"  {'':>16}  [{TEXT_MUTED}]{r.description}[/]",
            classes="sr-desc",
        )


class SettingsPage(Widget):
    """Settings page with tabbed sections.

    Tabs: Connection, Profile, Integrations, Appearance.

    Keybindings:
        tab/shift+tab   switch section
        j/k             navigate items
        enter           edit (where applicable)
        r               refresh
    """

    DEFAULT_CSS = """
    SettingsPage { width: 1fr; height: 1fr; }
    SettingsPage #settings-tabs { height: auto; }
    SettingsPage #settings-content { height: 1fr; overflow-y: auto; }
    SettingsPage #settings-help { height: 1; padding: 0 2; }
    """

    section: reactive[int] = reactive(0)
    cursor: reactive[int] = reactive(0)
    editing: reactive[bool] = reactive(False)

    def __init__(
        self,
        server_url: str = "",
        token: str = "",
        profile: UserProfile | None = None,
        integrations: list[IntegrationEntry] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._server_url = server_url
        self._token = token
        self._profile = profile
        self._integrations: list[IntegrationEntry] = integrations or []
        self._edit_buf = ""
        self._mounted = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield NiuuTabs(list(SETTINGS_TABS), id="settings-tabs")
            yield Vertical(id="settings-content")
            yield Static(self._help_text(), id="settings-help")

    def on_mount(self) -> None:
        self._mounted = True
        self._rebuild_content()

    # ── Data setters ────────────────────────────────────────

    def set_profile(self, profile: UserProfile) -> None:
        self._profile = profile
        if self.section == 1:
            self._rebuild_content()

    def set_integrations(self, integrations: list[IntegrationEntry]) -> None:
        self._integrations = list(integrations)
        if self.section == 2:
            self._rebuild_content()

    # ── Tab selection ────────────────────────────────────────

    def on_niuu_tabs_tab_selected(self, event: NiuuTabs.TabSelected) -> None:
        self.section = event.index
        self.cursor = 0
        self._rebuild_content()

    def action_next_tab(self) -> None:
        idx = (self.section + 1) % len(SETTINGS_TABS)
        try:
            self.query_one("#settings-tabs", NiuuTabs).select(idx)
        except Exception:
            self.section = idx
            self._rebuild_content()

    def action_prev_tab(self) -> None:
        idx = (self.section - 1) % len(SETTINGS_TABS)
        try:
            self.query_one("#settings-tabs", NiuuTabs).select(idx)
        except Exception:
            self.section = idx
            self._rebuild_content()

    # ── Content rendering ────────────────────────────────────

    def _rebuild_content(self) -> None:
        if not self._mounted:
            return
        try:
            container = self.query_one("#settings-content", Vertical)
        except Exception:
            return
        container.remove_children()

        match self.section:
            case 0:
                self._mount_connection(container)
            case 1:
                self._mount_profile(container)
            case 2:
                self._mount_integrations(container)
            case 3:
                self._mount_appearance(container)

    def _mount_connection(self, container: Vertical) -> None:
        rows = [
            SettingRow(
                "Server URL",
                self._server_url or "(not set)",
                "Volundr API server address",
                editable=True,
            ),
            SettingRow(
                "Auth Token",
                _mask_token(self._token),
                "OIDC Bearer token for API authentication",
            ),
            SettingRow(
                "WebSocket",
                "Auto (derived from server URL)",
                "WebSocket endpoint for real-time features",
            ),
            SettingRow("Timeout", "30s", "HTTP request timeout"),
        ]
        for i, row in enumerate(rows):
            is_editing = self.editing and i == self.cursor
            container.mount(
                SettingRowWidget(
                    row,
                    selected=(i == self.cursor),
                    editing=is_editing,
                    edit_buf=self._edit_buf if is_editing else "",
                )
            )

    def _mount_profile(self, container: Vertical) -> None:
        if not self._profile:
            container.mount(Static(f"[{TEXT_MUTED}]  No profile loaded[/]"))
            return
        p = self._profile
        rows = [
            SettingRow("User ID", p.user_id, "Unique identifier"),
            SettingRow("Display Name", p.display_name, "Your display name"),
            SettingRow("Email", p.email, "Email address"),
            SettingRow("Tenant", p.tenant_id, "Organization / tenant"),
            SettingRow("Roles", ", ".join(p.roles), "Assigned roles"),
            SettingRow("Status", p.status, "Account status"),
        ]
        for i, row in enumerate(rows):
            sel = i == self.cursor
            container.mount(SettingRowWidget(row, selected=sel))

    def _mount_integrations(self, container: Vertical) -> None:
        if not self._integrations:
            container.mount(Static(f"[{TEXT_MUTED}]  No integrations available[/]"))
            return
        for i, entry in enumerate(self._integrations):
            text = (
                f"  {entry.icon} [bold {TEXT_PRIMARY}]{entry.name:16}[/]  "
                f"[{ACCENT_PURPLE}]{entry.integration_type}[/]  "
                f"[{TEXT_MUTED}]{entry.description}[/]"
            )
            classes = "selected" if i == self.cursor else ""
            widget = Static(text)
            container.mount(widget)
            if classes:
                widget.add_class(classes)

    def _mount_appearance(self, container: Vertical) -> None:
        rows = [
            SettingRow("Theme", "dark", "Color scheme (dark is the only option)"),
            SettingRow("Sidebar", "Expanded", "Sidebar display mode (toggle with [)"),
            SettingRow("Timestamps", "Relative", "How to display timestamps"),
            SettingRow("Unicode Icons", "Enabled", "Use unicode icons in navigation"),
        ]
        for i, row in enumerate(rows):
            sel = i == self.cursor
            container.mount(SettingRowWidget(row, selected=sel))

    # ── Cursor ──────────────────────────────────────────────

    def watch_cursor(self) -> None:
        self._rebuild_content()

    def action_cursor_up(self) -> None:
        if self.cursor > 0:
            self.cursor -= 1

    def _max_cursor(self) -> int:
        """Return the maximum valid cursor index for the current section."""
        match self.section:
            case 0:
                return 3  # Connection: 4 rows
            case 1:
                return 5 if self._profile else 0  # Profile: 6 rows
            case 2:
                return max(0, len(self._integrations) - 1)
            case 3:
                return 3  # Appearance: 4 rows
            case _:
                return 0
        raise AssertionError("Unreachable _max_cursor fallthrough")

    def action_cursor_down(self) -> None:
        if self.cursor < self._max_cursor():
            self.cursor += 1

    def action_cursor_top(self) -> None:
        self.cursor = 0

    # ── Editing ──────────────────────────────────────────────

    def action_start_edit(self) -> None:
        """Enter edit mode for the current field if editable."""
        if self.section == 0 and self.cursor == 0:
            self.editing = True
            self._edit_buf = self._server_url
            self._rebuild_content()

    def action_save_edit(self) -> None:
        """Save the edit buffer."""
        if self.editing:
            self._server_url = self._edit_buf
            self.editing = False
            self._rebuild_content()

    def action_cancel_edit(self) -> None:
        """Cancel editing."""
        self.editing = False
        self._edit_buf = ""
        self._rebuild_content()

    def action_refresh(self) -> None:
        self._rebuild_content()

    # ── Help text ────────────────────────────────────────────

    def _help_text(self) -> str:
        if self.editing:
            return f"[{TEXT_MUTED}]  Enter: save  Esc: cancel[/]"
        return (
            f"[{TEXT_MUTED}]  Tab/Shift+Tab: switch section"
            f"  j/k: navigate  Enter: edit  r: refresh[/]"
        )

    def _update_help(self) -> None:
        with suppress(Exception):
            self.query_one("#settings-help", Static).update(self._help_text())

    def watch_editing(self) -> None:
        self._update_help()
