"""Diffs page — split view with file tree (left) and diff viewer (right)."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Static

from cli.tui.theme import (
    ACCENT_AMBER,
    ACCENT_CYAN,
    ACCENT_EMERALD,
    ACCENT_RED,
    TEXT_MUTED,
    TEXT_SECONDARY,
)


@dataclass
class DiffFile:
    """Represents a changed file with its diff content."""

    path: str = ""
    status: str = "M"  # M modified, A added, D deleted
    diff: str = ""
    additions: int = 0
    deletions: int = 0


def _status_color(status: str) -> str:
    match status:
        case "M":
            return ACCENT_AMBER
        case "A":
            return ACCENT_EMERALD
        case "D":
            return ACCENT_RED
        case _:
            return TEXT_MUTED
    raise AssertionError("Unreachable _status_color fallthrough")


def _truncate_path(path: str, max_len: int) -> str:
    if len(path) <= max_len:
        return path
    return "..." + path[len(path) - max_len + 3 :]


def _colorize_diff_line(line: str) -> str:
    """Apply syntax coloring to a single diff line."""
    escaped = escape(line)
    if line.startswith("+"):
        return f"[{ACCENT_EMERALD}]{escaped}[/]"
    if line.startswith("-"):
        return f"[{ACCENT_RED}]{escaped}[/]"
    if line.startswith("@@"):
        return f"[{ACCENT_CYAN}]{escaped}[/]"
    return f"[{TEXT_SECONDARY}]{escaped}[/]"


class FileTreeItem(Widget):
    """A single file in the tree sidebar."""

    DEFAULT_CSS = """
    FileTreeItem { height: 1; padding: 0 1; }
    FileTreeItem.selected { background: #27272a; }
    """

    def __init__(self, file: DiffFile, *, selected: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._file = file
        if selected:
            self.add_class("selected")

    def compose(self) -> ComposeResult:
        f = self._file
        color = _status_color(f.status)
        stats = f"[{TEXT_MUTED}]+{f.additions} -{f.deletions}[/]"
        yield Static(
            f"[{color}]{f.status}[/] [{TEXT_SECONDARY}]{_truncate_path(f.path, 28)}[/] {stats}"
        )


class DiffsPage(Widget):
    """Split-pane diff viewer with file tree and syntax-colored diff.

    Keybindings:
        j/k     select file in tree
        J/K     scroll diff content
        G/g     jump to last/first file
        /       search files
        r       refresh
    """

    DEFAULT_CSS = """
    DiffsPage { width: 1fr; height: 1fr; }
    DiffsPage #diffs-header { height: auto; padding: 0 0 1 0; }
    DiffsPage #diffs-stats { height: 1; }
    DiffsPage #diffs-search { height: auto; display: none; }
    DiffsPage #diffs-search.visible { display: block; }
    DiffsPage #diffs-body { height: 1fr; }
    DiffsPage #diffs-tree {
        width: 36; border: round #27272a; overflow-y: auto;
    }
    DiffsPage #diffs-viewer {
        width: 1fr; border: round #27272a; overflow-y: auto; padding: 0 1;
    }
    DiffsPage #diffs-viewer-content { height: auto; }
    DiffsPage #diffs-empty { color: #71717a; padding: 2; }
    """

    cursor: reactive[int] = reactive(0)
    scroll_pos: reactive[int] = reactive(0)
    searching: reactive[bool] = reactive(False)

    def __init__(self, files: list[DiffFile] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._all_files: list[DiffFile] = files or []
        self._filtered: list[DiffFile] = list(self._all_files)
        self._search_term = ""
        self._mounted = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="diffs-stats")
            with Horizontal(id="diffs-search"):
                yield Input(placeholder="Filter files…", id="diffs-search-input")
            with Horizontal(id="diffs-body"):
                yield Vertical(id="diffs-tree")
                yield Vertical(id="diffs-viewer")

    def on_mount(self) -> None:
        self._mounted = True
        self._apply_filter()
        self._update_stats()

    # ── Data ────────────────────────────────────────────────

    def set_files(self, files: list[DiffFile]) -> None:
        """Replace all files and refresh."""
        self._all_files = list(files)
        self._search_term = ""
        self.cursor = 0
        self.scroll_pos = 0
        self._apply_filter()
        self._update_stats()

    # ── Filter ──────────────────────────────────────────────

    def _apply_filter(self) -> None:
        search = self._search_term.lower()
        if not search:
            self._filtered = list(self._all_files)
        else:
            self._filtered = [f for f in self._all_files if search in f.path.lower()]
        if self.cursor >= len(self._filtered):
            self.cursor = max(0, len(self._filtered) - 1)
        self._rebuild_tree()
        self._rebuild_viewer()

    def _update_stats(self) -> None:
        total_add = sum(f.additions for f in self._all_files)
        total_del = sum(f.deletions for f in self._all_files)
        text = (
            f"  [{ACCENT_EMERALD}]+{total_add}[/]  "
            f"[{ACCENT_RED}]-{total_del}[/]  "
            f"[{TEXT_MUTED}]{len(self._all_files)} files changed[/]"
        )
        with suppress(Exception):
            self.query_one("#diffs-stats", Static).update(text)

    # ── Tree ────────────────────────────────────────────────

    def _rebuild_tree(self) -> None:
        if not self._mounted:
            return
        try:
            tree = self.query_one("#diffs-tree", Vertical)
        except Exception:
            return
        tree.remove_children()
        if not self._filtered:
            tree.mount(Static(f"[{TEXT_MUTED}]  No changes[/]"))
            return
        for i, f in enumerate(self._filtered):
            tree.mount(FileTreeItem(f, selected=(i == self.cursor)))

    # ── Diff viewer ─────────────────────────────────────────

    def _rebuild_viewer(self) -> None:
        if not self._mounted:
            return
        try:
            viewer = self.query_one("#diffs-viewer", Vertical)
        except Exception:
            return
        viewer.remove_children()
        if not self._filtered or self.cursor >= len(self._filtered):
            viewer.mount(Static(f"[{TEXT_MUTED}]  No file selected[/]"))
            return
        f = self._filtered[self.cursor]
        if not f.diff:
            viewer.mount(Static(f"[{TEXT_MUTED}]  Loading diff…[/]"))
            return
        lines = f.diff.split("\n")
        start = self.scroll_pos
        end = min(len(lines), start + 50)
        colored = "\n".join(_colorize_diff_line(ln) for ln in lines[start:end])
        viewer.mount(Static(colored))

    # ── Cursor / scroll ──────────────────────────────────────

    def watch_cursor(self) -> None:
        self.scroll_pos = 0
        self._rebuild_tree()
        self._rebuild_viewer()

    def watch_scroll_pos(self) -> None:
        self._rebuild_viewer()

    def action_cursor_up(self) -> None:
        if self.cursor > 0:
            self.cursor -= 1

    def action_cursor_down(self) -> None:
        if self.cursor < len(self._filtered) - 1:
            self.cursor += 1

    def action_cursor_top(self) -> None:
        self.cursor = 0

    def action_cursor_bottom(self) -> None:
        self.cursor = max(0, len(self._filtered) - 1)

    def action_scroll_diff_down(self) -> None:
        self.scroll_pos += 1

    def action_scroll_diff_up(self) -> None:
        if self.scroll_pos > 0:
            self.scroll_pos -= 1

    # ── Search ──────────────────────────────────────────────

    def action_toggle_search(self) -> None:
        self.searching = not self.searching

    def watch_searching(self, value: bool) -> None:
        try:
            box = self.query_one("#diffs-search", Horizontal)
        except Exception:
            return
        if value:
            box.add_class("visible")
            with suppress(Exception):
                self.query_one("#diffs-search-input", Input).focus()
        else:
            box.remove_class("visible")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "diffs-search-input":
            self._search_term = event.value
            self._apply_filter()

    def action_refresh(self) -> None:
        self._apply_filter()
        self._update_stats()
