"""uvicorn's own request-line log records must never carry a bearer.

Browser WebSocket clients cannot set an Authorization header, so they send
their bearer as ``?token=``/``?access_token=``. uvicorn logs the raw request
target — query included — on ``uvicorn.error`` (WebSocket accept/reject) and
``uvicorn.access`` (HTTP), so without a filter those JWTs land in app logs.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, WebSocket
from uvicorn.logging import AccessFormatter
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from niuu.observability import UvicornLogRedactionFilter, install_uvicorn_log_redaction

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyLWEifQ.c2lnbmF0dXJl"
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
SRC = Path(__file__).parents[2] / "src"


@pytest.fixture(autouse=True)
def _restore_uvicorn_loggers():
    """Put uvicorn's loggers back exactly as found: uvicorn.Config runs dictConfig."""
    saved = {
        name: (
            list(lg.handlers),
            list(lg.filters),
            lg.level,
            lg.propagate,
            lg.disabled,
        )
        for name in _UVICORN_LOGGERS
        for lg in [logging.getLogger(name)]
    }
    for name in _UVICORN_LOGGERS:
        for f in list(logging.getLogger(name).filters):
            if isinstance(f, UvicornLogRedactionFilter):
                logging.getLogger(name).removeFilter(f)
    yield
    for name, (handlers, filters, level, propagate, disabled) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers[:] = handlers
        lg.filters[:] = filters
        lg.setLevel(level)
        lg.propagate = propagate
        lg.disabled = disabled


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/ok")
    async def ok() -> dict[str, bool]:
        return {"ok": True}

    @app.websocket("/api/v1/forge/sessions/{sid}/replay")
    async def replay(websocket: WebSocket, sid: str) -> None:
        # Closing before accept is how every Niuu WS route denies auth; uvicorn
        # answers HTTP 403 and logs '"WebSocket <path>?<query>" 403'.
        await websocket.close(code=1008)

    return app


@pytest.mark.parametrize("order", ["install_then_config", "config_then_install"])
async def test_real_uvicorn_redacts_websocket_403_and_access_lines(order: str) -> None:
    """Both entrypoint orders: ``uvicorn.run(app)`` builds the app (installing
    the filter) before ``Config`` runs dictConfig; ``python -m uvicorn mod:app``
    runs dictConfig first and imports the app afterwards."""
    if order == "install_then_config":
        install_uvicorn_log_redaction()
    config = uvicorn.Config(_app(), host="127.0.0.1", port=0, log_level="info", lifespan="off")
    if order == "config_then_install":
        install_uvicorn_log_redaction()

    capture = _Capture()
    # dictConfig replaced these loggers' handlers, so attach after Config.
    for name in ("uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addHandler(capture)

    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://127.0.0.1:{port}/ok?access_token={JWT}&page=2")
        assert response.status_code == 200
        with pytest.raises(InvalidStatus) as denied:
            async with connect(
                f"ws://127.0.0.1:{port}/api/v1/forge/sessions/s1/replay?token={JWT}&after=7"
            ):
                pytest.fail("the replay socket should have been refused")
        assert denied.value.response.status_code == 403
    finally:
        server.should_exit = True
        await task

    messages = [record.getMessage() for record in capture.records]
    assert all(JWT not in message for message in messages), messages
    ws_line = next(m for m in messages if '"WebSocket /api/v1/forge/sessions/s1/replay?' in m)
    assert re.search(r"\?token=%5BREDACTED%5D&after=7\" 403$", ws_line), ws_line
    access = next(r for r in capture.records if r.name == "uvicorn.access")
    assert "/ok?access_token=%5BREDACTED%5D&page=2" in access.getMessage()
    # uvicorn's AccessFormatter unpacks args positionally and needs %d an int.
    assert access.args[4] == 200
    assert JWT not in AccessFormatter().format(access)


def _record(msg: str, args: object) -> logging.LogRecord:
    return logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, msg, args, None)


def test_non_string_arguments_keep_their_types() -> None:
    record = _record('%s - "%s %s HTTP/%s" %d', ("1.2.3.4:5", "GET", f"/x?token={JWT}", "1.1", 401))
    UvicornLogRedactionFilter().filter(record)
    assert record.args == ("1.2.3.4:5", "GET", "/x?token=%5BREDACTED%5D", "1.1", 401)


def test_mapping_arguments_and_the_message_itself_are_redacted() -> None:
    msg = f"Authorization: Bearer {JWT} at /bot123:SECRET/sendMessage %(url)s as %(n)d"
    # LogRecord unwraps a single mapping argument into record.args.
    record = _record(msg, ({"url": "/y?api_key=k&q=1", "n": 3},))
    UvicornLogRedactionFilter().filter(record)
    assert record.args == {"url": "/y?api_key=%5BREDACTED%5D&q=1", "n": 3}
    assert JWT not in record.msg
    assert "SECRET" not in record.msg


def test_text_without_sensitive_query_keys_is_untouched() -> None:
    path = "/s/1/session?after=3&x=a%2Fb"
    record = _record('%s - "WebSocket %s" [accepted]', ("1.2.3.4:5", path))
    UvicornLogRedactionFilter().filter(record)
    assert record.args == ("1.2.3.4:5", path)


def test_install_is_idempotent() -> None:
    install_uvicorn_log_redaction()
    install_uvicorn_log_redaction()
    for name in _UVICORN_LOGGERS:
        filters = logging.getLogger(name).filters
        assert sum(isinstance(f, UvicornLogRedactionFilter) for f in filters) == 1


# Modules that start uvicorn on an app built by a factory which installs the
# filter itself (the factory module is checked instead).
_SERVED_BY_INSTALLING_FACTORY = {
    "bifrost/__main__.py": "bifrost/app.py",
    "mimir/__main__.py": "mimir/app.py",
}
# Apps the charts/containers serve with the ``uvicorn module:app`` CLI, which
# bypasses every Python runner: their factory must install it.
_UVICORN_CLI_TARGETS = ("volundr/main.py", "cli/shared_host.py", "volundr/catalog/app.py")
_INSTALLS = re.compile(r"\binstall_uvicorn_log_redaction\(\)|\bconfigure_logging\(")


def test_every_uvicorn_entrypoint_installs_the_redaction_filter() -> None:
    starts_uvicorn = re.compile(r"\buvicorn\.(run|Config|Server)\(")
    missing = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        text = path.read_text()
        if not starts_uvicorn.search(text) or rel == "niuu/observability.py":
            continue
        checked = SRC / _SERVED_BY_INSTALLING_FACTORY.get(rel, rel)
        if not _INSTALLS.search(checked.read_text()):
            missing.append(rel)
    for rel in _UVICORN_CLI_TARGETS:
        if not _INSTALLS.search((SRC / rel).read_text()):
            missing.append(rel)
    assert missing == []
