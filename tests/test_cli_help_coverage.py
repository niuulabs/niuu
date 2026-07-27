"""Every operator-facing CLI option must carry help text.

An option with no help renders as a blank cell in the generated reference
(``docs/site/reference/``) and as a bare flag in ``--help``, which is how 21
undocumented flags on ``niuu platform up`` went unnoticed. This guards both
CLIs so the gap cannot reappear silently.

Options marked ``hidden=True`` are exempt: they are internal plumbing passed
by a caller, and neither ``--help`` nor the reference shows them.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from typer.main import get_command

os.environ.setdefault("NO_COLOR", "1")


def _load(which: str) -> Any:
    if which == "ravn":
        from ravn.cli.commands import app

        return get_command(app)
    from cli.app import build_app

    return get_command(build_app())


def _is_option(param: Any) -> bool:
    """Typer vendors a Parameter that does not subclass click.Option."""
    return getattr(param, "param_type_name", "") == "option"


def _undocumented(command: Any, path: list[str]) -> list[str]:
    found: list[str] = []
    for param in command.params:
        if not _is_option(param) or param.name == "help":
            continue
        if getattr(param, "hidden", False):
            continue
        if not (param.help or "").strip():
            found.append(f"{' '.join(path)}  {', '.join(param.opts)}")

    subcommands = getattr(command, "commands", None) or {}
    for name in sorted(subcommands):
        found.extend(_undocumented(subcommands[name], [*path, name]))
    return found


@pytest.mark.parametrize("cli", ["niuu", "ravn"])
def test_every_visible_option_has_help(cli: str) -> None:
    undocumented = _undocumented(_load(cli), [cli])

    assert not undocumented, "Options missing help text:\n  " + "\n  ".join(undocumented)


@pytest.mark.parametrize("cli", ["niuu", "ravn"])
def test_every_command_has_a_summary(cli: str) -> None:
    """A command with no docstring gets a blank description in the reference."""

    def _walk(command: Any, path: list[str]) -> list[str]:
        missing: list[str] = []
        if not (command.help or command.short_help or "").strip():
            missing.append(" ".join(path))
        subcommands = getattr(command, "commands", None) or {}
        for name in sorted(subcommands):
            if getattr(subcommands[name], "hidden", False):
                continue
            missing.extend(_walk(subcommands[name], [*path, name]))
        return missing

    assert not _walk(_load(cli), [cli]), "Commands missing a summary."
