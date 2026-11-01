"""AgentsPage — TUI page for viewing active Ravn agent sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from cli.tui.theme import (
    ACCENT_CYAN,
    ACCENT_EMERALD,
    ACCENT_RED,
    BG_SECONDARY,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

if TYPE_CHECKING:
    from niuu.cli_api_client import CLIAPIClient

_STATUS_COLORS: dict[str, str] = {
    "running": ACCENT_EMERALD,
    "stopped": ACCENT_RED,
    "idle": ACCENT_CYAN,
}


class AgentRow(Widget):
    """A single agent session entry in the list."""

    DEFAULT_CSS = """
    AgentRow {
        height: auto;
        padding: 1 2;
        border-bottom: solid #27272a;
        background: #18181b;
    }
    AgentRow:hover {
        background: #27272a;
    }
    """

    def __init__(self, session: dict[str, Any], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._session = session

    def compose(self) -> ComposeResult:
        session = self._session
        session_id = str(session.get("id", ""))[:8]
        status = session.get("status", "unknown")
        model = session.get("model", "—")
        created_at = session.get("created_at", "—")

        color = _STATUS_COLORS.get(status.lower(), TEXT_MUTED)

        yield Static(
            f"[bold {TEXT_PRIMARY}]{session_id}[/]  "
            f"[{color}]{status}[/]  "
            f"[{TEXT_SECONDARY}]model: {model}[/]  "
            f"[{TEXT_MUTED}]{created_at}[/]",
        )


class AgentsPage(Widget):
    """TUI page for viewing active Ravn agent sessions."""

    DEFAULT_CSS = f"""
    AgentsPage {{
        width: 1fr;
        height: 1fr;
        background: {BG_SECONDARY};
    }}
    AgentsPage #agents-header {{
        margin: 1 2;
        color: {TEXT_MUTED};
    }}
    AgentsPage #agents-list {{
        height: 1fr;
    }}
    """

    def __init__(
        self,
        client: CLIAPIClient | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._sessions: list[dict[str, Any]] = []

    @property
    def sessions(self) -> list[dict[str, Any]]:
        return list(self._sessions)

    def compose(self) -> ComposeResult:
        yield Static(
            f"[{TEXT_MUTED}]Active Ravn agent sessions[/]",
            id="agents-header",
        )
        yield VerticalScroll(id="agents-list")

    def on_mount(self) -> None:
        self._load_sessions()

    def load_data(self, sessions: list[dict[str, Any]]) -> None:
        """Load session data directly (for testing or programmatic use)."""
        self._sessions = sessions
        self._render_list()

    def _load_sessions(self) -> None:
        if not self._client:
            return
        try:
            resp = self._client.get("/api/v1/ravn/sessions")
            resp.raise_for_status()
            self._sessions = resp.json()
        except Exception:
            self._sessions = []
        self._render_list()

    def _render_list(self) -> None:
        try:
            container = self.query_one("#agents-list", VerticalScroll)
        except Exception:
            return
        container.remove_children()

        if not self._sessions:
            container.mount(Static(f"[{TEXT_MUTED}]No active agent sessions.[/]"))
            return

        for session in self._sessions:
            container.mount(AgentRow(session))
