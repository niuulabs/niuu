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


def _common_tasks(tasks: list[tuple[str, str, list[str]]]) -> list[str]:
    """Render the worked examples that open each page.

    The full command tree below them is exhaustive but alphabetical, which is
    the wrong shape for "how do I actually start a room". These answer that
    first; every line is a real invocation.
    """
    lines = ["## Common tasks", ""]
    for heading, blurb, commands in tasks:
        lines.append(f"### {heading}")
        lines.append("")
        if blurb:
            lines.append(blurb)
            lines.append("")
        lines.append("```bash")
        lines.extend(commands)
        lines.append("```")
        lines.append("")
    return lines


def _page(
    which: str,
    title: str,
    intro: list[str],
    tasks: list[tuple[str, str, list[str]]],
) -> str:
    command = _load(which)
    lines = [f"# {title}", ""]
    lines.extend(intro)
    lines.append("")
    lines.append("<!-- Generated by scripts/generate_cli_docs.py — do not edit by hand. -->")
    lines.append("")
    lines.extend(_common_tasks(tasks))
    lines.append("## Every command")
    lines.append("")
    _render(command, [which], 2, lines)
    text = "\n".join(lines).rstrip() + "\n"
    if _SENTINEL_REPR in text:  # pragma: no cover - guard against a Typer change
        raise RuntimeError("Sentinel default leaked into the generated page.")
    return text


NIUU_INTRO = [
    "`niuu` drives the platform itself: authentication, server contexts, the",
    "local stack, coding sessions, runs, and sagas.",
    "",
    "Start with the worked examples below; the full command tree follows.",
    "For the agent runtime CLI see [ravn](cli-ravn.md).",
]

RAVN_INTRO = [
    "`ravn` is the agent runtime: it runs a conversation or daemon, manages",
    "personas and profiles, and supervises rooms, flocks, and wardens on the",
    "local host.",
    "",
    "Start with the worked examples below; the full command tree follows.",
    "For the platform CLI see [niuu](cli-niuu.md).",
]

# Every line here is a real invocation — keep it that way when editing.
NIUU_TASKS: list[tuple[str, str, list[str]]] = [
    (
        "Run the local stack",
        "The scripts are the short path for day-to-day work. Use the CLI "
        "directly when you are debugging the platform host itself.",
        [
            "./start-dev                 # full local stack on :8080, mini mode",
            "./stop-dev",
            "",
            "niuu platform up            # same thing, driven directly",
            "niuu platform status        # health of every registered service",
            "niuu platform down",
        ],
    ),
    (
        "Choose what starts",
        "Services resolve from plugin defaults, then config, then these flags. "
        "`--all` overrides everything.",
        [
            "niuu platform up --all                     # every registered service",
            "niuu platform up --no-web                  # backend only, no web UI",
            "niuu platform up --no-mimir --no-ting      # skip specific services",
            "niuu platform up --skip-preflight          # bypass the host checks",
        ],
    ),
    (
        "First-time setup",
        "",
        [
            "niuu platform init          # interactive setup wizard",
            "niuu config show            # what the CLI is currently using",
            "niuu config set server.port 8090",
        ],
    ),
    (
        "Authenticate and switch servers",
        "A context is a named server the CLI talks to, so you can move between "
        "a local stack and a shared one.",
        [
            "niuu login",
            "niuu whoami",
            "",
            "niuu context list",
            "niuu context add staging https://niuu.example.com",
            "niuu context use staging",
        ],
    ),
    (
        "Work with coding sessions",
        "",
        [
            "niuu sessions list",
            "niuu sessions create my-feature",
            "niuu sessions stop <session-id>",
            "niuu sessions list --json          # machine-readable",
        ],
    ),
    (
        "Approve and follow autonomous work",
        "Runs are individual executions; sagas are the longer campaigns that dispatch them.",
        [
            "niuu runs active",
            "niuu runs approve <run-id>",
            "niuu runs reject <run-id>",
            "",
            "niuu sagas list",
            "niuu sagas create nightly-cleanup",
            "niuu sagas dispatch <saga-id>",
        ],
    ),
    (
        "Watch it all",
        "",
        [
            "niuu tui                    # interactive terminal UI",
            "niuu ravn list              # active agent sessions",
            "niuu ravn status            # Ravn platform status",
        ],
    ),
]

