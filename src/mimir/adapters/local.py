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

from mimir.ports.deployment import DeploymentRequest, KnowledgeDeploymentPort


class LocalKnowledgeDeploymentAdapter(KnowledgeDeploymentPort):
    def __init__(
        self,
        *,
        root: str = "~/.local/share/niuu/knowledge",
        gbrain_command: list[str] | None = None,
        gbrain_environment: dict[str, str] | None = None,
        database: dict | None = None,
        workspace: str = "",
        startup_timeout: float = 45,
        poll_interval: float = 0.5,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.gbrain_command = gbrain_command
        self.environment = gbrain_environment or {}
        self.database = database
        self.workspace = Path(workspace or Path.cwd()).resolve()
        self.timeout = startup_timeout
        self.poll = poll_interval
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
        return {k: record[k] for k in ("name", "backend")} | {
            "ready": ready,
            "message": message,
            "target": "local",
            "access_scope": "tenant" if record.get("tenant_id") else "local",
            "can_delete": True,
            "url": record["url"],
        }

    async def list_deployments(self, *, tenant_id: str = "") -> dict:
        return {
            "cluster": "This computer",
            "namespace": "local",
            "target": "local",
            "backends": ["gbrain"] if self.gbrain_command else [],
            "releases": [
                await self._view(r) for r in self._records() if r.get("tenant_id", "") == tenant_id
            ],
        }

    def mounted_ports(self, tenant_id: str | None = None) -> list[dict]:
        result = []
        for record in self._records():
            if tenant_id is not None and record.get("tenant_id", "") != tenant_id:
                continue
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
                    "tenant_id": record.get("tenant_id", ""),
                    "access_scope": "tenant" if record.get("tenant_id") else "local",
                    "connection": {"adapter": cfg["adapter"], "kwargs": connection_kwargs},
                    "name": name,
                    "role": "local",
                    "categories": None,
                    "priority": 0,
                    "port": self._ports[name],
                }
            )
        return result

    async def discover_mounts(self, tenant_id: str, authorization: str = "") -> list[dict]:
        return self.mounted_ports(tenant_id)

    async def deploy(self, request: DeploymentRequest) -> dict:
        async with self._lock:
            return await self._deploy(request)

    async def _deploy(self, request: DeploymentRequest) -> dict:
        if request.backend == "gbrain" and not self.gbrain_command:
            raise ValueError("The local target has no gbrain executable configured")
        directory = self.root / request.name
        if directory.exists():
            record = self._record(request.name)
            if record.get("tenant_id", "") != request.tenant_id:
                raise ValueError("Knowledge instance not found in this tenant")
            if record["backend"] != request.backend or not record.get("initialized"):
                raise ValueError(
                    f"A different or uninitialized deployment named {request.name} exists"
                )
            return await self._view(record)
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        directory.chmod(0o700)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        record = {
            "tenant_id": request.tenant_id,
            "name": request.name,
            "backend": request.backend,
            "url": url,
            "health_url": url + "/health",
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
                "adapter": "mimir.adapters.gbrain.GBrainMimirAdapter",
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
            return await self._view(record)
        except Exception as exc:
            if database is not None:
                await database.stop()
            record["error"] = str(exc)
            config_file.write_text(json.dumps(record))
            raise

    async def inspect_deployment(self, name: str, *, tenant_id: str = "") -> dict:
        record = self._record(name)
        if record.get("tenant_id", "") != tenant_id:
            raise ValueError("Knowledge instance not found in this tenant")
        directory = self.root / record["name"]
        logs = {}
        for stream in ("stdout", "stderr"):
            path = directory / f"{stream}.log"
            logs[stream] = ""
            if path.exists():
                with path.open("rb") as log:
                    log.seek(max(0, path.stat().st_size - 65536))
                    logs[stream] = log.read().decode(errors="replace")
        return {**await self._view(record), "logs": logs}

    async def control(self, name: str, action: str, *, tenant_id: str = "") -> dict:
        if action == "delete":
            return await self._delete(name, tenant_id=tenant_id)
        record = self._record(name)
        if record.get("tenant_id", "") != tenant_id:
            raise ValueError("Knowledge instance not found in this tenant")
        service = self._service_file(record["name"])
        if action not in {"start", "stop"}:
            raise ValueError("Supported actions: start, stop")
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
        return await self._view(record)

    async def _delete(self, name: str, *, tenant_id: str = "") -> dict:
        async with self._lock:
            record = self._record(name)
            if record.get("tenant_id", "") != tenant_id:
                raise ValueError("Knowledge instance not found in this tenant")
            instance = record["name"]
            directory = self.root / instance
            if (
                not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", instance)
                or directory.resolve().parent != self.root.resolve()
            ):
                raise ValueError("Invalid local instance directory")
            service = self._service_file(instance)
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
            port = self._ports.pop(instance, None)
            close = getattr(port, "close", None)
            if close is not None:
                await close()
            await asyncio.to_thread(shutil.rmtree, directory)
            return {"name": instance, "deleted": True}
