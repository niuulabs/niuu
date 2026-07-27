#!/usr/bin/env python
"""Generate the CLI reference pages from the live command trees.

The pages under ``docs/site/reference/`` are generated, not hand-written, so
they cannot drift from the actual commands. Re-run after adding or changing a
command:

    uv run python scripts/generate_cli_docs.py

Options are read from the Click/Typer parameter objects rather than scraped
from rendered ``--help`` output, so the tables carry real defaults, types, and
environment variables.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("NO_COLOR", "1")
os.environ.setdefault("TERM", "dumb")

import click  # noqa: E402
from typer.main import get_command  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs" / "site" / "reference"

# Typer uses a sentinel object for "no default"; it must never reach a page.
_SENTINEL_REPR = "<object object"


def _load(which: str) -> click.Command:
    if which == "ravn":
        from ravn.cli.commands import app

        return get_command(app)
    from cli.app import build_app

    return get_command(build_app())


def _clean(text: str | None) -> str:
    """Collapse a help string to a single markdown-table-safe line."""
    if not text:
        return ""
    collapsed = " ".join(str(text).split())
    return collapsed.replace("|", "\\|")


def _summary(command: click.Command) -> str:
    """First paragraph of the command's help."""
    raw = command.help or command.short_help or ""
    first = raw.strip().split("\n\n", 1)[0]
    return _clean(first)


def _details(command: click.Command) -> list[str]:
    """Help paragraphs after the summary, with Click's \\b markers removed."""
    raw = (command.help or "").strip()
    parts = raw.split("\n\n")
    out: list[str] = []
    for para in parts[1:]:
        stripped = para.strip()
        if not stripped or stripped == "\b":
            continue
        out.append(stripped.replace("\b\n", "").strip())
    return out


def _default_repr(param: Any) -> str:
    default = param.default
    if default is None or default == "" or default is False:
        return ""
    if callable(default):
        return ""
    text = repr(default) if not isinstance(default, str) else default
    if _SENTINEL_REPR in text:
        return ""
    return _clean(text)


def _type_name(param: Any) -> str:
    if getattr(param, "is_flag", False):
        return "flag"
    name = getattr(param.type, "name", "") or ""
    if name == "text":
        return "TEXT"
    return name.upper() if name else "TEXT"


def _is_option(param: Any) -> bool:
    """True for options.

    Typer vendors its own ``Parameter`` that does not subclass
    ``click.Option``, so the shared ``param_type_name`` attribute is the
    reliable discriminator across both CLIs.
    """
    return getattr(param, "param_type_name", "") == "option"


def _is_argument(param: Any) -> bool:
    return getattr(param, "param_type_name", "") == "argument"


def _option_rows(command: click.Command) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for param in command.params:
        if not _is_option(param) or param.name == "help":
            continue
        # Hidden options are internal plumbing the caller passes, not operator
        # surface — `--help` omits them and so must the reference.
        if getattr(param, "hidden", False):
            continue
        flags = ", ".join(f"`{opt}`" for opt in param.opts + param.secondary_opts)
        help_text = _clean(param.help)
        envvar = param.envvar
        if envvar:
            names = envvar if isinstance(envvar, list) else [envvar]
            help_text = f"{help_text} Env: {', '.join(f'`{n}`' for n in names)}".strip()
        if param.required:
            help_text = f"{help_text} **Required.**".strip()
        rows.append((flags, _type_name(param), _default_repr(param), help_text))
    return rows


def _argument_rows(command: click.Command) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for param in command.params:
        if not _is_argument(param):
            continue
        required = "yes" if param.required else "no"
        rows.append((f"`{param.name.upper()}`", required, _clean(getattr(param, "help", ""))))
    return rows


def _usage(command: click.Command, path: list[str]) -> str:
    ctx = click.Context(command, info_name=path[-1])
    pieces = command.collect_usage_pieces(ctx)
    return " ".join([*path, *pieces])


def _render(command: click.Command, path: list[str], depth: int, lines: list[str]) -> None:
    heading = "#" * min(depth + 1, 6)
    lines.append(f"{heading} `{' '.join(path)}`")
    lines.append("")

    summary = _summary(command)
    if summary:
        lines.append(summary)
        lines.append("")

    lines.append("```bash")
    lines.append(_usage(command, path))
    lines.append("```")
    lines.append("")

    for para in _details(command):
        body = [
            line
            for line in inspect.cleandoc(para).splitlines()
            if line.strip().rstrip(":").lower() not in ("example", "examples")
        ]
        if any(line.strip().startswith(path[0]) for line in body):
            lines.append("```bash")
            lines.extend(body)
            lines.append("```")
        else:
            lines.append(_clean(para))
        lines.append("")

    arguments = _argument_rows(command)
    if arguments:
        lines.append("| Argument | Required | Description |")
        lines.append("| --- | --- | --- |")
        for name, required, help_text in arguments:
            lines.append(f"| {name} | {required} | {help_text} |")
        lines.append("")

    options = _option_rows(command)
    if options:
        lines.append("| Option | Type | Default | Description |")
        lines.append("| --- | --- | --- | --- |")
        for flags, type_name, default, help_text in options:
            default_cell = f"`{default}`" if default else ""
            lines.append(f"| {flags} | {type_name} | {default_cell} | {help_text} |")
        lines.append("")

    subcommands = getattr(command, "commands", None) or {}
    visible = [
        name for name in sorted(subcommands) if not getattr(subcommands[name], "hidden", False)
    ]
    if not visible:
        return

    lines.append("| Subcommand | Description |")
    lines.append("| --- | --- |")
    for name in visible:
        lines.append(f"| [`{name}`](#{'-'.join([*path, name])}) | {_summary(subcommands[name])} |")
    lines.append("")

    for name in visible:
        _render(subcommands[name], [*path, name], depth + 1, lines)


def _page(which: str, title: str, intro: list[str]) -> str:
    command = _load(which)
    lines = [f"# {title}", ""]
    lines.extend(intro)
    lines.append("")
    lines.append("<!-- Generated by scripts/generate_cli_docs.py — do not edit by hand. -->")
    lines.append("")
    _render(command, [which], 1, lines)
    text = "\n".join(lines).rstrip() + "\n"
    if _SENTINEL_REPR in text:  # pragma: no cover - guard against a Typer change
        raise RuntimeError("Sentinel default leaked into the generated page.")
    return text


NIUU_INTRO = [
    "Every `niuu` command and option. `niuu` drives the platform itself:",
    "authentication, contexts, the local stack, sessions, runs, and sagas.",
    "",
    "For the agent runtime CLI see [ravn](cli-ravn.md).",
]

RAVN_INTRO = [
    "Every `ravn` command and option. `ravn` is the agent runtime: it runs a",
    "conversation or daemon, manages personas and profiles, and supervises",
    "rooms, flocks, and wardens on the local host.",
    "",
    "For the platform CLI see [niuu](cli-niuu.md).",
]


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    targets: list[tuple[str, str, str, list[str]]] = [
        ("niuu", "niuu CLI reference", "cli-niuu.md", NIUU_INTRO),
        ("ravn", "ravn CLI reference", "cli-ravn.md", RAVN_INTRO),
    ]
    for which, title, filename, intro in targets:
        path = DOCS_DIR / filename
        path.write_text(_page(which, title, intro), encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
