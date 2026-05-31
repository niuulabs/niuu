"""Chat page — WebSocket streaming messages with markdown and mention menus."""

from __future__ import annotations
from contextlib import suppress

from dataclasses import dataclass
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Static

from cli.tui.theme import (
    ACCENT_AMBER,
    ACCENT_CYAN,
    ACCENT_PURPLE,
    ACCENT_RED,
    TEXT_MUTED,
)
from cli.tui.widgets.mention_menu import MentionItem, MentionMenu
from volundr.tui._utils import format_count

SLASH_COMMANDS: list[MentionItem] = [
    MentionItem(label="help", value="/help ", detail="Show help", icon="▶", category="command"),
    MentionItem(label="clear", value="/clear ", detail="Clear chat", icon="▶", category="command"),
    MentionItem(
        label="reset",
        value="/reset ",
        detail="Reset session",
        icon="▶",
        category="command",
    ),
    MentionItem(
        label="status",
        value="/status ",
        detail="Session status",
        icon="▶",
        category="command",
    ),
    MentionItem(
        label="diff",
        value="/diff ",
        detail="Show changes",
        icon="▶",
        category="command",
    ),
    MentionItem(
        label="commit",
        value="/commit ",
        detail="Commit changes",
        icon="⚡",
        category="skill",
    ),
    MentionItem(
        label="review",
        value="/review ",
        detail="Code review",
        icon="⚡",
        category="skill",
    ),
    MentionItem(label="test", value="/test ", detail="Run tests", icon="⚡", category="skill"),
]


@dataclass
class ChatMessage:
    """A single chat message."""

    role: str = "user"  # user, assistant, system
    content: str = ""
    thinking: bool = False
    status: str = "complete"  # running, complete, error
    tokens: int = 0
    cost: float = 0.0


