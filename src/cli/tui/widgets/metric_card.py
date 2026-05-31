"""MetricCard widget — bordered box with icon, value, and label."""

from __future__ import annotations

from contextlib import suppress

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Static

from cli.tui.theme import ACCENT_CYAN, TEXT_MUTED

DEFAULT_METRIC_COLOR = ACCENT_CYAN


class MetricCard(Widget):
    """Bordered card displaying icon + value (bold, colored) + label (muted)."""

    DEFAULT_CSS = """
    MetricCard {
        width: 22;
        height: 5;
        border: round #27272a;
        padding: 1 2;
        background: #18181b;
    }
    MetricCard #metric-icon-value {
        height: 1;
    }
    MetricCard #metric-label {
        height: 1;
        color: #71717a;
    }
    """

    def __init__(
        self,
        label: str,
        value: str,
        icon: str = "",
        color: str = DEFAULT_METRIC_COLOR,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._label = label
        self._value = value
        self._icon = icon
        self._color = color

    @property
    def label(self) -> str:
        return self._label

    @property
    def value(self) -> str:
        return self._value

    def compose(self) -> ComposeResult:
        icon_part = f"{self._icon} " if self._icon else ""
        yield Static(
            f"[bold {self._color}]{icon_part}{self._value}[/]",
            id="metric-icon-value",
        )
        yield Static(f"[{TEXT_MUTED}]{self._label}[/]", id="metric-label")

    def set_value(self, value: str) -> None:
        self._value = value
        with suppress(Exception):
            icon_part = f"{self._icon} " if self._icon else ""
            self.query_one("#metric-icon-value", Static).update(
                f"[bold {self._color}]{icon_part}{value}[/]"
            )


class MetricRow(Horizontal):
    """Horizontal layout container for MetricCard widgets."""

    DEFAULT_CSS = """
    MetricRow {
        height: auto;
        width: 1fr;
    }
    """