RAVN_TASKS: list[tuple[str, str, list[str]]] = [
    (
        "Talk to an agent",
        "Pass a prompt for a single turn, or omit it for a REPL.",
        [
            'ravn run "summarise the failing tests"',
            "ravn run                                   # interactive REPL",
            "ravn run --persona reviewer                # pick a role",
            'ravn run --no-tools "just answer, run nothing"',
            "ravn run --show-usage                      # print token usage",
        ],
    ),
    (
        "See what roles are available",
        "Persona picks *who* an agent is; profile picks *how* it is deployed. "
        "Both accept a name or a path to a YAML file.",
        [
            "ravn personas list",
            "ravn personas list --builtin",
            "ravn profiles list",
            "",
            "ravn run --persona ./contrib/red-team.yaml",
        ],
    ),
    (
        "Start a room and put agents in it",
        "A room is a local collaboration space — no platform services needed. "
        "It runs until you stop it.",
        [
            "ravn room create desk                      # create and start",
            "ravn room ls                               # rooms and their status",
            "",
            "ravn join --persona reviewer --room desk",
            "ravn join --persona coder --room desk --as builder",
            "ravn room members --room desk              # who is in, and live?",
        ],
    ),
    (
        "Talk in a room",
        "`@handle` addresses a member; every recipient gets the whole message. "
        "With no address it goes to whoever spoke last.",
        [
            "ravn room join --participant human:you --environment desk --role owner",
            "",
            "ravn room post --as human:you '@reviewer take a look at the diff'",
            "ravn room post --as human:you '@builder build it then @reviewer check it'",
            "ravn room post --as human:you --to reviewer 'explicit target'",
            "ravn room post --as human:you --dry-run '@reviewer who gets this?'",
            "",
            "ravn room tail --room desk                 # recent turns",
            "ravn room tail --room desk --follow        # keep streaming",
        ],
    ),
    (
        "Stop and clean up a room",
        "`stop` keeps the definition so you can start it again; `rm` deletes "
        "the room and its transcripts.",
        [
            "ravn leave --as builder --room desk        # remove one member",
            "ravn room stop desk                        # stop the broker",
            "ravn room start desk                       # bring it back",
            "ravn room rm desk --force                  # delete it entirely",
        ],
    ),
    (
        "Run a flock",
        "A flock is a mesh of agent daemons that can delegate work to each "
        "other. Point one at a room and its nodes join as members.",
        [
            "ravn flock init reviewer coder             # write the definition",
            "ravn flock init --room desk reviewer coder # ...and join a room",
            "ravn flock start",
            "ravn flock status",
            "ravn flock peers                           # who found whom",
            "ravn flock logs --node reviewer",
            "ravn flock stop",
        ],
    ),
    (
        "Run unattended",
        "The daemon drives itself and answers what is addressed to it; the "
        "gateway exposes Telegram and a local HTTP channel.",
        [
            "ravn daemon --persona coordinator",
            "ravn listen --persona coder                # take dispatched tasks",
            "ravn gateway gateway --telegram --http",
            "",
            "ravn warden list                           # persisted long-lived agents",
            "ravn warden create <name>",
        ],
    ),
    (
        "Operator surfaces",
        "",
        [
            "ravn tui                                   # terminal UI",
            "ravn web --port 7477                       # standalone web UI",
            "ravn peers                                 # verified mesh peers",
            "",
            "ravn approvals list                        # command approval patterns",
            "ravn approvals revoke '<pattern>'",
        ],
    ),
]


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    targets = [
        ("niuu", "niuu CLI reference", "cli-niuu.md", NIUU_INTRO, NIUU_TASKS),
        ("ravn", "ravn CLI reference", "cli-ravn.md", RAVN_INTRO, RAVN_TASKS),
    ]
    for which, title, filename, intro, tasks in targets:
        path = DOCS_DIR / filename
        path.write_text(_page(which, title, intro, tasks), encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
