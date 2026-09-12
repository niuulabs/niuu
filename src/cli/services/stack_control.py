"""Stack controller for docker mode: stage, apply and observe bundle changes.

Runs inside the platform container. ``niuu up`` leaves the effective bundle
settings in ``stack.yaml`` under the data directory; the wizard stages a
validated change set into ``stack-staged.yaml``; ``apply`` folds it into
``stack-overrides.yaml`` (which ``niuu up`` honours on the host too),
re-renders the compose bundle into the mounted compose directory, and runs
``docker compose up -d`` from a short-lived applier container so the platform
container can be recreated underneath it without killing the command.

Uses the dynamic adapter pattern (plain ``**kwargs`` constructor).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import docker
import yaml
from docker.errors import ImageNotFound, NotFound

from cli.config import CLISettings
from cli.services.compose_bundle import (
    APPLY_STATE_FILE,
    HOST_FACTS_FILE,
    STACK_FILE,
    STACK_OVERRIDES_FILE,
    STACK_STAGED_FILE,
    bundle_paths,
    compose_args,
    load_stack_settings,
    write_bundle,
    write_stack_overrides,
)
from cli.services.docker_host import GpuFacts, HostFacts
from cli.services.model_catalog import model_options
from niuu.domain.stack import (
    ApplyStatus,
    StackSettings,
    StackView,
    VllmSettings,
    VllmStatus,
    deep_merge,
    validate_stack_changes,
)
from niuu.ports.stack_control import StackControlPort

logger = logging.getLogger(__name__)

LABEL_MANAGED_BY = "niuu.managed-by"
MANAGED_BY = "docker_container"
LABEL_STACK_APPLY = "niuu.io/stack-apply"
DOCKER_SOCKET = "/var/run/docker.sock"
DEFAULT_LOG_TAIL = 40
MIB_PER_GIB = 1024


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def stack_settings_view(settings: CLISettings, external_host: str) -> StackSettings:
    vllm = settings.docker.vllm
    return StackSettings(
        bind_host=settings.docker.bind_host,
        external_host=external_host,
        port=settings.server.port,
        project_name=settings.docker.project_name,
        skuld_image=settings.docker.skuld_image,
        vllm=VllmSettings(
            enabled=vllm.enabled,
            model=vllm.model,
            image=vllm.image,
            max_model_len=vllm.max_model_len,
            gpu_memory_utilization=vllm.gpu_memory_utilization,
        ),
    )


class DockerStackController(StackControlPort):
    """Stage and apply bundle changes on the host this platform runs on.

    Args:
        stack_dir: Directory holding ``stack.yaml`` and the wizard's files
            (the data directory ``niuu up`` uses).
        docker_base_url: Docker daemon URL; empty uses the environment.
        docker_socket: Socket path bound into the applier container.
        log_tail: How many applier log lines to keep in a failure detail.
    """

    def __init__(
        self,
        *,
        stack_dir: str,
        docker_base_url: str = "",
        docker_socket: str = DOCKER_SOCKET,
        log_tail: int = DEFAULT_LOG_TAIL,
        **_extra: object,
    ) -> None:
        self._dir = Path(stack_dir).expanduser()
        self._socket = str(docker_socket)
        self._log_tail = int(log_tail)
        self._client = (
            docker.DockerClient(base_url=str(docker_base_url))
            if str(docker_base_url).strip()
            else docker.from_env()
        )

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    @property
    def _stack_file(self) -> Path:
        return self._dir / STACK_FILE

    @property
    def _overrides_file(self) -> Path:
        return self._dir / STACK_OVERRIDES_FILE

    @property
    def _staged_file(self) -> Path:
        return self._dir / STACK_STAGED_FILE

    @property
    def _apply_state_file(self) -> Path:
        return self._dir / APPLY_STATE_FILE

    def _host_facts(self) -> HostFacts:
        path = self._dir / HOST_FACTS_FILE
        if not path.exists():
            raise FileNotFoundError(
                f"{path} is missing; start the platform with `niuu up` so the stack "
                "controller knows how the host was published"
            )
        raw = json.loads(path.read_text())
        raw["gpus"] = [GpuFacts(**gpu) for gpu in raw.get("gpus", [])]
        return HostFacts(**raw)

    def _current_settings(self) -> CLISettings:
        if not self._stack_file.exists():
            raise FileNotFoundError(
                f"{self._stack_file} is missing; `niuu up` writes it. Stack changes are "
                "only available on installs started that way"
            )
        return load_stack_settings(self._stack_file, self._overrides_file)

    def _staged(self) -> dict[str, Any]:
        return _read_yaml(self._staged_file)

    def _effective_settings(self) -> CLISettings:
        current = self._current_settings()
        staged = self._staged()
        if not staged:
            return current
        return CLISettings.model_validate(deep_merge(current.model_dump(mode="json"), staged))

    def _external_host(self, current: CLISettings) -> str:
        paths = bundle_paths(current)
        if paths.env_file.exists():
            for line in paths.env_file.read_text().splitlines():
                if line.startswith("NIUU_EXTERNAL_HOST="):
                    return line.split("=", 1)[1].strip()
        return self._host_facts().external_host

    # ------------------------------------------------------------------
    # StackControlPort
    # ------------------------------------------------------------------

    async def view(self) -> StackView:
        current = self._current_settings()
        effective = self._effective_settings()
        external_host = self._external_host(current)
        facts = self._host_facts()
        memory_gib = max((gpu.memory_total_mib for gpu in facts.gpus), default=0) // MIB_PER_GIB
        return StackView(
            current=stack_settings_view(current, external_host),
            staged=self._staged(),
            effective=stack_settings_view(effective, external_host),
            models=model_options(memory_gib),
            accelerator_memory_gib=memory_gib,
        )

    async def stage(self, changes: dict[str, Any]) -> StackView:
        validated = validate_stack_changes(changes)
        merged = deep_merge(self._staged(), validated)
        # Validate the merged result renders as settings before keeping it.
        CLISettings.model_validate(
            deep_merge(self._current_settings().model_dump(mode="json"), merged)
        )
        _write_yaml(self._staged_file, merged)
        return await self.view()

    async def discard(self) -> StackView:
        if self._staged_file.exists():
            self._staged_file.unlink()
        return await self.view()

    async def apply(self) -> ApplyStatus:
        staged = self._staged()
        if not staged:
            raise ValueError("Nothing is staged; stage a change before applying")
        status = await self.status()
        if status.state == "applying":
            raise ValueError("An apply is already running; wait for it to finish")

        current = self._current_settings()
        external_host = self._external_host(current)
        effective = CLISettings.model_validate(deep_merge(current.model_dump(mode="json"), staged))
        facts = replace(
            self._host_facts(),
            bind_host=effective.docker.bind_host,
            external_host=external_host,
            port=effective.server.port,
            skuld_image=effective.docker.skuld_image,
        )
        write_bundle(effective, host_facts=facts, external_host=external_host)
        # The overrides file is what `niuu up` merges on the host, so the
        # change survives the next start from the CLI as well.
        write_stack_overrides(
            self._overrides_file, deep_merge(_read_yaml(self._overrides_file), staged)
        )
        self._staged_file.unlink()

        started_at = datetime.now(UTC)
        name = f"{effective.docker.project_name}-apply-{started_at.strftime('%Y%m%d%H%M%S')}"
        await asyncio.to_thread(self._run_applier, effective, name)
        state = {
            "container": name,
            "started_at": started_at.isoformat(),
            "changes": staged,
        }
        self._apply_state_file.write_text(json.dumps(state))
        logger.info("Stack apply started in %s (%s)", name, json.dumps(staged))
        return ApplyStatus(
            state="applying",
            started_at=state["started_at"],
            detail="Restarting the services whose configuration changed.",
            changes=staged,
        )

    def _run_applier(self, settings: CLISettings, name: str) -> None:
        paths = bundle_paths(settings)
        image = settings.docker.applier_image
        try:
            self._client.images.get(image)
        except ImageNotFound:
            logger.info("Pulling stack applier image %s", image)
            self._client.images.pull(image)
        self._client.containers.run(
            image,
            command=[*compose_args(settings), "up", "--detach", "--remove-orphans"],
            name=name,
            detach=True,
            labels={LABEL_MANAGED_BY: MANAGED_BY, LABEL_STACK_APPLY: name},
            volumes={
                self._socket: {"bind": DOCKER_SOCKET, "mode": "rw"},
                str(paths.compose_dir): {"bind": str(paths.compose_dir), "mode": "ro"},
            },
            working_dir=str(paths.compose_dir),
            environment={},
        )

    async def status(self) -> ApplyStatus:
        vllm = await asyncio.to_thread(self._vllm_status)
        if not self._apply_state_file.exists():
            return ApplyStatus(state="idle", vllm=vllm)
        state = json.loads(self._apply_state_file.read_text())
        container = await asyncio.to_thread(self._get_container, str(state.get("container", "")))
        if container is None:
            return ApplyStatus(
                state="failed",
                started_at=str(state.get("started_at", "")),
                detail="The applier container disappeared before reporting a result.",
                changes=dict(state.get("changes", {})),
                vllm=vllm,
            )
        await asyncio.to_thread(container.reload)
        if str(container.status) in {"created", "running"}:
            return ApplyStatus(
                state="applying",
                started_at=str(state.get("started_at", "")),
                detail="Restarting the services whose configuration changed.",
                changes=dict(state.get("changes", {})),
                vllm=vllm,
            )
        exit_code = int((container.attrs.get("State") or {}).get("ExitCode", 1))
        logs = await asyncio.to_thread(container.logs, tail=self._log_tail)
        text = logs.decode("utf-8", errors="replace") if isinstance(logs, bytes) else str(logs)
        await asyncio.to_thread(container.remove, force=True)
        self._apply_state_file.unlink()
        if exit_code != 0:
            return ApplyStatus(
                state="failed",
                started_at=str(state.get("started_at", "")),
                detail=text.strip() or f"docker compose exited with code {exit_code}",
                changes=dict(state.get("changes", {})),
                vllm=vllm,
            )
        return ApplyStatus(
            state="applied",
            started_at=str(state.get("started_at", "")),
            detail="Applied.",
            changes=dict(state.get("changes", {})),
            vllm=vllm,
        )

    # ------------------------------------------------------------------
    # Docker helpers (synchronous SDK, always called off the event loop)
    # ------------------------------------------------------------------

    def _get_container(self, name: str) -> Any | None:
        if not name:
            return None
        try:
            return self._client.containers.get(name)
        except NotFound:
            return None

    def _vllm_status(self) -> VllmStatus | None:
        try:
            settings = self._effective_settings()
        except FileNotFoundError:
            return None
        if not settings.docker.vllm.enabled:
            return None
        container = self._get_container(f"{settings.docker.project_name}-vllm-1")
        if container is None:
            return VllmStatus(state="absent", detail="The vLLM container has not been created yet.")
        container.reload()
        status = str(container.status)
        health = str(((container.attrs.get("State") or {}).get("Health") or {}).get("Status", ""))
        logs = container.logs(tail=1)
        last = (
            logs.decode("utf-8", errors="replace") if isinstance(logs, bytes) else str(logs)
        ).strip()
        if status != "running":
            return VllmStatus(state="failed", detail=last or f"container {status}")
        if health == "healthy":
            return VllmStatus(state="ready", detail=f"Serving {settings.docker.vllm.model}.")
        return VllmStatus(state="starting", detail=last)


def stack_view_dict(view: StackView) -> dict[str, Any]:
    """JSON-ready view (dataclasses to dicts) for the REST layer."""
    return {
        "current": asdict(view.current),
        "staged": view.staged,
        "effective": asdict(view.effective),
        "models": [asdict(option) for option in view.models],
        "accelerator_memory_gib": view.accelerator_memory_gib,
    }
