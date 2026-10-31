"""The permission mode an unattended Claude Code session runs with.

A Forge node chooses it once in its config file (``claude_permission_mode``): YOLO
(``bypassPermissions``) where the host allows it, Claude's classifier-gated ``auto``
mode everywhere else. Only Claude transports check the value, so a typo on a node
never stops that node's Codex or other engines.
"""

DEFAULT_CLAUDE_PERMISSION_MODE = "auto"

# Modes that let a session work without a human answering prompts. ``plan`` never
# edits, and ``manual``/``dontAsk``/``default`` wait on or silently deny prompts that
# unattended transports have no channel to answer.
UNATTENDED_CLAUDE_PERMISSION_MODES = ("acceptEdits", "auto", "bypassPermissions")


def resolve_claude_permission_mode(value: str) -> str:
    """Return the canonical spelling of an unattended mode, or raise with the remedy."""
    wanted = str(value).strip().lower()
    for mode in UNATTENDED_CLAUDE_PERMISSION_MODES:
        if wanted == mode.lower():
            return mode
    raise ValueError(
        f"claude_permission_mode {value!r} is not an unattended Claude permission mode; "
        f"set one of {', '.join(UNATTENDED_CLAUDE_PERMISSION_MODES)} in the node config"
    )
