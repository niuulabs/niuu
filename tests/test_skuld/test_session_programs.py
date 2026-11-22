"""The pinned programs of a session's MCP servers are installed before the engine starts."""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError

from niuu.domain.session_program import ProgramError, SessionProgram, platform_key
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.session_programs import ProgramInstaller

URL = "https://downloads.example/cad-1.0-linux.tar.gz"
COMMAND = "cad-1.0/bin/cad"
SCRIPT = b"#!/bin/sh\necho cad\n"


def _tar(members: dict[str, bytes] | None = None, link: tuple[str, str] | None = None) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for name, content in (members or {COMMAND: SCRIPT}).items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(content))
        if link is not None:
            info = tarfile.TarInfo(link[0])
            info.type, info.linkname = tarfile.SYMTYPE, link[1]
            tar.addfile(info)
    return raw.getvalue()


def _zip() -> bytes:
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as bundle:
        bundle.writestr(COMMAND, SCRIPT)
    return raw.getvalue()


def _program(archive: bytes, **platforms: str) -> dict[str, Any]:
    build = {"url": URL, "sha256": hashlib.sha256(archive).hexdigest(), "command": COMMAND}
    return {"version": "1.0", "platforms": {"linux-x86_64": build}}


def _server(archive: bytes, **extra: Any) -> dict[str, Any]:
    return {"name": "cad", "required": True, "args": ["mcp"], "program": _program(archive), **extra}


def _installer(tmp_path: Path, host_platform: str = "linux-x86_64") -> ProgramInstaller:
    return ProgramInstaller(
        tmp_path / "cache", connect_timeout_s=1, read_timeout_s=1, host_platform=host_platform
    )


def _left_behind(tmp_path: Path) -> list[str]:
    cache = tmp_path / "cache"
    return sorted(p.name for p in cache.iterdir()) if cache.exists() else []


@respx.mock
@pytest.mark.parametrize("archive", [_tar(), _zip()], ids=["tar", "zip"])
def test_a_program_becomes_the_servers_command(tmp_path: Path, archive: bytes) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, content=archive))
    other = {"name": "npx-server", "command": "npx"}

    servers = _installer(tmp_path).resolve([other, _server(archive)])

    digest = hashlib.sha256(archive).hexdigest()
    command = tmp_path / "cache" / digest / COMMAND
    assert servers == [
        other,
        {
            "name": "cad",
            "required": True,
            "args": ["mcp"],
            "type": "stdio",
            "command": str(command),
        },
    ]
    assert command.read_bytes() == SCRIPT
    assert os.access(command, os.X_OK)
    assert _left_behind(tmp_path) == [digest]

    _installer(tmp_path).resolve([_server(archive)])
    assert route.call_count == 1, "a program installed once is not fetched again"


@respx.mock
def test_a_redirect_is_followed(tmp_path: Path) -> None:
    archive = _tar()
    target = "https://cdn.example/cad.tar.gz"
    respx.get(URL).mock(return_value=httpx.Response(302, headers={"location": target}))
    respx.get(target).mock(return_value=httpx.Response(200, content=archive))

    [server] = _installer(tmp_path).resolve([_server(archive)])

    assert Path(server["command"]).is_file()


@respx.mock
def test_an_archive_that_is_not_the_pinned_one_is_refused(tmp_path: Path) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, content=_tar({COMMAND: b"tampered"})))

    with pytest.raises(ProgramError, match="not the pinned"):
        _installer(tmp_path).resolve([_server(_tar())])

    assert _left_behind(tmp_path) == []


def test_a_platform_without_a_build_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ProgramError, match="no build for darwin-arm64.*linux-x86_64"):
        _installer(tmp_path, "darwin-arm64").resolve([_server(_tar())])


@respx.mock
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(404), "404"),
        (httpx.ConnectError("no route"), "no route"),
    ],
)
def test_an_archive_that_cannot_be_fetched_is_refused(tmp_path, response, message) -> None:
    route = respx.get(URL)
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)

    with pytest.raises(ProgramError, match=f"could not be installed from {URL}.*{message}"):
        _installer(tmp_path).resolve([_server(_tar())])

    assert _left_behind(tmp_path) == []


@respx.mock
@pytest.mark.parametrize(
    ("archive", "message"),
    [
        (_tar({"cad-1.0/README": b"x"}), "has no file 'cad-1.0/bin/cad'"),
        (b"not an archive", "not a tar or zip archive"),
        (_tar({"../escape": b"x"}), "could not be installed"),
        (_tar({"cad-1.0/README": b"x"}, link=(COMMAND, "/bin/sh")), "could not be installed"),
    ],
    ids=["no command", "no archive", "escaping member", "escaping link"],
)
def test_an_archive_without_the_program_is_refused(tmp_path, archive, message) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, content=archive))

    with pytest.raises(ProgramError, match=message):
        _installer(tmp_path).resolve([_server(archive)])

    assert _left_behind(tmp_path) == []


