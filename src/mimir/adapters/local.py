"""Persistent local knowledge services using the host's service manager.

It also provisions the memory gateway's per-org memory systems on this computer
(``provision_memory_system``): one gbrain service per org under ``<root>/.memory/<key
of the org>/`` (a name no knowledge instance can take), initialised database-only,
supervised by launchd or systemd like a knowledge instance. That directory is the
registry: ``memory.json`` names the org and says whether the system is ready, and the
owner bootstrap token (``admin-token``) and signing key (``signing-key``) are 0600
files created once and kept. Provisioning runs in the background and holds an
exclusive lock on ``<key>.lock``, so concurrent first requests, from this process or
another on this computer, provision once.
"""

from __future__ import annotations

import asyncio
import fcntl
import importlib
import json
import logging
import os
import plistlib
import re
import secrets
import shutil
import socket
import sys
from pathlib import Path

import httpx

from mimir.ports.deployment import (
    DeploymentRequest,
    KnowledgeDeploymentPort,
    MemoryProvisioningError,
    MemorySystemPhase,
    MemorySystemRecord,
    memory_system_key,
    memory_system_name,
)

logger = logging.getLogger(__name__)

_MEMORY_DIR = ".memory"
_MEMORY_RECORD = "memory.json"
_ADMIN_TOKEN_FILE = "admin-token"
_SIGNING_KEY_FILE = "signing-key"
#: Random bytes of a new owner token or signing key (64 URL-safe characters).
_SECRET_BYTES = 48


def _private_secret(path: Path) -> str:
    """The secret in *path*, created once (0600) when the file does not exist yet."""
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(secrets.token_urlsafe(_SECRET_BYTES))
    value = path.read_text().strip()
    if not value:
        raise MemoryProvisioningError(f"{path} is empty; restore it from a backup")
    return value


