"""Persistent local knowledge services using the host's service manager."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import plistlib
import re
import secrets
import shutil
import socket
import sys
from pathlib import Path

import httpx

from mimir.dream_results import dream_results
from mimir.ports.deployment import DeploymentRequest, KnowledgeDeploymentPort
from ravn.warden.deployment import build_warden_store
from ravn.warden.models import WardenMimirBinding, WardenSpec


class LocalKnowledgeDeploymentAdapter(KnowledgeDeploymentPort):
    def __init__(
        self,
        *,
        root: str = "~/.local/share/niuu/knowledge",
        gbrain_command: list[str] | None = None,
        gbrain_environment: dict[str, str] | None = None,
        warden_defaults: dict | None = None,
        database: dict | None = None,
        workspace: str = "",
        startup_timeout: float = 45,
        poll_interval: float = 0.5,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.gbrain_command = gbrain_command
        self.environment = gbrain_environment or {}
        self.warden_defaults = warden_defaults
        self.database = database
        self.workspace = Path(workspace or Path.cwd()).resolve()
        self.timeout = startup_timeout
        self.poll = poll_interval
        self.wardens = build_warden_store()
        self._ports: dict = {}
        self._lock = asyncio.Lock()

    def _records(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted(self.root.glob("*/deployment.json"))]

    def _record(self, name: str) -> dict:
        name = name.removeprefix("local/")
        return next((r for r in self._records() if r["name"] == name), None) or self._missing(name)

    def _missing(self, name: str):
        raise ValueError(f"Unknown local deployment: {name}")

    async def _run(self, *args: str, env: dict | None = None) -> str:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
        )
        out, err = await process.communicate()
        if process.returncode:
            detail = err.decode().strip() or f"{args[0]} exited {process.returncode}"
            for key, value in (env or {}).items():
                if value and any(
                    part in key for part in ("TOKEN", "PASSWORD", "API_KEY", "DATABASE_URL")
                ):
                    detail = detail.replace(value, "[redacted]")
            raise RuntimeError(detail)
        return out.decode()

    def _service_file(self, name: str) -> Path:
        if sys.platform == "darwin":
            return Path.home() / "Library/LaunchAgents" / f"dev.niuu.knowledge.{name}.plist"
        if sys.platform == "linux":
            return Path.home() / ".config/systemd/user" / f"niuu-knowledge-{name}.service"
        raise ValueError("Local deployments require macOS launchd or Linux systemd")

    async def _install(self, name: str, command: list[str], directory: Path) -> None:
        service = self._service_file(name)
        service.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            service.write_bytes(
                plistlib.dumps(
                    {
                        "Label": f"dev.niuu.knowledge.{name}",
                        "ProgramArguments": command,
                        "WorkingDirectory": str(self.workspace),
                        "RunAtLoad": True,
                        "KeepAlive": True,
                        "StandardOutPath": str(directory / "stdout.log"),
                        "StandardErrorPath": str(directory / "stderr.log"),
                        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "")},
                    }
                )
            )
            await self._run("launchctl", "load", "-w", str(service))
            return
        quoted = " ".join(
            '"' + arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
            for arg in command
        )
        service.write_text(
            f"[Unit]\nDescription=Niuu knowledge {name}\n[Service]\n"
            f"ExecStart={quoted}\nWorkingDirectory={self.workspace}\nRestart=on-failure\n"
            f"StandardOutput=append:{directory}/stdout.log\n"
            f"StandardError=append:{directory}/stderr.log\n"
            "[Install]\nWantedBy=default.target\n"
        )
        await self._run("systemctl", "--user", "daemon-reload")
        await self._run("systemctl", "--user", "enable", "--now", service.name)

    async def _view(self, record: dict) -> dict:
        ready = False
        message = record.get("error") or "Service is not responding"
        try:
            async with httpx.AsyncClient(timeout=self.poll + 1) as client:
                response = await client.get(record["health_url"])
                ready = response.is_success
                message = record.get("error") or (
                    "Running on this computer" if ready else f"HTTP {response.status_code}"
                )
        except httpx.HTTPError:
            pass  # A stopped service is an observed state, not a successful deployment.
        return {k: record[k] for k in ("name", "backend", "dream", "warden_id")} | {
            "ready": ready,
            "message": message,
            "target": "local",
            "can_delete": True,
            "url": record["url"],
        }

    async def list_deployments(self) -> dict:
        return {
            "cluster": "This computer",
            "namespace": "local",
            "target": "local",
            "backends": ["mimir", *(["gbrain"] if self.gbrain_command else [])],
            "warden_available": self.warden_defaults is not None,
            "dream_available": bool(self.database or self.environment.get("GBRAIN_DATABASE_URL")),
            "releases": [await self._view(r) for r in self._records()],
        }

    def mounted_ports(self) -> list[dict]:
        result = []
        for record in self._records():
            if not record.get("initialized"):
                continue
            name = record["name"]
            if name not in self._ports:
                cfg = record["mount"]
                module, cls = cfg["adapter"].rsplit(".", 1)
                self._ports[name] = getattr(importlib.import_module(module), cls)(**cfg["kwargs"])
            cfg = record["mount"]
            connection_kwargs = dict(cfg["kwargs"])
            if token := connection_kwargs.pop("api_token", None):
                token_path = self.root / name / "session-token"
                token_path.touch(mode=0o600, exist_ok=True)
                token_path.chmod(0o600)
                token_path.write_text(token)
                connection_kwargs["api_token_file"] = str(token_path)
            result.append(
                {
                    "connection": {"adapter": cfg["adapter"], "kwargs": connection_kwargs},
                    "name": name,
                    "role": "local",
                    "categories": None,
                    "priority": 0,
                    "port": self._ports[name],
                }
            )
        return result

    async def deploy(self, request: DeploymentRequest) -> dict:
        async with self._lock:
            return await self._deploy(request)

    async def _deploy(self, request: DeploymentRequest) -> dict:
        if request.warden and self.warden_defaults is None:
            raise ValueError("Configure the local warden model/profile before enabling a warden")
        if request.backend == "gbrain" and not self.gbrain_command:
            raise ValueError("The local target has no gbrain executable configured")
        if request.dream.enabled and (
            request.backend != "gbrain"
            or not (self.database or self.environment.get("GBRAIN_DATABASE_URL"))
        ):
            raise ValueError("Native dream scheduling requires gbrain with a PostgreSQL connection")
        directory = self.root / request.name
        if directory.exists():
            record = self._record(request.name)
            if record["backend"] != request.backend or not record.get("initialized"):
                raise ValueError(
                    f"A different or uninitialized deployment named {request.name} exists"
                )
            if record.get("warden_id") and request.warden_overrides.model_dump(exclude_none=True):
                raise ValueError(
                    "Warden overrides apply when attaching a new warden; "
                    "this instance already has one"
                )
            if request.warden and not record.get("warden_id"):
                cfg = record["mount"]
                binding = {"name": request.name, **cfg}
                await self._attach_warden(request, record, binding, directory / "deployment.json")
                record.pop("error", None)
                (directory / "deployment.json").write_text(json.dumps(record))
            return await self._view(record)
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        directory.chmod(0o700)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        record = {
            "name": request.name,
            "backend": request.backend,
            "url": url,
            "health_url": url + ("/mimir/health" if request.backend == "mimir" else "/health"),
            "dream": request.dream.model_dump(),
            "warden_id": None,
            "initialized": False,
        }
        config_file = directory / "deployment.json"
        config_file.write_text(json.dumps(record))
        config_file.chmod(0o600)
        database = None
        database_config = dict(self.database) if self.database else None
        if database_config:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                database_config.update(
                    listen_host="127.0.0.1",
                    listen_port=sock.getsockname()[1],
                    listen_password=secrets.token_urlsafe(32),
                )
        try:
            if request.backend == "mimir":
                data = directory / "data"
                record["mount"] = {
                    "adapter": "mimir.adapters.markdown.MarkdownMimirAdapter",
                    "kwargs": {"root": str(data)},
                }
                command = [
                    sys.executable,
                    "-m",
                    "mimir",
                    "serve",
                    "--path",
                    str(data),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--name",
                    request.name,
                ]
                binding = {"name": request.name, "path": str(data)}
            else:
                env = {**self.environment, "GBRAIN_HOME": str(directory)}
                if self.database:
                    from mimir.local_runtime import start_database

                    database, database_url = await start_database(database_config, directory)
                    env["GBRAIN_DATABASE_URL"] = database_url
                # Templates select a separate database for each named instance.
                env = {
                    key: value.replace("{name}", request.name.replace("-", "_"))
                    for key, value in env.items()
                }
                prefix = self.gbrain_command
                await self._run(
                    *prefix,
                    "init",
                    *(["--non-interactive"] if env.get("GBRAIN_DATABASE_URL") else ["--pglite"]),
                    "--no-embedding",
                    env={**os.environ, **env},
                )
                # The native local MCP listener is loopback-only. Reuse a configured scoped token.
                token_output = await self._run(
                    *prefix,
                    "auth",
                    "create",
                    "niuu-" + request.name,
                    "--scopes",
                    "read,write",
                    env={**os.environ, **env},
                )
                match = re.search(r"(?m)^\s+(gbrain_[A-Za-z0-9_-]+)\s*$", token_output)
                if match is None:
                    raise RuntimeError("gbrain did not return its scoped access token")
                token = match.group(1)
                kwargs = {"mcp_url": url + "/mcp", "api_token": token, "query_expansion": False}
                record["mount"] = {
                    "adapter": "ravn.adapters.mimir.gbrain.GBrainMimirAdapter",
                    "kwargs": kwargs,
                }
                binding = {
                    "name": request.name,
                    "adapter": record["mount"]["adapter"],
                    "kwargs": kwargs,
                }
                # Keep environment secrets in the private runner config.
                runner = directory / "runtime.json"
                runner.write_text(
                    json.dumps(
                        {
                            "database": database_config,
                            "command": self.gbrain_command,
                            "environment": env,
                            "port": port,
                            "dream": request.dream.model_dump(),
                        }
                    )
                )
                runner.chmod(0o600)
                command = [sys.executable, "-m", "mimir.local_runtime", str(runner)]
            if database is not None:
                await database.stop()
                database = None
            record["initialized"] = True
            config_file.write_text(json.dumps(record))
            await self._install(request.name, command, directory)
            async with asyncio.timeout(self.timeout):
                while not (await self._view(record))["ready"]:
                    await asyncio.sleep(self.poll)
            if request.warden:
                await self._attach_warden(request, record, binding, config_file)
            return await self._view(record)
        except Exception as exc:
            if database is not None:
                await database.stop()
            record["error"] = str(exc)
            config_file.write_text(json.dumps(record))
            raise

    async def _attach_warden(self, request, record, binding, config_file):
        spec = WardenSpec(
            **{
                **request.warden_overrides.apply(self.warden_defaults),
                "id": request.name + "-warden",
                "name": f"{request.name} warden",
                "autostart": True,
                "deployment": "launchd" if sys.platform == "darwin" else "systemd",
                "mimir": WardenMimirBinding(
                    mount_names=[request.name], instance_configs={request.name: binding}
                ),
            }
        )
        warden = await asyncio.to_thread(self.wardens.create, spec)
        record["warden_id"] = warden.id
        config_file.write_text(json.dumps(record))
        await asyncio.to_thread(self.wardens.install, warden.id, self.workspace)

    async def inspect_deployment(self, name: str) -> dict:
        record = self._record(name)
        directory = self.root / record["name"]
        logs = {}
        for stream in ("stdout", "stderr", "dream"):
            path = directory / f"{stream}.log"
            logs[stream] = ""
            if path.exists():
                with path.open("rb") as log:
                    log.seek(max(0, path.stat().st_size - 65536))
                    logs[stream] = log.read().decode(errors="replace")
        return {
            **await self._view(record),
            "logs": logs,
            "dream_results": dream_results(logs["dream"]),
        }

    async def control(self, name: str, action: str) -> dict:
        if action == "delete":
            return await self._delete(name)
        record = self._record(name)
        service = self._service_file(record["name"])
        if action not in {"start", "stop"}:
            raise ValueError("Supported actions: start, stop")
        if record.get("warden_id") and action == "stop":
            await asyncio.to_thread(self.wardens.stop, record["warden_id"])
        if sys.platform == "darwin":
            jobs = await self._run("launchctl", "list")
            label = f"dev.niuu.knowledge.{record['name']}"
            loaded = any(line.split() and line.split()[-1] == label for line in jobs.splitlines())
            if action == "start" and loaded:
                await self._run("launchctl", "kickstart", f"gui/{os.getuid()}/{label}")
            elif action == "start" or loaded:
                await self._run(
                    "launchctl", "load" if action == "start" else "unload", "-w", str(service)
                )
        else:
            await self._run("systemctl", "--user", action, service.name)
        if record.get("warden_id") and action == "start":
            await asyncio.to_thread(self.wardens.start, record["warden_id"])
        return await self._view(record)

    async def _delete(self, name: str) -> dict:
        async with self._lock:
            record = self._record(name)
            instance = record["name"]
            directory = self.root / instance
            if (
                not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", instance)
                or directory.resolve().parent != self.root.resolve()
            ):
                raise ValueError("Invalid local instance directory")
            service = self._service_file(instance)
            if record.get("warden_id"):
                await asyncio.to_thread(self.wardens.uninstall, record["warden_id"])
            pid = None
            if sys.platform == "darwin":
                jobs = await self._run("launchctl", "list")
                job = next(
                    (
                        line.split()
                        for line in jobs.splitlines()
                        if line.split() and line.split()[-1] == f"dev.niuu.knowledge.{instance}"
                    ),
                    None,
                )
                if job:
                    pid = int(job[0]) if job[0].isdigit() else None
                    await self._run("launchctl", "unload", "-w", str(service))
            elif service.exists():
                await self._run("systemctl", "--user", "disable", "--now", service.name)
            # launchd can return before the runner finishes stopping its database.
            async with asyncio.timeout(self.timeout):
                while pid:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    await asyncio.sleep(self.poll)
            database_pid = directory / "postgres/postmaster.pid"
            if database_pid.exists():
                raise RuntimeError(
                    "Local PostgreSQL has not shut down; instance data was preserved"
                )
            service.unlink(missing_ok=True)
            if sys.platform == "linux":
                await self._run("systemctl", "--user", "daemon-reload")
            if record.get("warden_id"):
                await asyncio.to_thread(self.wardens.delete, record["warden_id"])
            port = self._ports.pop(instance, None)
            close = getattr(port, "close", None)
            if close is not None:
                await close()
            await asyncio.to_thread(shutil.rmtree, directory)
            return {"name": instance, "deleted": True}
