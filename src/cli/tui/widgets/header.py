"""Header widget — top bar with hammer icon, title, mode badge, and connection status."""

from __future__ import annotations

from enum import StrEnum

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from cli.tui.mode import MODE_COLORS_HEX, InputMode
from cli.tui.theme import ACCENT_AMBER, ACCENT_EMERALD, ACCENT_RED


class ConnectionState(StrEnum):
    """Server connection state shown in the header."""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


_CONNECTION_INDICATORS: dict[ConnectionState, tuple[str, str]] = {
    ConnectionState.CONNECTING: ("◌", ACCENT_AMBER),
    ConnectionState.CONNECTED: ("●", ACCENT_EMERALD),
    ConnectionState.DISCONNECTED: ("●", ACCENT_RED),
}


class NiuuHeader(Widget):
    """Top bar: hammer icon + 'Niuu' + mode badge + connection dot + server URL."""

    DEFAULT_CSS = """
    NiuuHeader {
        dock: top;
        height: 1;
        background: #18181b;
        color: #fafafa;
    }
    NiuuHeader .header-content {
        width: 1fr;
        height: 1;
    }
    """

    mode: reactive[InputMode] = reactive(InputMode.NORMAL)
    connection: reactive[ConnectionState] = reactive(ConnectionState.CONNECTING)
    server_url: reactive[str] = reactive("")

    def __init__(
        self,
        server_url: str = "",
        *,
        mode: InputMode = InputMode.NORMAL,
        connection: ConnectionState = ConnectionState.CONNECTING,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.mode = mode
        self.connection = connection
        self.server_url = server_url

    def compose(self) -> ComposeResult:
        yield Static(self._render_bar(), id="header-bar", classes="header-content")

    def _render_bar(self) -> str:
        mode_color = MODE_COLORS_HEX[self.mode]
        mode_label = f"[bold {mode_color}] {self.mode} [/]"

        dot_char, dot_color = _CONNECTION_INDICATORS[self.connection]
        status = f"[{dot_color}]{dot_char}[/]"

        url_display = self.server_url.removeprefix("https://").removeprefix("http://")
        url_part = f" [{dot_color}]{url_display}[/]" if url_display else ""

        return f"[bold {ACCENT_AMBER}]⚒[/] [bold]Niuu[/] {mode_label}  {status}{url_part}"

    def watch_mode(self) -> None:
        self._refresh_bar()

    def watch_connection(self) -> None:
        self._refresh_bar()

    def watch_server_url(self) -> None:
        self._refresh_bar()

    def _refresh_bar(self) -> None:
        try:
            bar = self.query_one("#header-bar", Static)
        except Exception:
            return
        bar.update(self._render_bar())