class LocalKnowledgeDeploymentAdapter(KnowledgeDeploymentPort):
    provisions_memory_systems = True

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
        self._memory_tasks: dict[str, asyncio.Task[None]] = {}

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

    def _service_file(self, name: str, kind: str = "knowledge") -> Path:
        if sys.platform == "darwin":
            return Path.home() / "Library/LaunchAgents" / f"dev.niuu.{kind}.{name}.plist"
        if sys.platform == "linux":
            return Path.home() / ".config/systemd/user" / f"niuu-{kind}-{name}.service"
        raise ValueError("Local deployments require macOS launchd or Linux systemd")

    async def _install(
        self, name: str, command: list[str], directory: Path, kind: str = "knowledge"
    ) -> None:
        service = self._service_file(name, kind)
        service.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            service.write_bytes(
                plistlib.dumps(
                    {
                        "Label": f"dev.niuu.{kind}.{name}",
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
            f"[Unit]\nDescription=Niuu {kind} {name}\n[Service]\n"
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

    # -- memory systems --------------------------------------------------

    def _memory_dir(self, tenant_id: str) -> Path:
        return self.root / _MEMORY_DIR / memory_system_key(tenant_id)

    def _memory_lock(self, tenant_id: str) -> int | None:
        """An exclusive, non-blocking lock on the org's provisioning, or None when
        another process holds it. The caller closes the descriptor to release it."""
        parent = self.root / _MEMORY_DIR
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            parent / f"{memory_system_key(tenant_id)}.lock", os.O_RDWR | os.O_CREAT, 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return None
        return descriptor

    def _read_memory_record(self, tenant_id: str) -> dict | None:
        path = self._memory_dir(tenant_id) / _MEMORY_RECORD
        if not path.exists():
            return None
        record = json.loads(path.read_text())
        if record.get("tenant_id") != tenant_id:
            raise MemoryProvisioningError(
                f"{path} belongs to org {record.get('tenant_id')!r}, not {tenant_id!r}; the two "
                "orgs' memory-system keys collide. Move one of them to memory.tenants"
            )
        return record

    def _write_memory_record(self, tenant_id: str, record: dict) -> None:
        path = self._memory_dir(tenant_id) / _MEMORY_RECORD
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record))
        temporary.chmod(0o600)
        temporary.replace(path)

    def _memory_view(
        self, tenant_id: str, record: dict, *, interrupted: bool, started: bool = False
    ) -> MemorySystemRecord:
        name = memory_system_name(tenant_id)
        directory = self._memory_dir(tenant_id)
        if record.get("error"):
            return MemorySystemRecord(
                tenant_id=tenant_id,
                name=name,
                phase=MemorySystemPhase.FAILED,
                message=(
                    f"{record['error']} (logs in {directory}; remove that directory to "
                    "provision it again)"
                ),
            )
        if record.get("ready"):
            return MemorySystemRecord(
                tenant_id=tenant_id,
                name=name,
                phase=MemorySystemPhase.READY,
                message="running on this computer",
                mcp_url=record["url"] + "/mcp",
                admin_token=_private_secret(directory / _ADMIN_TOKEN_FILE),
                signing_key=_private_secret(directory / _SIGNING_KEY_FILE),
            )
        if interrupted:
            return MemorySystemRecord(
                tenant_id=tenant_id,
                name=name,
                phase=MemorySystemPhase.FAILED,
                message=(
                    f"provisioning in {directory} stopped before it finished (the process "
                    "that ran it ended); remove that directory to provision it again"
                ),
            )
        return MemorySystemRecord(
            tenant_id=tenant_id,
            name=name,
            phase=MemorySystemPhase.PREPARING,
            message="starting its gbrain on this computer",
            started=started,
        )

    def _in_flight(self, tenant_id: str) -> bool:
        task = self._memory_tasks.get(tenant_id)
        return task is not None and not task.done()

    async def provision_memory_system(self, tenant_id: str) -> MemorySystemRecord:
        if not self.gbrain_command:
            raise MemoryProvisioningError(
                "the local target has no gbrain executable configured (gbrain_command)"
            )
        if self._in_flight(tenant_id):
            return self._memory_view(tenant_id, {}, interrupted=False)
        record = self._read_memory_record(tenant_id)
        if record is not None and (record.get("ready") or record.get("error")):
            return self._memory_view(tenant_id, record, interrupted=False)
        lock = self._memory_lock(tenant_id)
        if lock is None:
            # Another process on this computer is provisioning it.
            return self._memory_view(tenant_id, record or {}, interrupted=False)
        try:
            record = self._read_memory_record(tenant_id)
            if record is not None:
                # Under the lock and not in flight anywhere: an earlier attempt ended
                # half-way. Never resumed over: its directory may hold a started database.
                os.close(lock)
                return self._memory_view(tenant_id, record, interrupted=True)
            record = self._begin_memory(tenant_id)
        except BaseException:
            os.close(lock)
            raise
        task = asyncio.create_task(
            self._provision_memory(tenant_id, record),
            name=f"provision-memory-{memory_system_key(tenant_id)}",
        )
        # Released when the task ends however it ends, even cancelled before it ran.
        task.add_done_callback(lambda _: os.close(lock))
        self._memory_tasks[tenant_id] = task
        return self._memory_view(tenant_id, record, interrupted=False, started=True)

    async def memory_system(self, tenant_id: str) -> MemorySystemRecord | None:
        if self._in_flight(tenant_id):
            return self._memory_view(tenant_id, {}, interrupted=False)
        record = self._read_memory_record(tenant_id)
        if record is None:
            return None
        if record.get("ready") or record.get("error"):
            return self._memory_view(tenant_id, record, interrupted=False)
        lock = self._memory_lock(tenant_id)
        if lock is None:
            return self._memory_view(tenant_id, record, interrupted=False)
        os.close(lock)
        return self._memory_view(tenant_id, record, interrupted=True)

    async def memory_systems(self) -> list[MemorySystemRecord]:
        records = []
        for path in sorted((self.root / _MEMORY_DIR).glob(f"*/{_MEMORY_RECORD}")):
            tenant_id = json.loads(path.read_text()).get("tenant_id", "")
            if not tenant_id or path.parent.name != memory_system_key(tenant_id):
                raise MemoryProvisioningError(
                    f"{path} names no org whose key is {path.parent.name}; it was not made "
                    "by the gateway"
                )
            found = await self.memory_system(tenant_id)
            if found is not None:
                records.append(found)
        return records

    def _begin_memory(self, tenant_id: str) -> dict:
        """The org's directory, secrets and first record, made under its lock."""
        directory = self._memory_dir(tenant_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        directory.chmod(0o700)
        _private_secret(directory / _ADMIN_TOKEN_FILE)
        _private_secret(directory / _SIGNING_KEY_FILE)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        record = {
            "tenant_id": tenant_id,
            "name": memory_system_name(tenant_id),
            "backend": "gbrain",
            "url": url,
            "health_url": url + "/health",
            "port": port,
            "ready": False,
        }
        self._write_memory_record(tenant_id, record)
        return record

    async def _provision_memory(self, tenant_id: str, record: dict) -> None:
        """Initialise the org's gbrain database-only, install its service, wait for it.

        Runs in the background while its caller holds the org's lock. A failure is written into the
        record (and so answered as FAILED, with the remedy) and logged at error.
        """
        directory = self._memory_dir(tenant_id)
        key = memory_system_key(tenant_id)
        database = None
        try:
            env = {
                **self.environment,
                "GBRAIN_HOME": str(directory),
                "GBRAIN_ADMIN_BOOTSTRAP_TOKEN": _private_secret(directory / _ADMIN_TOKEN_FILE),
            }
            database_config = dict(self.database) if self.database else None
            if database_config:
                from mimir.local_runtime import start_database

                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    database_config.update(
                        listen_host="127.0.0.1",
                        listen_port=sock.getsockname()[1],
                        listen_password=secrets.token_urlsafe(32),
                    )
                database, env["GBRAIN_DATABASE_URL"] = await start_database(
                    database_config, directory
                )
            # Templates select a separate database for each org.
            env = {k: v.replace("{name}", f"memory_{key}") for k, v in env.items()}
            await self._run(
                *self.gbrain_command,
                "init",
                *(["--non-interactive"] if env.get("GBRAIN_DATABASE_URL") else ["--pglite"]),
                "--db-only",
                "--no-embedding",
                env={**os.environ, **env},
            )
            if database is not None:
                await database.stop()
                database = None
            runner = directory / "runtime.json"
            runner.write_text(
                json.dumps(
                    {
                        "database": database_config,
                        "command": self.gbrain_command,
                        "environment": env,
                        "port": record["port"],
                    }
                )
            )
            runner.chmod(0o600)
            command = [sys.executable, "-m", "mimir.local_runtime", str(runner)]
            await self._install(key, command, directory, kind="memory")
            async with asyncio.timeout(self.timeout):
                while not await self._answers(record["health_url"]):
                    await asyncio.sleep(self.poll)
            record["ready"] = True
            self._write_memory_record(tenant_id, record)
            logger.info(
                "memory: provisioned the memory system of org %r in %s", tenant_id, directory
            )
        except Exception as exc:  # recorded and answered as FAILED with its remedy
            if database is not None:
                try:
                    await database.stop()
                except Exception as stop_error:  # the original failure is the one recorded
                    logger.error(
                        "memory: the database of org %r's failed provisioning did not stop: %s",
                        tenant_id,
                        stop_error,
                    )
            message = (
                f"gbrain did not answer within startup_timeout={self.timeout:g} s"
                if isinstance(exc, TimeoutError)
                else f"{type(exc).__name__}: {exc}"
            )
            record["error"] = message
            self._write_memory_record(tenant_id, record)
            logger.error(
                "memory: provisioning the memory system of org %r failed: %s", tenant_id, message
            )

    async def _answers(self, url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self.poll + 1) as client:
                return (await client.get(url)).is_success
        except httpx.HTTPError:
            return False  # not up yet; the startup timeout bounds the wait

    async def close(self) -> None:
        """Cancel provisioning in flight and wait until each attempt has stopped."""
        tasks = list(self._memory_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
