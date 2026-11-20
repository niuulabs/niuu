"""Claude Code 2.1.321 /model picker and /effort slider screens (LEX-2022).

A running session switches model and effort for itself alone with the pickers' "s"
key; these are the screens the tmux transport reads while it does.
"""

from __future__ import annotations

_ROWS = (
    (1, "Default (recommended)", "Use the default model (currently Opus 5.5) · $4/$20 per Mtok"),
    (2, "Opus ✔", "Opus 5.5 · Best for everyday, complex tasks · $4/$20 per Mtok"),
    (3, "Fable", "{fable} · Most capable for your hardest tasks · $8/$40 per Mtok"),
    (4, "Sonnet", "Sonnet 5.5 · Efficient for routine tasks · $2/$10 per Mtok"),
    (5, "Haiku", "Haiku 5.5 · Fastest for quick answers · $0.10/$0.50 per Mtok"),
)
_LABELS = "low     medium     high     xhigh      max"
_INDENT = 25


def model_picker(cursor: int, *, fable: str = "Fable 5.2") -> str:
    """The open /model picker with its cursor (❯) on row ``cursor``."""
    lines = ["❯ /model", "────", "  Select model", "  Switch between Claude models."]
    for number, label, text in _ROWS:
        mark = "❯" if number == cursor else " "
        lines.append(f"  {mark} {number}. {label:<22} {text.format(fable=fable)}")
    lines += [
        "  ◉ xHigh effort ←/→ to adjust",
        "  Enter to set as default · s to use this session only · Esc to cancel",
    ]
    return "\n".join(lines)


def effort_slider(level: str) -> str:
    """The open /effort slider with its ▲ marker under ``level``."""
    start = _LABELS.index(level)
    centre = _INDENT + start + len(level) // 2
    bar = "─" * (centre - _INDENT) + "▲" + "─" * (_INDENT + len(_LABELS) - centre - 1)
    return "\n".join(
        [
            "❯ /effort",
            "────",
            "  Effort",
            " " * _INDENT + "Faster                             Smarter",
            " " * _INDENT + bar + "      Ultracode  off",
            " " * _INDENT + _LABELS + "      Tab to toggle",
            "  ←/→ to adjust · Enter to confirm · s for this session only · Esc to cancel",
        ]
    )


def model_ack(display: str, *, effort: str = "xhigh") -> str:
    """The pane after the picker closed: the echo, the acknowledgement and the footer."""
    return "\n".join(
        [
            "❯ /model",
            f"  ⎿  Set model to {display} for this session only",
            "────",
            "❯ ",
            "────",
            f"  ⏵⏵ bypass permissions on                       ◉ {effort} · /effort",
        ]
    )


def effort_ack(level: str) -> str:
    return "\n".join(
        ["❯ /effort", f"  ⎿  Set effort level to {level} (this session only): Effort", "❯ "]
    )
