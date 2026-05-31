"""ChatView — WebSocket conversation with a Ravn daemon.

Uses the /ws WebSocket endpoint with the CLI stream-json format (same as the
web UI's useSkuldChat hook).  Text deltas are streamed live into a Static
widget as they arrive; when the block closes the content is committed as a
chat bubble to the RichLog.
"""

from __future__ import annotations
from contextlib import suppress

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, RichLog, Static

if TYPE_CHECKING:
    pass


class ChatView(Widget):
    """Streams over /ws WebSocket (CLI stream-json format).

    Message protocol (server → client):
      content_block_start  type=text|thinking|tool_use
      content_block_delta  type=text_delta|thinking_delta|input_json_delta
      content_block_stop
      result               subtype=success|error
      error                error.message
    """

    DEFAULT_CSS = """
    ChatView {
        height: 1fr;
        width: 1fr;
        background: #09090b;
        layout: vertical;
    }
    ChatView #cv-log {
        height: 1fr;
        background: #09090b;
    }
    ChatView #cv-stream {
        padding: 0 1;
        background: #1c1c1e;
        color: #d4d4d8;
        display: none;
    }
    ChatView #cv-stream.streaming {
        display: block;
    }
    ChatView #cv-input-bar {
        height: 3;
        border-top: solid #27272a;
        background: #0d0d0f;
        padding: 0 1;
    }
    ChatView #cv-input {
        width: 1fr;
        background: #0d0d0f;
        color: #d4d4d8;
        border: none;
    }
    """

    can_focus = True

    BINDINGS = [
        Binding("j", "scroll_down", show=False),
        Binding("k", "scroll_up", show=False),
        Binding("G", "scroll_bottom", show=False),
        Binding("g", "scroll_top", show=False),
    ]

    streaming: reactive[bool] = reactive(False)

    def __init__(self, connection: Any | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._connection: Any | None = connection
        self._ws: Any | None = None
        # Per-block stream buffers
        self._block_type: str = ""
        self._text_buf: str = ""
        self._think_buf: str = ""

    def compose(self) -> ComposeResult:
        yield RichLog(id="cv-log", markup=True, highlight=True, wrap=True)
        yield Static("", id="cv-stream", markup=True)
        name = self._connection.name if self._connection else "ravn"
        yield Input(placeholder=f"{name} > message…", id="cv-input")

    def on_mount(self) -> None:
        log = self.query_one("#cv-log", RichLog)
        if self._connection:
            log.write(f"[#3f3f46]  ── {self._connection.name} ──[/]")
            log.write("")
            asyncio.create_task(self._connect_ws(), name="chat-ws")
        else:
            log.write("[#3f3f46]  ── no ravn selected ──[/]")
            log.write("")
            log.write("[#52525b]  Select a ravn from the Flokk panel:[/]")
            log.write(
                "[#3f3f46]  Tab[/][#71717a] → focus Flokk  [/]"
                "[#3f3f46]j/k[/][#71717a] → navigate  [/]"
                "[#3f3f46]↵[/][#71717a] → open chat[/]"
            )
            log.write("")
            log.write("[#52525b]  No ravn? Connect one:[/]")
            log.write("[#3f3f46]  :[/][#71717a]connect host:7477[/]")

    # ------------------------------------------------------------------
    # Scroll actions (j/k/G/g when log is in view)
    # ------------------------------------------------------------------

    def action_scroll_down(self) -> None:
        with suppress(Exception):
            self.query_one("#cv-log", RichLog).scroll_down(animate=False)

    def action_scroll_up(self) -> None:
        with suppress(Exception):
            self.query_one("#cv-log", RichLog).scroll_up(animate=False)

    def action_scroll_bottom(self) -> None:
        with suppress(Exception):
            self.query_one("#cv-log", RichLog).scroll_end(animate=False)

    def action_scroll_top(self) -> None:
        with suppress(Exception):
            self.query_one("#cv-log", RichLog).scroll_home(animate=False)

    # ------------------------------------------------------------------
    # WebSocket connection
    # ------------------------------------------------------------------

    async def _connect_ws(self) -> None:
        if not self._connection:
            return
        try:
            import websockets

            async with websockets.connect(self._connection.ws_url) as ws:
                self._ws = ws
                self._append_system("connected")
                async for raw in ws:
                    with suppress(json.JSONDecodeError):
                        frame = json.loads(raw)
                        self._handle_frame(frame)
        except Exception as exc:
            self._append_system(f"[#ef4444]disconnected: {exc}[/]")
        finally:
            self._ws = None
            self.streaming = False
            self._clear_stream()

    # ------------------------------------------------------------------
    # CLI stream-json frame handler
    # ------------------------------------------------------------------

    def _handle_frame(self, frame: dict[str, Any]) -> None:
        ftype = frame.get("type", "")

        match ftype:
            case "content_block_start":
                cb = frame.get("content_block", {})
                self._block_type = cb.get("type", "")
                self._text_buf = ""
                self._think_buf = ""
                if self._block_type == "tool_use":
                    tool_name = cb.get("name", "?")
                    self._append_tool_start(tool_name)
                elif self._block_type == "text":
                    self._show_stream("")

            case "content_block_delta":
                delta = frame.get("delta", {})
                dtype = delta.get("type", "")
                if dtype == "text_delta":
                    self._text_buf += delta.get("text", "")
                    # Stream live into the Static widget
                    self._show_stream(self._text_buf)
                elif dtype == "thinking_delta":
                    self._think_buf += delta.get("thinking", "")

            case "content_block_stop":
                self._clear_stream()
                if self._block_type == "text" and self._text_buf:
                    self._append_ravn(self._text_buf)
                elif self._block_type == "thinking" and self._think_buf:
                    preview = self._think_buf[:80].replace("\n", " ")
                    dots = "…" if len(self._think_buf) > 80 else ""
                    self._append_thought(f"{preview}{dots}")
                self._block_type = ""
                self._text_buf = ""
                self._think_buf = ""

            case "result":
                self.streaming = False
                if frame.get("subtype") == "error" or frame.get("is_error"):
                    self._append_system(f"[#ef4444]{frame.get('result', 'error')}[/]")

            case "error":
                self.streaming = False
                msg = frame.get("error", {}).get("message", "unknown error")
                self._append_system(f"[#ef4444]{msg}[/]")

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        self._append_user(text)
        await self._send(text)

    async def _send(self, text: str) -> None:
        if not self._ws:
            self._append_system("[#71717a](not connected — reconnecting…)[/]")
            if self._connection:
                asyncio.create_task(self._connect_ws(), name="chat-ws-reconnect")
            return
        try:
            await self._ws.send(json.dumps({"type": "user", "content": text}))
            self.streaming = True
        except Exception:
            self._append_system("[#ef4444]send failed[/]")

    # ------------------------------------------------------------------
    # Live stream display
    # ------------------------------------------------------------------

    def _show_stream(self, text: str) -> None:
        with suppress(Exception):
            stream = self.query_one("#cv-stream", Static)
            stream.add_class("streaming")
            # Escape Rich markup in streamed text to avoid parse errors
            safe = text.replace("[", "\\[")
            stream.update(f"[#d4d4d8]  {safe}[/]")

    def _clear_stream(self) -> None:
        with suppress(Exception):
            stream = self.query_one("#cv-stream", Static)
            stream.remove_class("streaming")
            stream.update("")

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    def _append_user(self, text: str) -> None:
        from datetime import datetime

        log = self.query_one("#cv-log", RichLog)
        ts = datetime.now().strftime("%H:%M:%S")
        safe = text.replace("[", "\\[")
        log.write(f"[#d4d4d8 on #1a1308]  {safe}  [/]")
        log.write(f"[#3f3f46]  {ts} · you[/]")
        log.write("")

    def _append_ravn(self, text: str) -> None:
        from datetime import datetime

        try:
            log = self.query_one("#cv-log", RichLog)
        except Exception:
            return
        name = self._connection.name if self._connection else "ravn"
        ts = datetime.now().strftime("%H:%M:%S")
        for line in _render_markdown(text):
            log.write(line)
        log.write(f"[#3f3f46]  {ts} · {name}[/]")
        log.write("")
        log.scroll_end(animate=False)

    def _append_thought(self, text: str) -> None:
        with suppress(Exception):
            log = self.query_one("#cv-log", RichLog)
            safe = text.replace("[", "\\[")
            log.write(f"[italic #52525b]  ✦ {safe}[/]")

    def _append_tool_start(self, tool: str) -> None:
        with suppress(Exception):
            log = self.query_one("#cv-log", RichLog)
            log.write(f"[#06b6d4 on #071a1e]  TOOL [/][#06b6d4] {tool}[/]")

    def _append_system(self, text: str) -> None:
        with suppress(Exception):
            log = self.query_one("#cv-log", RichLog)
            log.write(f"[#3f3f46]  ── {text} ──[/]")


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def _inline(text: str) -> str:
    """Apply inline markdown to already-escape-safe text.

    Assumes `[` has NOT been escaped yet — this function does the escaping
    first, then applies bold/italic/code substitutions.
    """
    text = text.replace("[", "\\[")
    # Inline code (highest priority — must run before bold/italic)
    text = re.sub(
        r"`(.+?)`",
        lambda m: "[#a1a1aa on #111111] " + m.group(1).replace("[", "\\[") + " [/]",
        text,
    )
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"[bold #fafafa]\1[/]", text)
    text = re.sub(r"__(.+?)__", r"[bold #fafafa]\1[/]", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"[italic #d4d4d8]\1[/]", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"[italic #d4d4d8]\1[/]", text)
    return text


