"""Devrunner - hosts the session terminal next to the Skuld broker."""

import asyncio
import json
import logging
import os
import signal
import subprocess
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("devrunner")


class Devrunner:
    """Seeds the home directory and serves the terminal until it is told to stop."""

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir

    async def run(self) -> None:
        """Start the terminal server and serve it until SIGTERM or SIGINT."""
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

        # A session without a checkout starts with no workspace directory, and
        # every terminal window starts in it.
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)

        from terminal import TerminalServer

        terminal = TerminalServer(
            port=int(os.environ.get("TERMINAL_PORT", "7681")),
            workspace_dir=str(self.workspace_dir),
        )
        await terminal.start()
        logger.info("Devrunner started")

        await stop.wait()
        logger.info("Shutting down devrunner")
        await terminal.stop()
        logger.info("Devrunner shutdown complete")


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

    logger.info(f"Devrunner starting for session {session_id}")
    logger.info(f"Workspace: {workspace_dir}")

    runner = Devrunner(workspace_dir=workspace_dir)
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
