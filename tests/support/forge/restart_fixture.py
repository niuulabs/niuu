"""Provider-free subprocess fixture for the real Forge lifespan/reconnect test.

Only database/catalog infrastructure and the model transport are faked. The
Volundr composition, local process manager, Skuld broker, HTTP/WS proxy, OS
processes, state-file recovery and conversation persistence are production code.
Never import this module from production. It has no provider credentials.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from niuu.ports.cli import CLITransport


def process_identity(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "start_ticks": fields[19]}


class HoldingTransport(CLITransport):
    """A synthetic turn held across an API restart, with an owned native child."""

    def __init__(self, workspace_dir: str):
        super().__init__()
        self.workspace = Path(workspace_dir)
        self.child = None
        self.task = None

    async def start(self):
        self.child = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", "import time; time.sleep(180)"
        )
        (self.workspace / "native.pid").write_text(str(self.child.pid))
        (self.workspace / "native.identity.json").write_text(
            json.dumps(process_identity(self.child.pid))
        )
        await self._emit({"type": "system", "subtype": "init", "session_id": self.session_id})

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.child is not None and self.child.returncode is None:
            self.child.terminate()
            await self.child.wait()

    async def send_message(self, content, *, msg_id=None, request_id=None):
        with (self.workspace / "sends.jsonl").open("a") as log:
            log.write(json.dumps({"content": content, "request_id": request_id}) + "\n")
        self.task = asyncio.create_task(self._turn())

    async def _turn(self):
        await self._text("BEFORE_RESTART", "before-item")
        while not (self.workspace / "release-turn").exists():
            offline = self.workspace / "emit-offline"
            if offline.exists():
                await self._text("DURING_API_OUTAGE", "offline-item")
                offline.unlink()
            await asyncio.sleep(0.05)
        await self._text("AFTER_RECONNECT", "after-item")
        await self._emit({"type": "result", "subtype": "success", "result": "AFTER_RECONNECT"})

    async def _text(self, text, item_id):
        await self._emit(
            {
                "type": "assistant",
                "message": {
                    "id": item_id,
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                },
            }
        )

    @property
    def session_id(self):
        return "provider-free-native-identity"

    @property
    def last_result(self):
        return None

    @property
    def is_alive(self):
        return self.child is not None and self.child.returncode is None

    @property
    def is_turn_active(self):
        return self.task is not None and not self.task.done()


def _wait_gateway_ready(port: int, gateway: subprocess.Popen, timeout: float = 30.0) -> None:
    """Block until the spawned gateway reports ready, or raise."""
    url = f"http://127.0.0.1:{port}/ready"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if gateway.poll() is not None:
            raise RuntimeError(f"gateway exited with {gateway.returncode} before becoming ready")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if json.loads(response.read()).get("ready") is True:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise TimeoutError(f"gateway did not become ready within {timeout}s")


def serve(config_path: Path):
    """Start the real API, using test-only database infrastructure."""
    import uvicorn

    from tests.conftest import InMemorySessionRepository
    from volundr.config import Settings
    from volundr.domain.models import Session
    from volundr.main import create_app

    config = json.loads(config_path.read_text())
    repo = InMemorySessionRepository()
    session = Session.model_validate(config["session"])
    repo._sessions[session.id] = session
    pool = AsyncMock()
    pool.get_max_size = MagicMock(return_value=10)

    @asynccontextmanager
    async def database(_settings):
        yield pool

    state_file = Path(config["state_file"])
    # The first API owns the spawn, matching local-process deployment. A new
    # process group prevents the test driver's API termination from killing it.
    if not state_file.exists():
        with Path(config["gateway_log"]).open("ab") as log:
            gateway = subprocess.Popen(
                [sys.executable, "-m", "skuld"],
                env=config["gateway_env"],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        Path(config["workspace"]).joinpath("gateway.identity.json").write_text(
            json.dumps(process_identity(gateway.pid))
        )
        # The seeded session is RUNNING, which means its broker was observed
        # ready. Reach that state before the API starts reconciling against it.
        _wait_gateway_ready(config["gateway_port"], gateway)
        state_file.write_text(
            json.dumps(
                {
                    str(session.id): {
                        "session_id": str(session.id),
                        "pid": gateway.pid,
                        "port": config["gateway_port"],
                        "workspace": config["workspace"],
                        "state": "running",
                    }
                }
            )
        )
    settings = Settings(
        pod_manager={
            "adapter": "volundr.adapters.outbound.local_process.LocalProcessPodManager",
            "kwargs": {
                "state_file": str(state_file),
                "stop_timeout": 1,
                "workspaces_dir": config["workspace"],
            },
        },
        local_mounts={"enabled": True, "mini_mode": True},
        session_liveness={"enabled": False, "reconcile_interval_seconds": 5},
        telegram_ingress={"enabled": False},
    )
    with (
        patch("volundr.main._bootstrap_startup_schema", new=AsyncMock()),
        patch("volundr.main.database_pool", database),
        patch("volundr.main.PostgresSessionRepository", return_value=repo),
        patch("volundr.main.seed_development_identity", new=AsyncMock()),
        patch(
            "volundr.adapters.outbound.bifrost_catalog_http.HttpBifrostCatalogAdapter.list_models",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "volundr.domain.services.tenant.TenantService.ensure_default_tenant", new=AsyncMock()
        ),
    ):
        app = create_app(settings)

        @app.get("/fixture/status")
        async def status():
            return {
                "backend": app.state.session_service._runtime_backend,
                "session": (await repo.get(session.id)).model_dump(mode="json"),
            }

        uvicorn.run(
            app,
            host="127.0.0.1",
            port=config["api_port"],
            access_log=False,
            ws="websockets-sansio",
            timeout_graceful_shutdown=2,
        )


if __name__ == "__main__":
    serve(Path(sys.argv[1]))