@respx.mock
def test_a_session_that_installed_it_first_wins(tmp_path: Path) -> None:
    archive = _tar()
    home = tmp_path / "cache" / hashlib.sha256(archive).hexdigest()

    def installed_meanwhile(_request: httpx.Request) -> httpx.Response:
        (home / COMMAND).parent.mkdir(parents=True)
        (home / COMMAND).write_bytes(b"first")
        return httpx.Response(200, content=archive)

    respx.get(URL).mock(side_effect=installed_meanwhile)

    [server] = _installer(tmp_path).resolve([_server(archive)])

    assert Path(server["command"]).read_bytes() == b"first"
    assert _left_behind(tmp_path) == [home.name]


@respx.mock
def test_a_place_taken_by_something_else_is_refused(tmp_path: Path) -> None:
    archive = _tar()
    home = tmp_path / "cache" / hashlib.sha256(archive).hexdigest()
    (home / "other").mkdir(parents=True)
    respx.get(URL).mock(return_value=httpx.Response(200, content=archive))

    with pytest.raises(ProgramError, match="could not be installed"):
        _installer(tmp_path).resolve([_server(archive)])


@pytest.mark.parametrize("extra", [{"command": "cad"}, {"url": "https://x"}])
def test_a_program_beside_a_command_or_url_is_refused(tmp_path, extra) -> None:
    with pytest.raises(ProgramError, match="both a program and a command or url"):
        _installer(tmp_path).resolve([_server(_tar(), **extra)])


def test_an_invalid_program_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ProgramError, match="invalid program"):
        _installer(tmp_path).resolve([{"name": "cad", "program": {"version": "1"}}])


@pytest.mark.parametrize(
    "change",
    [
        {"url": "http://plain.example/cad.tar"},
        {"sha256": "abc"},
        {"command": "/bin/sh"},
        {"command": "../cad"},
        {"command": ""},
    ],
)
def test_the_pin_is_validated(change) -> None:
    program = _program(_tar())
    program["platforms"]["linux-x86_64"].update(change)
    with pytest.raises(ValidationError):
        SessionProgram.model_validate(program)


def test_an_unknown_platform_key_is_refused() -> None:
    program = _program(_tar())
    program["platforms"]["windows-x64"] = program["platforms"].pop("linux-x86_64")
    with pytest.raises(ValidationError, match="unknown platforms windows-x64"):
        SessionProgram.model_validate(program)


def test_platform_keys() -> None:
    assert platform_key("Linux", "aarch64") == "linux-arm64"
    assert platform_key("Linux", "x86_64") == "linux-x86_64"
    assert platform_key("Darwin", "arm64") == "darwin-arm64"
    assert platform_key("Linux", "riscv64") == "linux-riscv64"


# -- the broker


def _broker(tmp_path: Path, servers: list[dict[str, Any]], adapter: str) -> Broker:
    (tmp_path / "ws").mkdir()
    return Broker(
        settings=SkuldSettings(
            session={"id": "program-session", "workspace_dir": str(tmp_path / "ws")},
            transport_adapter=adapter,
            forge_mcp={"enabled": False},
            home_dir=str(tmp_path / "home"),
            programs={"cache_dir": str(tmp_path / "cache")},
            mcp_servers=servers,
        )
    )


SDK = "skuld.transports.sdk.SDKTransport"
PI = "skuld.transports.pi.PiRpcTransport"


async def test_a_session_whose_program_cannot_be_installed_does_not_start(tmp_path) -> None:
    server = {"name": "cad", "program": {"version": "1"}}
    broker = _broker(tmp_path, [server], SDK)

    await broker.startup()

    status = broker.startup_status()
    assert status["ready"] is False
    assert status["startup_state"] == "failed"
    assert "invalid program" in status["error"]
    assert broker._transport is None


@respx.mock
async def test_the_engine_gets_the_installed_command(tmp_path) -> None:
    archive = _tar()
    program = _program(archive)
    for key in ("linux-arm64", "darwin-arm64", "darwin-x86_64"):
        program["platforms"][key] = program["platforms"]["linux-x86_64"]
    respx.get(URL).mock(return_value=httpx.Response(200, content=archive))
    broker = _broker(tmp_path, [{"name": "cad", "program": program}], SDK)

    await broker._install_session_programs()

    [server] = broker._build_transport_kwargs()["mcp_servers"]
    assert Path(server["command"]).is_relative_to(tmp_path / "cache")
    assert "program" not in server


async def test_a_harness_without_mcp_refuses_a_server_the_session_asked_for(tmp_path) -> None:
    broker = _broker(tmp_path, [{"name": "cad", "command": "cad", "required": True}], PI)

    await broker.startup()

    assert broker.startup_status()["startup_state"] == "failed"
    assert "takes no MCP servers" in broker.startup_status()["error"]


def test_programs_are_kept_in_the_home_by_default(tmp_path) -> None:
    settings = SkuldSettings(home_dir=str(tmp_path))
    assert settings.programs_path == f"{tmp_path}/.cache/niuu/programs"