class MessageBubble(Widget):
    """Renders a single chat message bubble."""

    DEFAULT_CSS = """
    MessageBubble { height: auto; padding: 0 1; margin: 0 0 1 0; }
    MessageBubble .mb-role { height: 1; }
    MessageBubble .mb-content { height: auto; padding: 0 2; }
    MessageBubble .mb-thinking { color: #f59e0b; }
    """

    def __init__(self, message: ChatMessage, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._message = message

    def compose(self) -> ComposeResult:
        m = self._message
        role_color = _role_color(m.role)
        role_icon = _role_icon(m.role)
        status_indicator = ""
        if m.status == "running":
            status_indicator = f" [{ACCENT_AMBER}]⟳[/]"
        elif m.status == "error":
            status_indicator = f" [{ACCENT_RED}]✗[/]"

        yield Static(
            f"[bold {role_color}]{role_icon} {m.role}[/]{status_indicator}",
            classes="mb-role",
        )
        if m.thinking:
            yield Static(f"[{ACCENT_AMBER}]💭 thinking…[/]", classes="mb-thinking")
        yield Static(m.content, classes="mb-content", markup=False)


def _role_color(role: str) -> str:
    match role:
        case "user":
            return ACCENT_CYAN
        case "assistant":
            return ACCENT_PURPLE
        case "system":
            return ACCENT_AMBER
        case _:
            return TEXT_MUTED
    raise AssertionError("Unreachable _role_color fallthrough")


def _role_icon(role: str) -> str:
    match role:
        case "user":
            return "◆"
        case "assistant":
            return "◈"
        case "system":
            return "◉"
        case _:
            return "○"
    raise AssertionError("Unreachable _role_icon fallthrough")


class ChatPage(Widget):
    """Chat page with WebSocket streaming, markdown, and mention menus.

    Keybindings:
        i       enter insert mode (focus input)
        Esc     exit insert mode
        j/k     scroll up/down in normal mode
        G/g     jump to bottom/top
    """

    DEFAULT_CSS = """
    ChatPage { width: 1fr; height: 1fr; }
    ChatPage #chat-messages { height: 1fr; }
    ChatPage #chat-model-bar { height: 1; background: #18181b; padding: 0 2; }
    ChatPage #chat-input-row { height: auto; padding: 1 0; }
    ChatPage #chat-input { width: 1fr; }
    ChatPage #chat-metrics { height: 1; padding: 0 2; }
    ChatPage #chat-mention-file { display: none; }
    ChatPage #chat-mention-file.active { display: block; }
    ChatPage #chat-mention-cmd { display: none; }
    ChatPage #chat-mention-cmd.active { display: block; }
    """

    input_active: reactive[bool] = reactive(False)

    class UserMessageSubmitted(Message):
        """Fired when the user submits a message."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(
        self,
        messages: list[ChatMessage] | None = None,
        model: str = "claude-sonnet-4",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._messages: list[ChatMessage] = messages or []
        self._model = model
        self._streaming_idx: int = -1
        self._total_tokens: int = 0
        self._total_cost: float = 0.0
        self._mounted = False

    def compose(self) -> ComposeResult:
        yield Static(
            f"[{TEXT_MUTED}]Model:[/] [{ACCENT_PURPLE}]{self._model}[/]",
            id="chat-model-bar",
        )
        yield VerticalScroll(id="chat-messages")
        yield MentionMenu(trigger="@", id="chat-mention-file")
        yield MentionMenu(trigger="/", id="chat-mention-cmd")
        with Horizontal(id="chat-input-row"):
            yield Input(placeholder="Type a message… (i to focus)", id="chat-input")
        yield Static(
            self._metrics_text(),
            id="chat-metrics",
        )

    def on_mount(self) -> None:
        self._mounted = True
        self._rebuild_messages()

    # ── Message management ──────────────────────────────────

    def add_message(self, message: ChatMessage) -> None:
        """Append a message and refresh."""
        self._messages.append(message)
        self._total_tokens += message.tokens
        self._total_cost += message.cost
        self._rebuild_messages()
        self._update_metrics()

    def start_streaming(self) -> None:
        """Begin a new streaming assistant message."""
        msg = ChatMessage(role="assistant", status="running")
        self._messages.append(msg)
        self._streaming_idx = len(self._messages) - 1
        self._rebuild_messages()

    def append_stream_delta(self, text: str) -> None:
        """Append text to the current streaming message."""
        if self._streaming_idx < 0 or self._streaming_idx >= len(self._messages):
            return
        self._messages[self._streaming_idx].content += text
        self._rebuild_messages()

    def finish_streaming(self, tokens: int = 0, cost: float = 0.0) -> None:
        """Mark the streaming message as complete."""
        if self._streaming_idx < 0 or self._streaming_idx >= len(self._messages):
            return
        self._messages[self._streaming_idx].status = "complete"
        self._messages[self._streaming_idx].tokens = tokens
        self._messages[self._streaming_idx].cost = cost
        self._total_tokens += tokens
        self._total_cost += cost
        self._streaming_idx = -1
        self._rebuild_messages()
        self._update_metrics()

    def _rebuild_messages(self) -> None:
        if not self._mounted:
            return
        try:
            container = self.query_one("#chat-messages", VerticalScroll)
        except Exception:
            return
        container.remove_children()
        for msg in self._messages:
            container.mount(MessageBubble(msg))
        container.scroll_end(animate=False)

    def _metrics_text(self) -> str:
        return (
            f"[{TEXT_MUTED}]Tokens:[/] [{ACCENT_AMBER}]{format_count(self._total_tokens)}[/]"
            f"  [{TEXT_MUTED}]Cost:[/] [{ACCENT_AMBER}]${self._total_cost:.4f}[/]"
        )

    def _update_metrics(self) -> None:
        with suppress(Exception):
            self.query_one("#chat-metrics", Static).update(self._metrics_text())

    # ── Input handling ──────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "chat-input":
            return
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        self.add_message(ChatMessage(role="user", content=text))
        self.post_message(self.UserMessageSubmitted(text))

    def action_focus_input(self) -> None:
        self.input_active = True
        with suppress(Exception):
            self.query_one("#chat-input", Input).focus()

    def action_unfocus_input(self) -> None:
        self.input_active = False

    # ── Mention menus ────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "chat-input":
            return
        text = event.value
        self._handle_mention_trigger(text)

    def _handle_mention_trigger(self, text: str) -> None:
        """Detect @, /, ! triggers and open the appropriate menu."""
        try:
            cmd_menu = self.query_one("#chat-mention-cmd", MentionMenu)
        except Exception:
            return
        if text.endswith("/"):
            cmd_menu.open(items=SLASH_COMMANDS, query="")
            return
        if cmd_menu.active and "/" in text:
            after_slash = text.rsplit("/", 1)[-1]
            cmd_menu.set_query(after_slash)
            return
        cmd_menu.close()

    def on_mention_menu_item_chosen(self, event: MentionMenu.ItemChosen) -> None:
        try:
            inp = self.query_one("#chat-input", Input)
        except Exception:
            return
        # Replace from the trigger character onward with the chosen value.
        text = inp.value
        trigger = event.item.category
        trigger_char = "/" if trigger == "command" or trigger == "skill" else "@"
        if trigger_char in text:
            prefix = text[: text.rfind(trigger_char)]
            inp.value = prefix + event.item.value
