"""RavnTUI — main Textual application for the Ravn terminal operator interface."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path
from typing import Any

import textual.app as textual_app
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Label

from ravn.tui.commands import CommandDispatcher, CommandParseError, parse_command
from ravn.tui.connections import FlokkManager
from ravn.tui.keybindings import KeybindingLoader, KeybindingMap, KeySequenceBuffer
from ravn.tui.layouts import LayoutManager
from ravn.tui.widgets.bottom_bar import BottomBar
from ravn.tui.widgets.pane import PaneWidget
from ravn.tui.widgets.split import PaneNode, SplitNode, SplitTree, TreeNode
from ravn.tui.widgets.status_bar import StatusBar
from ravn.tui.widgets.tab_bar import TabBar

logger = logging.getLogger(__name__)
TextualApp = textual_app.App

_APP_TITLE = "ᚠ Ravn"
_RESIZE_STEP = 0.05

# Textual uses verbose names for some keys; normalise to the short form
# the keybinding map uses.
_KEY_ALIASES: dict[str, str] = {
    "less_than_sign": "<",
    "greater_than_sign": ">",
    "plus": "+",
    "minus": "-",
    "equal_sign": "=",
    "slash": "/",
    "colon": ":",
    "semicolon": ";",
    "question_mark": "?",
}

# Keys already handled by Textual BINDINGS or by individual view widgets —
# do NOT dispatch these from on_key's single-key handler.
# NOTE: "q" is intentionally absent — it is handled entirely in on_key so that
# the Textual BINDING mechanism cannot fire action_quit_guard mid-sequence.
_BINDINGS_KEYS: frozenset[str] = frozenset({":", "b", "n", "escape"})
_VIEW_NAV_KEYS: frozenset[str] = frozenset({"j", "k", "G", "/"})


def _normalise_key(key: str) -> str:
    return _KEY_ALIASES.get(key, key)


class RavnTUI(TextualApp[None]):
    """Ravn TUI — terminal operator interface for Flokk management.

    Supports vim/tmux-style arbitrary split layout, 8 view types,
    command mode, named layouts, broadcast, ghost mode, and live
    observability over WebSocket + SSE.
    """

    CSS_PATH = Path(__file__).parent / "app.tcss"

    BINDINGS = [
        Binding(":", "command_mode", "Command"),
        Binding("b", "broadcast", "Broadcast"),
        Binding("n", "notifications", "Notifs"),
        Binding("escape", "escape", "Escape", show=False),
    ]

    TITLE = _APP_TITLE

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    class GhostMode(Message):
        def __init__(self, host: str, port: int) -> None:
            super().__init__()
            self.host = host
            self.port = port

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def __init__(
        self,
        connections: list[tuple[str, int]] | None = None,
        discover: bool = False,
        layout_name: str | None = None,
        mimir_urls: list[tuple[str, str]] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._initial_connections = connections or []
        self._discover = discover
        self._initial_layout = layout_name
        self.mimir_urls: list[tuple[str, str]] = mimir_urls or []

        self.flokk = FlokkManager()
        self.layout_mgr = LayoutManager()
        self._tree = SplitTree()
        self._focused_pane_id: str | None = None
        self._zoomed: bool = False
        self._zoom_previous_tree: SplitTree | None = None
        self._notif_log: list[str] = []
        self._insert_mode: bool = False
        self._cmd_dispatcher = CommandDispatcher()
        self._register_commands()

        # Load keybindings from editor config (default: vim)
        self._kb_map: KeybindingMap = KeybindingLoader().load()
        self._kb_seq: KeySequenceBuffer = KeySequenceBuffer()
        self._init_sequence_buffer()

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield StatusBar(flokk=self.flokk, id="status-bar")
        yield TabBar(id="tab-bar")
        yield Container(id="main-container")
        yield Container(
            Label("[bold #f59e0b]:[/]", id="cmd-input-prompt"),
            Input(placeholder="command", id="cmd-input"),
            id="cmd-input-container",
        )
        yield BottomBar(id="bottom-bar")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_mount(self) -> None:
        # Load initial layout
        layout_name = self._initial_layout or "flokk"
        layout_data = self.layout_mgr.load(layout_name)
        if layout_data:
            self._tree = SplitTree.from_dict(layout_data)
        else:
            self._tree = SplitTree()

        # Connect to initial Ravens
        for host, port in self._initial_connections:
            asyncio.create_task(
                self.flokk.connect(host, port),
                name=f"connect:{host}:{port}",
            )

        if self._discover:
            asyncio.create_task(self._mdns_discover(), name="mdns-discover")

        await self._render_tree()

        # Focus the first pane
        panes = self._tree.all_panes()
        if panes:
            self._focused_pane_id = panes[0].pane_id
            self._focus_pane(panes[0].pane_id)

    async def _mdns_discover(self) -> None:
        """Attempt mDNS service discovery for Ravn daemons."""
        try:
            from zeroconf import ServiceBrowser
            from zeroconf.asyncio import AsyncZeroconf

            async with AsyncZeroconf() as azc:

                def on_service_added(zeroconf: Any, service_type: str, name: str) -> None:
                    info = zeroconf.get_service_info(service_type, name)
                    if info:
                        host = info.server or info.parsed_addresses()[0]
                        port = info.port or 7477
                        asyncio.create_task(self.flokk.connect(host, port))

                ServiceBrowser(
                    azc.zeroconf,
                    "_ravn._tcp.local.",
                    handlers=[on_service_added],
                )
                await asyncio.sleep(float("inf"))
        except ImportError:
            logger.debug("zeroconf not available — mDNS discovery disabled")
        except Exception:
            logger.debug("mDNS discovery failed")

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    async def _render_tree(self) -> None:
        container = self.query_one("#main-container", Container)
        await container.remove_children()
        widget = self._build_widget(self._tree.root)
        await container.mount(widget)

    def _build_widget(self, node: TreeNode) -> Widget:
        if node.is_leaf:
            assert isinstance(node, PaneNode)
            pane = PaneWidget(
                pane_id=node.pane_id,
                view_type=node.view_type,
                target=node.target,
                flokk=self.flokk,
                mimir_urls=self.mimir_urls,
                id=f"pane-{node.pane_id.replace('-', '_')}",
            )
            return pane

        assert isinstance(node, SplitNode)
        css_class = "split-h" if node.direction == "horizontal" else "split-v"
        left_widget = self._build_widget(node.left)
        right_widget = self._build_widget(node.right)

        # Apply ratio as CSS width/height fraction
        if node.direction == "horizontal":
            left_widget.styles.width = f"{int(node.ratio * 100)}%"
            right_widget.styles.width = f"{int((1 - node.ratio) * 100)}%"
        else:
            left_widget.styles.height = f"{int(node.ratio * 100)}%"
            right_widget.styles.height = f"{int((1 - node.ratio) * 100)}%"

        return Container(left_widget, right_widget, classes=css_class)

    # ------------------------------------------------------------------
    # Pane focus management
    # ------------------------------------------------------------------

    def _focus_pane(self, pane_id: str) -> None:
        from ravn.tui.widgets.pane import PaneHeader

        # Remove amber border from previous pane
        if self._focused_pane_id and self._focused_pane_id != pane_id:
            with suppress(Exception):
                prev = self.query_one(
                    f"#pane-{self._focused_pane_id.replace('-', '_')}", PaneWidget
                )
                prev.focused_pane = False

        self._focused_pane_id = pane_id
        with suppress(Exception):
            pane = self.query_one(f"#pane-{pane_id.replace('-', '_')}", PaneWidget)
            pane.focused_pane = True
            # Focus the view widget inside (first non-header child)
            view = next(
                (c for c in pane.children if not isinstance(c, PaneHeader)),
                None,
            )
            if view is not None:
                view.focus()
            # Update contextual bars
            self.query_one("#bottom-bar", BottomBar).set_context(pane._view_type)
            self.query_one("#status-bar", StatusBar).set_active_ravn(pane._target or "—")

    # ------------------------------------------------------------------
    # Keybinding sequence dispatch
    # ------------------------------------------------------------------

    def _enter_insert_mode(self) -> None:
        """Focus the Input in the current pane and enter INSERT mode."""
        if not self._focused_pane_id:
            return
        with suppress(Exception):
            from textual.widgets import Input as _Input

            pane = self.query_one(f"#pane-{self._focused_pane_id.replace('-', '_')}", PaneWidget)
            inp = next((c for c in pane.query(_Input)), None)
            if inp is None:
                return
            inp.focus()
            self._insert_mode = True
            self.query_one("#status-bar", StatusBar).set_mode("INSERT")

    def _exit_insert_mode(self) -> None:
        """Return to NORMAL mode, restoring pane focus."""
        self._insert_mode = False
        with suppress(Exception):
            self.query_one("#status-bar", StatusBar).set_mode("NORMAL")
        if self._focused_pane_id:
            self._focus_pane(self._focused_pane_id)

    def _init_sequence_buffer(self) -> None:
        """Register all multi-key sequences from the loaded keybinding map."""
        for seq, action in self._kb_map.multi_key:
            self._kb_seq.register(seq, action)

    async def on_key(self, event: Any) -> None:
        """Intercept keys for multi-key sequence matching before Textual dispatch."""
        from textual.widgets import Input as _Input

        in_input = isinstance(self.focused, _Input)
        key = _normalise_key(event.key)

        # Multi-key sequences always run (even in inputs) so ^w v/s/q work from chat bar.
        action, consumed = self._kb_seq.handle(key)
        if consumed:
            event.stop()
            if in_input:
                event.prevent_default()
        if action:
            await self._dispatch_kb_action(action)
            return

        # In INSERT mode Esc exits; all other keys pass through to the focused widget.
        if self._insert_mode:
            if key == "escape":
                event.stop()
                self._exit_insert_mode()
            return

        if in_input:
            return

        # "q" is handled here (not via BINDING) so ^w q sequences are never
        # intercepted by a quit BINDING before on_key sees the second key.
        if not self._kb_seq.pending and key == "q":
            event.stop()
            self.exit()
            return

        if not self._kb_seq.pending and key not in _BINDINGS_KEYS and key not in _VIEW_NAV_KEYS:
            single_action = self._kb_map.single_key.get(key)
            if single_action:
                event.stop()
                await self._dispatch_kb_action(single_action)

    async def _dispatch_kb_action(self, action: str) -> None:
        """Dispatch a TUI action name to the correct handler."""
        match action:
            case "split_vert":
                await self._split_current("vertical")
            case "split_horiz":
                await self._split_current("horizontal")
            case "close_pane":
                await self._close_current()
            case "focus_next":
                self._focus_next_pane()
            case "focus_left":
                self._focus_direction("left")
            case "focus_down":
                self._focus_direction("down")
            case "focus_up":
                self._focus_direction("up")
            case "focus_right":
                self._focus_direction("right")
            case "move_far_left":
                await self._move_to_edge("left")
            case "move_far_down":
                await self._move_to_edge("bottom")
            case "move_far_up":
                await self._move_to_edge("top")
            case "move_far_right":
                await self._move_to_edge("right")
            case "swap_pane":
                await self._swap_current()
            case "equalise_panes":
                self._tree.equalise()
                await self._render_tree()
            case "zoom_pane":
                await self._toggle_zoom()
            case "resize_left":
                await self._resize_current(-_RESIZE_STEP)
            case "resize_right":
                await self._resize_current(_RESIZE_STEP)
            case "resize_up":
                await self._resize_current_height(_RESIZE_STEP)
            case "resize_down":
                await self._resize_current_height(-_RESIZE_STEP)
            case "rotate_pane":
                await self._rotate_current()
            case "scroll_top":
                self._scroll_focused("top")
            case "scroll_bottom":
                self._scroll_focused("bottom")
            case "layout_flokk":
                await self._switch_layout("flokk")
            case "layout_cascade":
                await self._switch_layout("cascade")
            case "layout_mimir":
                await self._switch_layout("mimir")
            # Assign current pane to a view type (f/e/m/t keys)
            case "view_flokk":
                await self._cmd_view("flokk")
            case "view_events":
                await self._cmd_view("events")
            case "view_mimir":
                await self._cmd_view("mimir")
            case "view_tasks":
                await self._cmd_view("tasks")
            case "command_mode":
                await self.action_command_mode()
            case "command_palette":
                await self.action_command_palette()
            case "broadcast":
                await self.action_broadcast()
            case "notifications":
                await self.action_notifications()
            case "insert_mode":
                self._enter_insert_mode()
            case "quit":
                self.exit()

    def _scroll_focused(self, direction: str) -> None:
        """Send a scroll action to the currently focused pane's view."""
        if not self._focused_pane_id:
            return
        with suppress(Exception):
            pane = self.query_one(f"#pane-{self._focused_pane_id.replace('-', '_')}", PaneWidget)
            if direction == "top":
                pane.scroll_home(animate=False)
            else:
                pane.scroll_end(animate=False)

    async def _split_current(self, direction: str) -> None:
        if not self._focused_pane_id:
            return
        if direction == "vertical":
            new_id = self._tree.split_vertical(self._focused_pane_id)
        else:
            new_id = self._tree.split_horizontal(self._focused_pane_id)
        await self._render_tree()
        self._focus_pane(new_id)

    async def _close_current(self) -> None:
        if not self._focused_pane_id:
            return
        panes = self._tree.all_panes()
        if len(panes) <= 1:
            self.notify("Only one pane — use ^w v or ^w s to split first")
            return
        new_focus = self._tree.next_pane(self._focused_pane_id)
        self._tree.close_pane(self._focused_pane_id)
        await self._render_tree()
        if new_focus:
            self._focus_pane(new_focus.pane_id)

    def _focus_next_pane(self) -> None:
        if not self._focused_pane_id:
            return
        pane = self._tree.next_pane(self._focused_pane_id)
        if pane:
            self._focus_pane(pane.pane_id)

    def _focus_direction(self, direction: str) -> None:
        if not self._focused_pane_id:
            return
        pane = self._tree.pane_in_direction(self._focused_pane_id, direction)  # type: ignore[arg-type]
        if pane and pane.pane_id != self._focused_pane_id:
            self._focus_pane(pane.pane_id)

    async def _move_to_edge(self, edge: str) -> None:
        if not self._focused_pane_id:
            return
        self._tree.move_to_edge(self._focused_pane_id, edge)  # type: ignore[arg-type]
        await self._render_tree()
        self._focus_pane(self._focused_pane_id)

    async def _swap_current(self) -> None:
        if not self._focused_pane_id:
            return
        self._tree.swap(self._focused_pane_id)
        await self._render_tree()
        self._focus_pane(self._focused_pane_id)

    async def _toggle_zoom(self) -> None:
        if not self._focused_pane_id:
            return
        if self._zoomed:
            if self._zoom_previous_tree:
                self._tree = self._zoom_previous_tree
                self._zoom_previous_tree = None
            self._zoomed = False
            await self._render_tree()
            self._focus_pane(self._focused_pane_id)
            return
        # Save tree, create single-pane tree with current pane
        leaf = self._tree.find_pane(self._focused_pane_id)
        if not leaf:
            return
        self._zoom_previous_tree = SplitTree.from_dict(self._tree.to_dict())
        self._tree = SplitTree(
            PaneNode(
                pane_id=leaf.pane_id,
                view_type=leaf.view_type,
                target=leaf.target,
            )
        )
        self._zoomed = True
        await self._render_tree()
        self._focus_pane(self._focused_pane_id)

    async def _resize_current(self, delta: float) -> None:
        if not self._focused_pane_id:
            return
        self._tree.resize(self._focused_pane_id, delta)
        await self._render_tree()
        self._focus_pane(self._focused_pane_id)

    async def _resize_current_height(self, delta: float) -> None:
        await self._resize_current(delta)

    async def _rotate_current(self) -> None:
        if not self._focused_pane_id:
            return
        self._tree.rotate(self._focused_pane_id)
        await self._render_tree()
        self._focus_pane(self._focused_pane_id)

    # ------------------------------------------------------------------
    # Tab bar
    # ------------------------------------------------------------------

    async def on_tab_bar_tab_changed(self, msg: TabBar.TabChanged) -> None:
        tab_id = msg.tab_id
        if tab_id == "split":
            # "Split" tab = current custom layout, nothing to load
            return
        data = self.layout_mgr.load(tab_id)
        if data:
            self._tree = SplitTree.from_dict(data)
            await self._render_tree()
            panes = self._tree.all_panes()
            if panes:
                self._focus_pane(panes[0].pane_id)

    async def action_layout_flokk(self) -> None:
        await self._switch_layout("flokk")

    async def action_layout_cascade(self) -> None:
        await self._switch_layout("cascade")

    async def action_layout_mimir(self) -> None:
        await self._switch_layout("mimir")

    def reload_keybindings(self, source: str | None = None) -> None:
        """Hot-reload keybindings from editor config.

        Called at runtime if the user changes their tui.yaml and wants
        the TUI to pick up the new bindings without restarting.
        """
        self._kb_map = KeybindingLoader().load(source)
        self._kb_seq = KeySequenceBuffer()
        self._init_sequence_buffer()
        n_single = len(self._kb_map.single_key)
        n_seq = len(self._kb_map.multi_key)
        self.notify(f"Keybindings reloaded ({n_single} single, {n_seq} sequences)")

    async def _switch_layout(self, name: str) -> None:
        data = self.layout_mgr.load(name)
        if not data:
            return
        self._tree = SplitTree.from_dict(data)
        await self._render_tree()
        panes = self._tree.all_panes()
        if panes:
            self._focus_pane(panes[0].pane_id)
        with suppress(Exception):
            self.query_one(TabBar).set_active(name)

    # ------------------------------------------------------------------
    # Command mode
    # ------------------------------------------------------------------

    async def action_command_mode(self) -> None:
        container = self.query_one("#cmd-input-container", Container)
        container.add_class("visible")
        self.query_one("#cmd-input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "cmd-input":
            return
        text = event.value.strip()
        event.input.clear()
        self.query_one("#cmd-input-container", Container).remove_class("visible")
        self.restore_focus()

        if not text:
            return
        raw = text.lstrip(":")
        try:
            cmd = parse_command(raw)
            await self._cmd_dispatcher.dispatch(cmd)
        except CommandParseError as exc:
            self.notify(str(exc), severity="error")

    def restore_focus(self) -> None:
        if self._focused_pane_id:
            self._focus_pane(self._focused_pane_id)

    async def action_escape(self) -> None:
        if self._insert_mode:
            self._exit_insert_mode()
            return
        container = self.query_one("#cmd-input-container", Container)
        if "visible" in container.classes:
            container.remove_class("visible")
            self.restore_focus()

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _register_commands(self) -> None:
        d = self._cmd_dispatcher
        d.register("connect", self._cmd_connect)
        d.register("disconnect", self._cmd_disconnect)
        d.register("view", self._cmd_view)
        d.register("layout", self._cmd_layout)
        d.register("broadcast", self._cmd_broadcast)
        d.register("ingest", self._cmd_ingest)
        d.register("checkpoint", self._cmd_checkpoint)
        d.register("resume", self._cmd_resume)
        d.register("keybindings", self._cmd_keybindings)
        d.register("quit", self._cmd_quit)
        d.register("q", self._cmd_quit)

    async def _cmd_connect(self, host_port: str = "") -> None:
        if ":" not in host_port:
            self.notify("Usage: connect host:port", severity="error")
            return
        host, port_str = host_port.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            self.notify(f"Invalid port: {port_str}", severity="error")
            return
        await self.flokk.connect(host, port)
        self.notify(f"Connecting to {host}:{port}")

    async def _cmd_disconnect(self, name: str = "") -> None:
        await self.flokk.disconnect(name)
        self.notify(f"Disconnected {name}")

    async def _cmd_view(self, view_type: str = "flokk", target: str | None = None) -> None:
        if not self._focused_pane_id:
            return
        self._tree.set_view(self._focused_pane_id, view_type, target)
        try:
            pane_id = self._focused_pane_id
            pane = self.query_one(f"#pane-{pane_id.replace('-', '_')}", PaneWidget)
            pane.assign_view(view_type, target, self.flokk)
            self.query_one("#bottom-bar", BottomBar).set_context(view_type)
        except Exception:
            await self._render_tree()

    async def _cmd_layout(self, subcommand: str = "list", name: str = "") -> None:
        match subcommand:
            case "save":
                if not name:
                    self.notify("Usage: layout save <name>", severity="error")
                    return
                self.layout_mgr.save(name, self._tree.to_dict())
                self.notify(f"Layout saved: {name}")
            case "load":
                if not name:
                    self.notify("Usage: layout load <name>", severity="error")
                    return
                data = self.layout_mgr.load(name)
                if not data:
                    self.notify(f"Layout not found: {name}", severity="error")
                    return
                self._tree = SplitTree.from_dict(data)
                await self._render_tree()
                panes = self._tree.all_panes()
                if panes:
                    self._focus_pane(panes[0].pane_id)
                with suppress(Exception):
                    self.query_one(TabBar).set_active(name)
                self.notify(f"Layout loaded: {name}")
            case "delete":
                if not name:
                    self.notify("Usage: layout delete <name>", severity="error")
                    return
                if not self.layout_mgr.delete(name):
                    self.notify(f"Layout not found: {name}", severity="error")
                    return
                self.notify(f"Layout deleted: {name}")
            case "list":
                names = ", ".join(self.layout_mgr.list())
                self.notify(f"Layouts: {names}")


    async def _cmd_broadcast(self, *args: str) -> None:
        message = " ".join(args)
        if not message:
            self.notify("Usage: broadcast <message>", severity="error")
            return
        results = await self.flokk.broadcast(message)
        self.notify(f"Broadcast to {len(results)} Ravens")

    async def action_broadcast(self) -> None:
        from ravn.tui.widgets.broadcast_overlay import BroadcastOverlay

        await self.push_screen(BroadcastOverlay(self.flokk))

    async def action_command_palette(self) -> None:
        from ravn.tui.widgets.command_palette import CommandPaletteScreen

        def _on_dismiss(cmd: str | None) -> None:
            if not cmd:
                return
            asyncio.create_task(self._handle_palette_result(cmd))

        self.push_screen(CommandPaletteScreen(), _on_dismiss)

    async def _handle_palette_result(self, cmd: str) -> None:
        text = cmd.lstrip(":")
        if cmd.endswith(" "):
            # Command needs arguments — pre-fill the command bar
            container = self.query_one("#cmd-input-container", Container)
            container.add_class("visible")
            inp = self.query_one("#cmd-input", Input)
            inp.value = text
            inp.focus()
        else:
            # Execute directly
            try:
                parsed = parse_command(text)
                await self._cmd_dispatcher.dispatch(parsed)
            except CommandParseError as exc:
                self.notify(str(exc), severity="error")

    async def action_notifications(self) -> None:
        if self._notif_log:
            self.notify("\n".join(self._notif_log[-5:]))
        else:
            self.notify("No notifications")


    async def _cmd_ingest(self, path: str = "") -> None:
        import os

        if not path:
            self.notify("Usage: ingest <filepath>", severity="error")
            return
        if not self.mimir_urls:
            self.notify("No mimir HTTP instances configured", severity="error")
            return
        filepath = os.path.expanduser(path)
        if not os.path.isfile(filepath):
            self.notify(f"File not found: {filepath}", severity="error")
            return
        try:
            import httpx

            with open(filepath, encoding="utf-8", errors="replace") as f:
                content = f.read()
            title = os.path.basename(filepath)
            name, base_url = self.mimir_urls[0]
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{base_url}/mimir/ingest",
                    json={"title": title, "content": content, "source_type": "document"},
                )
            if resp.status_code == 200:
                data = resp.json()
                pages = len(data.get("pages_updated", []))
                self.notify(f"Ingested '{title}' into {name} → {pages} pages updated")
            else:
                self.notify(f"Ingest failed: HTTP {resp.status_code}", severity="error")
        except Exception as exc:
            self.notify(f"Ingest error: {exc}", severity="error")

    async def _cmd_checkpoint(self, label: str = "") -> None:
        self.notify(f"Checkpoint: {label}")

    async def _cmd_resume(self, task_id: str = "") -> None:
        self.notify(f"Resume: {task_id}")

    async def _cmd_keybindings(self, subcommand: str = "show", source: str = "") -> None:
        match subcommand:
            case "reload":
                self.reload_keybindings(source or None)
            case "show":
                lines = [f"{k} → {a}" for k, a in sorted(self._kb_map.single_key.items())]
                lines += [f"{'+'.join(seq)} → {a}" for seq, a in self._kb_map.multi_key]
                self.notify("\n".join(lines[:20]))
            case _:
                self.notify("Usage: keybindings [show|reload] [source]")

    async def _cmd_quit(self) -> None:
        self.exit()

    # ------------------------------------------------------------------
    # Ravn selection from FlokkView
    # ------------------------------------------------------------------

    def on_flokk_view_ravn_selected(self, msg: Any) -> None:
        """Wire a selected ravn to the nearest chat pane."""
        conn = msg.conn
        panes = self._tree.all_panes()

        # Prefer an existing chat pane; fall back to first non-flokk pane
        target_pane = next((p for p in panes if p.view_type == "chat"), None)
        if target_pane is None:
            target_pane = next((p for p in panes if p.view_type != "flokk"), None)
        if target_pane is None:
            return

        with suppress(Exception):
            widget = self.query_one(f"#pane-{target_pane.pane_id.replace('-', '_')}", PaneWidget)
            widget.assign_view("chat", conn.name, self.flokk)
            self._focus_pane(target_pane.pane_id)

    # ------------------------------------------------------------------
    # Ghost mode
    # ------------------------------------------------------------------

    async def on_ravn_t_u_i_ghost_mode(self, message: GhostMode) -> None:
        conn = await self.flokk.connect(message.host, message.port, ghost=True)
        self.notify(f"Ghost mode: {conn.name}")
