"""Devrunner - manages PostgreSQL, watches config.yaml, runs user services, generates nginx.conf."""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("devrunner")


@dataclass
class ServiceSpec:
    """Service definition from config.yaml."""

    name: str
    command: str
    port: int
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    restart_policy: str = "on-failure"
    max_restarts: int = 5


@dataclass
class ServiceState:
    """Runtime state for a managed service."""

    spec: ServiceSpec
    process: asyncio.subprocess.Process | None = None
    restart_count: int = 0
    started_at: float = 0.0
    last_error: str | None = None


class Devrunner:
    """Watches config.yaml, manages PostgreSQL and user services, generates nginx.conf."""

    def __init__(self, services_dir: Path, workspace_dir: Path):
        self.services_dir = services_dir
        self.workspace_dir = workspace_dir
        self.config_path = services_dir / "config.yaml"
        self.nginx_conf_path = services_dir / "nginx.conf"
        self.logs_dir = services_dir / "logs"
        self.status_dir = services_dir / "status"
        self.pg_data_dir = services_dir / "pgdata"
        self._services: dict[str, ServiceState] = {}
        self._postgres: asyncio.subprocess.Process | None = None
        self._shutting_down = False
        self._monitor_task: asyncio.Task | None = None
        self._terminal = None  # TerminalServer instance, lazily imported

    async def run(self) -> None:
        """Main entry point - start postgres, watch config, reconcile services."""
        # Seed persistent home directory (dotfiles, oh-my-zsh, Homebrew)
        init_home = Path("/usr/local/bin/init-home.sh")
        if init_home.exists():
            logger.info("Running init-home.sh to seed persistent home directory")
            result = subprocess.run(
                [str(init_home)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                logger.warning(f"init-home.sh failed: {result.stderr}")
            else:
                logger.info("Home directory initialized")

        self.services_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)
        self.status_dir.mkdir(exist_ok=True)

        # Write empty nginx config initially
        self._write_nginx_conf({})

        # Register signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        # Start terminal server
        terminal_port = int(os.environ.get("TERMINAL_PORT", "7681"))
        from terminal import TerminalServer

        self._terminal = TerminalServer(
            port=terminal_port,
            workspace_dir=str(self.workspace_dir),
        )
        await self._terminal.start()

        # Start PostgreSQL
        await self._start_postgres()

        # Start service monitor
        self._monitor_task = asyncio.create_task(self._monitor_services())

        # Main config watch loop
        logger.info("Devrunner started, watching for config changes")
        await self._watch_config()

    async def shutdown(self) -> None:
        """Graceful shutdown - stop all services and postgres."""
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("Shutting down devrunner")

        if self._monitor_task:
            self._monitor_task.cancel()

        # Stop all user services
        for name in list(self._services.keys()):
            await self._stop_service(name)

        # Stop terminal server
        if self._terminal:
            await self._terminal.stop()

        # Stop PostgreSQL
        await self._stop_postgres()

        logger.info("Devrunner shutdown complete")
        sys.exit(0)

    # --- PostgreSQL Management ---

    async def _start_postgres(self) -> None:
        """Initialize and start PostgreSQL with data in workspace."""
        pg_bin = self._find_pg_bin()
        if not pg_bin:
            logger.warning("PostgreSQL binaries not found, skipping postgres")
            return

        log_file = self.logs_dir / "postgres.log"

        if not self.pg_data_dir.exists():
            logger.info("Initializing PostgreSQL database cluster")
            proc = await asyncio.create_subprocess_exec(
                str(pg_bin / "initdb"),
                "-D",
                str(self.pg_data_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            await proc.wait()
            if proc.returncode != 0:
                logger.error("Failed to initialize PostgreSQL")
                return

            # Configure pg_hba.conf for local trust auth
            hba_conf = self.pg_data_dir / "pg_hba.conf"
            hba_conf.write_text(
                "# Allow local connections without password\n"
                "local   all   all                 trust\n"
                "host    all   all   127.0.0.1/32   trust\n"
                "host    all   all   ::1/128        trust\n"
            )

            # Configure postgresql.conf for lightweight usage
            pg_conf = self.pg_data_dir / "postgresql.conf"
            with open(pg_conf, "a") as f:
                f.write("\n# Devrunner configuration\n")
                f.write("listen_addresses = '127.0.0.1'\n")
                f.write("port = 5432\n")
                f.write("unix_socket_directories = '/run/postgresql'\n")
                f.write("shared_buffers = 128MB\n")
                f.write("max_connections = 20\n")
                f.write("logging_collector = off\n")

        logger.info("Starting PostgreSQL")
        with open(log_file, "a") as lf:
            self._postgres = await asyncio.create_subprocess_exec(
                str(pg_bin / "postgres"),
                "-D",
                str(self.pg_data_dir),
                stdout=lf,
                stderr=asyncio.subprocess.STDOUT,
            )

        # Wait for postgres to be ready
        for _ in range(30):
            result = subprocess.run(
                [str(pg_bin / "pg_isready"), "-h", "127.0.0.1", "-p", "5432"],
                capture_output=True,
            )
            if result.returncode == 0:
                logger.info("PostgreSQL is ready")
                self._write_status("postgres", {"status": "running", "port": 5432})
                return
            await asyncio.sleep(1)

        logger.error("PostgreSQL failed to start within 30 seconds")

    async def _stop_postgres(self) -> None:
        """Stop PostgreSQL gracefully."""
        if not self._postgres:
            return

        logger.info("Stopping PostgreSQL")
        self._postgres.terminate()
        try:
            await asyncio.wait_for(self._postgres.wait(), timeout=10.0)
        except TimeoutError:
            logger.warning("PostgreSQL did not terminate, killing")
            self._postgres.kill()
            await self._postgres.wait()

        self._postgres = None
        self._write_status("postgres", {"status": "stopped"})

    def _find_pg_bin(self) -> Path | None:
        """Find PostgreSQL binary directory."""
        candidates = [
            Path("/usr/lib/postgresql/16/bin"),
            Path("/usr/lib/postgresql/15/bin"),
            Path("/usr/bin"),
        ]
        for path in candidates:
            if (path / "postgres").exists():
                return path
        return None

    # --- Service Management ---

    async def _start_service(self, spec: ServiceSpec) -> None:
        """Start a user service."""
        name = spec.name
        logger.info(f"Starting service: {name} (port {spec.port})")

        log_path = self.logs_dir / f"{name}.log"
        cwd = spec.cwd or str(self.workspace_dir)

        env = {**os.environ, **spec.env}
        env["PORT"] = str(spec.port)

        with open(log_path, "a") as lf:
            lf.write(f"\n--- Service {name} starting at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            lf.flush()

            proc = await asyncio.create_subprocess_shell(
                spec.command,
                cwd=cwd,
                env=env,
                stdout=lf,
                stderr=asyncio.subprocess.STDOUT,
            )

        state = ServiceState(
            spec=spec,
            process=proc,
            started_at=time.time(),
        )
        self._services[name] = state

        self._write_status(
            name,
            {
                "status": "running",
                "pid": proc.pid,
                "port": spec.port,
                "command": spec.command,
                "started_at": state.started_at,
            },
        )

        logger.info(f"Service {name} started with PID {proc.pid}")

    async def _stop_service(self, name: str) -> None:
        """Stop a user service."""
        state = self._services.get(name)
        if not state or not state.process:
            self._services.pop(name, None)
            return

        logger.info(f"Stopping service: {name}")
        state.process.terminate()
        try:
            await asyncio.wait_for(state.process.wait(), timeout=5.0)
        except TimeoutError:
            logger.warning(f"Service {name} did not terminate, killing")
            state.process.kill()
            await state.process.wait()

        self._services.pop(name, None)
        self._write_status(name, {"status": "stopped"})
        logger.info(f"Service {name} stopped")

    async def _reconcile(self) -> None:
        """Reconcile running services with config."""
        config = self._load_config()
        desired_services = config.get("services", {})
        desired_names = set(desired_services.keys())
        running_names = set(self._services.keys())

        # Stop removed services
        for name in running_names - desired_names:
            await self._stop_service(name)

        # Start new services
        for name in desired_names - running_names:
            svc_config = desired_services[name]
            spec = ServiceSpec(
                name=name,
                command=svc_config["command"],
                port=svc_config["port"],
                cwd=svc_config.get("cwd", ""),
                env=svc_config.get("env", {}),
                restart_policy=svc_config.get("restart_policy", "on-failure"),
                max_restarts=svc_config.get("max_restarts", 5),
            )
            await self._start_service(spec)

        # Update services whose config has changed
        for name in desired_names & running_names:
            svc_config = desired_services[name]
            state = self._services[name]
            if state.spec.command != svc_config["command"] or state.spec.port != svc_config["port"]:
                logger.info(f"Service {name} config changed, restarting")
                await self._stop_service(name)
                spec = ServiceSpec(
                    name=name,
                    command=svc_config["command"],
                    port=svc_config["port"],
                    cwd=svc_config.get("cwd", ""),
                    env=svc_config.get("env", {}),
                    restart_policy=svc_config.get("restart_policy", "on-failure"),
                    max_restarts=svc_config.get("max_restarts", 5),
                )
                await self._start_service(spec)

        # Generate nginx config
        self._write_nginx_conf(desired_services)

    async def _monitor_services(self) -> None:
        """Background task to monitor services and handle restarts."""
        while not self._shutting_down:
            await asyncio.sleep(3)

            for name, state in list(self._services.items()):
                if not state.process:
                    continue

                if state.process.returncode is None:
                    continue

                # Process has exited
                exit_code = state.process.returncode
                logger.warning(f"Service {name} exited with code {exit_code}")

                if state.spec.restart_policy == "never":
                    self._write_status(
                        name,
                        {
                            "status": "exited",
                            "exit_code": exit_code,
                        },
                    )
                    continue

                if state.restart_count >= state.spec.max_restarts:
                    logger.error(
                        f"Service {name} exceeded max restarts ({state.spec.max_restarts})"
                    )
                    self._write_status(
                        name,
                        {
                            "status": "failed",
                            "exit_code": exit_code,
                            "restart_count": state.restart_count,
                        },
                    )
                    continue

                # Restart the service
                state.restart_count += 1
                logger.info(f"Restarting service {name} (attempt {state.restart_count})")
                await self._stop_service(name)
                await self._start_service(state.spec)
                self._services[name].restart_count = state.restart_count

    # --- Config Watching ---

    async def _watch_config(self) -> None:
        """Watch config.yaml for changes using inotify."""
        # Do initial reconcile
        if self.config_path.exists():
            await self._reconcile()

        while not self._shutting_down:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "inotifywait",
                    "-e",
                    "modify,create,delete",
                    "-q",
                    str(self.config_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.wait()

                if self._shutting_down:
                    break

                # Small delay for writes to complete
                await asyncio.sleep(0.5)
                await self._reconcile()

            except FileNotFoundError:
                # inotifywait not available, fall back to polling
                logger.warning("inotifywait not available, falling back to polling")
                await self._poll_config()
                break
            except Exception:
                logger.exception("Error watching config")
                await asyncio.sleep(2)

    async def _poll_config(self) -> None:
        """Fallback: poll config.yaml for changes."""
        last_mtime = 0.0
        while not self._shutting_down:
            await asyncio.sleep(2)
            try:
                if not self.config_path.exists():
                    continue
                mtime = self.config_path.stat().st_mtime
                if mtime != last_mtime:
                    last_mtime = mtime
                    await self._reconcile()
            except Exception:
                logger.exception("Error polling config")

    # --- Nginx Config Generation ---

    def _write_nginx_conf(self, services: dict) -> None:
        """Generate nginx config fragment for dynamic services."""
        lines = ["# Auto-generated by devrunner - do not edit manually"]

        for name, svc in services.items():
            port = svc.get("port", svc.get("port"))
            if not port:
                continue

            path = svc.get("path", f"/svc/{name}")
            # Ensure path starts with /svc/
            if not path.startswith("/svc/"):
                path = f"/svc/{path.lstrip('/')}"

            lines.append("")
            lines.append(f"# Service: {name}")
            lines.append(f"location {path}/ {{")
            lines.append(f"    proxy_pass http://127.0.0.1:{port}/;")
            lines.append("    proxy_http_version 1.1;")
            lines.append("    proxy_set_header Upgrade $http_upgrade;")
            lines.append('    proxy_set_header Connection "upgrade";')
            lines.append("    proxy_set_header Host $host;")
            lines.append("    proxy_set_header X-Real-IP $remote_addr;")
            lines.append("    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;")
            lines.append("    proxy_read_timeout 3600s;")
            lines.append("    proxy_send_timeout 3600s;")
            lines.append("}")

        content = "\n".join(lines) + "\n"
        self.nginx_conf_path.write_text(content)
        logger.info(f"Nginx config updated with {len(services)} service(s)")

    # --- Helpers ---

    def _load_config(self) -> dict:
        """Load config.yaml."""
        if not self.config_path.exists():
            return {}

        try:
            with open(self.config_path) as f:
                return yaml.safe_load(f) or {}
        except Exception:
            logger.exception("Error loading config.yaml")
            return {}

    def _write_status(self, name: str, status: dict) -> None:
        """Write service status to status directory."""
        status_file = self.status_dir / f"{name}.json"
        status["updated_at"] = time.time()
        status_file.write_text(json.dumps(status))

    def _read_status(self, name: str) -> dict:
        """Read service status."""
        status_file = self.status_dir / f"{name}.json"
        if not status_file.exists():
            return {"status": "unknown"}

        try:
            return json.loads(status_file.read_text())
        except Exception:
            return {"status": "unknown"}


def _source_secrets() -> None:
    """Read /run/secrets/manifest.json and inject secrets into env/filesystem."""
    manifest_path = Path("/run/secrets/manifest.json")
    secret_dir = Path("/run/secrets/user")
    if not manifest_path.exists() or not secret_dir.is_dir():
        return

    manifest = json.loads(manifest_path.read_text())
    for env_var, spec in manifest.get("env", {}).items():
        fpath = secret_dir / spec["file"]
        if not fpath.exists():
            continue
        data = json.loads(fpath.read_text())
        os.environ[env_var] = data.get(spec.get("key", ""), "")

    for target, spec in manifest.get("files", {}).items():
        src = secret_dir / spec["file"]
        if src.exists():
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            if not Path(target).exists():
                Path(target).symlink_to(src)

    logger.info("Secrets sourced from manifest (%d env, %d files)",
                len(manifest.get("env", {})), len(manifest.get("files", {})))


def _source_env_file() -> None:
    """Source env vars rendered by the configured secret injector."""
    env_file = Path("/run/secrets/env.sh")
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Parse "export KEY='VALUE'" lines
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        if not key:
            continue
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ[key] = value
    logger.info("Sourced env vars from %s", env_file)


def _cleanup_agent_token() -> None:
    """Remove leftover Infisical agent access token (no longer needed after init)."""
    token_path = Path("/home/.infisical-workdir/identity-access-token")
    if token_path.exists():
        token_path.unlink()
        logger.info("Removed Infisical agent token at %s", token_path)


async def main() -> None:
    """Entry point."""
    _source_secrets()
    _source_env_file()
    _cleanup_agent_token()
    session_id = os.environ.get("SESSION_ID", "unknown")
    mount_path = os.environ.get("PERSISTENCE_MOUNT_PATH", "/volundr/sessions")
    workspace_dir = Path(
        os.environ.get(
            "WORKSPACE_DIR",
            f"{mount_path}/{session_id}/workspace",
        )
    )
    services_dir = workspace_dir / ".services"

    logger.info(f"Devrunner starting for session {session_id}")
    logger.info(f"Workspace: {workspace_dir}")
    logger.info(f"Services dir: {services_dir}")

    runner = Devrunner(services_dir=services_dir, workspace_dir=workspace_dir)
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