def _render_markdown(text: str) -> list[str]:
    """Convert a markdown response to a list of Rich markup strings."""
    output: list[str] = []
    in_code_block = False
    code_lang = ""

    for raw in text.split("\n"):
        # Code fence toggle
        if raw.startswith("```"):
            if in_code_block:
                in_code_block = False
                output.append("[#3f3f46]  ───[/]")
            else:
                in_code_block = True
                code_lang = raw[3:].strip()
                label = f" {code_lang}" if code_lang else ""
                output.append(f"[#3f3f46]  ──{label}[/]")
            continue

        if in_code_block:
            safe = raw.replace("[", "\\[")
            output.append(f"  [#a1a1aa on #111111]{safe}[/]")
            continue

        # Headings
        if raw.startswith("### "):
            output.append(f"[bold #a1a1aa]{_inline(raw[4:])}[/]")
        elif raw.startswith("## "):
            output.append(f"[bold #d4d4d8]{_inline(raw[3:])}[/]")
        elif raw.startswith("# "):
            output.append(f"[bold #fafafa]{_inline(raw[2:])}[/]")
        # Unordered list
        elif raw.startswith(("- ", "* ")):
            output.append(f"  [#52525b]·[/] [#d4d4d8]{_inline(raw[2:])}[/]")
        # Ordered list
        elif re.match(r"^\d+\. ", raw):
            content = re.sub(r"^\d+\. ", "", raw)
            num = re.match(r"^(\d+)\. ", raw).group(1)  # type: ignore[union-attr]
            output.append(f"  [#52525b]{num}.[/] [#d4d4d8]{_inline(content)}[/]")
        # Blockquote
        elif raw.startswith("> "):
            output.append(f"  [italic #71717a]{_inline(raw[2:])}[/]")
        # Horizontal rule
        elif raw.strip() in ("---", "***", "___"):
            output.append("[#3f3f46]  ─────────────────────────────────────[/]")
        # Normal text
        elif raw:
            output.append(f"[#d4d4d8]{_inline(raw)}[/]")
        else:
            output.append("")

    return output
