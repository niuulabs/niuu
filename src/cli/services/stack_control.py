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
import importlib.metadata
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import docker
import httpx
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
    vllm_base_url,
    write_bundle,
    write_stack_overrides,
)
from cli.services.docker_host import MIB_PER_GIB, GpuFacts, HostFacts
from cli.services.model_catalog import expected_weight_bytes, model_options
from niuu.domain.stack import (
    ApplyStatus,
    ExternalIntegrationDefinition,
    ExternalIntegrationSettings,
    ExternalIntegrationValidation,
    ExternalModuleComponent,
    ModelServerSettings,
    ModelTestResult,
    Progress,
    StackSettings,
    StackView,
    VllmSettings,
    VllmStatus,
    deep_merge,
    validate_stack_changes,
)
from niuu.ports.stack_control import StackControlPort
from volundr.config import IntegrationsConfig
from volundr.external_modules import read_external_module_manifest
from volundr.integration_definitions import load_integration_definition_configs

logger = logging.getLogger(__name__)

LABEL_MANAGED_BY = "niuu.managed-by"
MANAGED_BY = "docker_container"
LABEL_STACK_APPLY = "niuu.io/stack-apply"
DOCKER_SOCKET = "/var/run/docker.sock"
DEFAULT_LOG_TAIL = 40
# Lines of container output read to work out what a long step is doing.
PROGRESS_LOG_TAIL = 400
# The quick test sent to the local model once it serves.
MODEL_TEST_PROMPT = "Reply with the single word OK."
MODEL_TEST_MAX_TOKENS = 32
MODEL_TEST_TIMEOUT_SECONDS = 90.0
GB = 1000**3
EXTERNAL_INTEGRATION_MOUNT_ROOT = Path("/opt/niuu-external-integrations")

# `docker compose up` in plain progress mode prints one line per layer event:
#   " 9b37ee547aa6 Downloading [==>   ]  207.6MB/4.311GB"
#   " 0743781e50d5 Extracting 14 s"   " f5cceb5df98d Pull complete"
_LAYER_LINE = re.compile(
    r"^\s*(?P<layer>[0-9a-f]{12})\s+(?P<state>Downloading|Extracting|Verifying Checksum|"
    r"Download complete|Pull complete|Already exists|Waiting|Pulling fs layer)\b(?P<rest>.*)$"
)
_LAYER_BYTES = re.compile(
    r"(?P<done>[\d.]+)(?P<done_unit>[kMG]?B)/(?P<total>[\d.]+)(?P<total_unit>[kMG]?B)"
)
_UNITS = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3}
_LAYER_DONE_STATES = {"Download complete", "Pull complete", "Already exists"}
_SERVICE_LINE = re.compile(
    r"^\s*(?P<service>[\w.-]+)\s+(?P<state>Pulling|Pulled|Creating|Created|Starting|Started|Recreate|Recreated|Running|Healthy)\s*$"
)

# vLLM's log tells which phase the start is in.
_SHARDS_LINE = re.compile(
    r"Loading safetensors checkpoint shards:\s+(?P<pct>\d+)% Completed \| (?P<n>\d+)/(?P<m>\d+)"
)
_WARMUP_MARKERS = (
    "Capturing CUDA graph",
    "torch.compile",
    "Compiling",
    "Warming up",
    "Capturing cudagraphs",
)
_LOADING_MARKERS = ("Starting to load model", "Loading weights", "Loading model weights")
_SERVING_MARKERS = ("Application startup complete", "Uvicorn running")


def _bytes(value: str, unit: str) -> int:
    return round(float(value) * _UNITS[unit])


def parse_pull_progress(text: str) -> Progress | None:
    """Turn `docker compose up` plain output into one Progress, or None when nothing pulled.

    Per layer the last line wins. Completed layers keep the size their last
    Downloading line showed, so the total stays stable while layers finish.
    """
    layers: dict[str, dict[str, Any]] = {}
    services: dict[str, str] = {}
    for raw in text.replace("\r", "\n").splitlines():
        service = _SERVICE_LINE.match(raw)
        if service:
            services[service["service"]] = service["state"]
            continue
        match = _LAYER_LINE.match(raw)
        if not match:
            continue
        entry = layers.setdefault(match["layer"], {"done": 0, "total": 0, "state": ""})
        entry["state"] = match["state"]
        sizes = _LAYER_BYTES.search(match["rest"])
        if sizes:
            entry["total"] = _bytes(sizes["total"], sizes["total_unit"])
            if match["state"] == "Downloading":
                entry["done"] = _bytes(sizes["done"], sizes["done_unit"])
        if match["state"] in _LAYER_DONE_STATES or match["state"] == "Extracting":
            entry["done"] = entry["total"]
    if not layers:
        pulling = [name for name, state in services.items() if state == "Pulling"]
        if pulling:
            return Progress(phase="pulling", detail=f"Pulling the {', '.join(pulling)} image…")
        return None
    total = sum(int(entry["total"]) for entry in layers.values())
    done = sum(min(int(entry["done"]), int(entry["total"])) for entry in layers.values())
    finished = sum(1 for entry in layers.values() if entry["state"] in _LAYER_DONE_STATES)
    extracting = sum(1 for entry in layers.values() if entry["state"] == "Extracting")
    pulling = [name for name, state in services.items() if state == "Pulling"]
    what = f"the {', '.join(pulling)} image" if pulling else "images"
    if all(entry["state"] in _LAYER_DONE_STATES for entry in layers.values()):
        return Progress(
            phase="starting",
            detail="Images pulled; starting the services…",
            completed_bytes=total,
            total_bytes=total,
        )
    parts = [f"Pulling {what}"]
    if total:
        parts.append(f"{done / GB:.1f} of {total / GB:.1f} GB")
    parts.append(f"{finished} of {len(layers)} layers done")
    if extracting:
        parts.append(f"extracting {extracting}")
    return Progress(
        phase="pulling", detail=" · ".join(parts), completed_bytes=done, total_bytes=total
    )


def _download_state(path: Path) -> tuple[int, bool]:
    """Bytes under *path* and whether huggingface_hub still has a partial blob there."""
    total = 0
    in_flight = False
    for root, _dirs, files in os.walk(path):
        for name in files:
            if name.endswith(".incomplete"):
                in_flight = True
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total, in_flight


def hf_cache_dir(models_dir: Path, model: str) -> Path:
    """Where huggingface_hub keeps *model* under ``HF_HOME=models_dir``."""
    return models_dir / "hub" / f"models--{model.replace('/', '--')}"


def vllm_progress(text: str, *, model: str, cache_dir: Path, expected_bytes: int) -> Progress:
    """What a not-yet-healthy vLLM container is doing, from its log and its download on disk."""
    lines = [line for line in text.replace("\r", "\n").splitlines() if line.strip()]
    for line in reversed(lines):
        if any(marker in line for marker in _SERVING_MARKERS):
            return Progress(phase="serving", detail="Model loaded; the server is coming up…")
        shards = _SHARDS_LINE.search(line)
        if shards:
            return Progress(
                phase="loading",
                detail=f"Loading the model into memory · shard {shards['n']} of {shards['m']}",
                completed_bytes=int(shards["n"]),
                total_bytes=int(shards["m"]),
            )
        if any(marker in line for marker in _WARMUP_MARKERS):
            return Progress(phase="warming", detail="Warming up the GPU (CUDA graphs)…")
    # vLLM logs "Starting to load model" before it fetches the weights, so the
    # download on disk (huggingface_hub keeps partial blobs as `.incomplete`)
    # decides between downloading and loading until the shard counter appears.
    downloaded, in_flight = _download_state(cache_dir) if cache_dir.exists() else (0, False)
    loading = any(marker in line for line in lines for marker in _LOADING_MARKERS)
    if loading and downloaded and not in_flight:
        return Progress(phase="loading", detail="Loading the model into memory…")
    if downloaded:
        size = f"{downloaded / GB:.1f}"
        if expected_bytes:
            detail = f"Downloading {model} · {size} of ~{expected_bytes / GB:.0f} GB"
        else:
            detail = f"Downloading {model} · {size} GB so far"
        return Progress(
            phase="downloading",
            detail=detail,
            completed_bytes=downloaded,
            total_bytes=max(expected_bytes, downloaded) if expected_bytes else 0,
        )
    last = lines[-1] if lines else ""
    return Progress(phase="starting", detail=last or "Starting the vLLM container…")


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
    server = settings.docker.model_server
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
        max_sessions=settings.pod_manager.max_concurrent,
        model_server=ModelServerSettings(
            enabled=server.enabled,
            base_url=server.base_url,
            models=tuple(server.models),
            has_api_key=bool(server.api_key),
        ),
        external_integrations=tuple(
            ExternalIntegrationSettings(
                source_dir=item.source_dir,
                definition_files=tuple(item.definition_files),
                manifest_file=item.manifest_file,
            )
            for item in settings.docker.external_integrations
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
        memory_gib = round(facts.accelerator_memory_mib / MIB_PER_GIB)
        return StackView(
            current=stack_settings_view(current, external_host),
            staged=self._staged(),
            effective=stack_settings_view(effective, external_host),
            models=model_options(effective.docker.models, memory_gib),
            accelerator_memory_gib=memory_gib,
        )

    async def stage(self, changes: dict[str, Any]) -> StackView:
        validated = validate_stack_changes(changes)
        merged = deep_merge(self._staged(), validated)
        # Validate the merged result renders as settings before keeping it.
        CLISettings.model_validate(
            deep_merge(self._current_settings().model_dump(mode="json"), merged)
        )
        # This is the one path every caller of `stage()` goes through,
        # including `PUT /stack`: whatever route staged the change, an
        # `external_integrations` list is confined to the managed root (or an
        # already-registered mount), checked for missing definition/manifest
        # files, and checked for slug/module/component collisions before it
        # is written. This does not execute the package's code — that only
        # happens when a genuinely new package is added through the
        # dedicated `/settings/external-integrations` endpoint.
        staged_external_integrations = merged.get("docker", {}).get("external_integrations")
        if staged_external_integrations is not None:
            await asyncio.to_thread(
                self._check_staged_external_integrations, staged_external_integrations
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
        # The bundle `niuu up` wrote must be the one re-rendered: a missing
        # secrets file means this container is looking at the wrong directory,
        # and rendering there would start Postgres with a new password against
        # the old data directory.
        paths = bundle_paths(effective)
        if not paths.secrets_file.exists():
            raise FileNotFoundError(
                f"{paths.secrets_file} is missing; the compose bundle is not visible from the "
                "platform container. Run `niuu up` on the host again."
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
        if state.get("state") in {"applied", "failed"}:
            # The outcome is kept until the next apply: the platform restarts
            # during an apply, so the poll that would have seen it can miss it.
            return ApplyStatus(
                state=str(state["state"]),
                started_at=str(state.get("started_at", "")),
                detail=str(state.get("detail", "")),
                changes=dict(state.get("changes", {})),
                vllm=vllm,
            )
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
            logs = await asyncio.to_thread(container.logs, tail=PROGRESS_LOG_TAIL)
            text = logs.decode("utf-8", errors="replace") if isinstance(logs, bytes) else str(logs)
            progress = parse_pull_progress(text)
            return ApplyStatus(
                state="applying",
                started_at=str(state.get("started_at", "")),
                detail=progress.detail
                if progress
                else "Restarting the services whose configuration changed.",
                changes=dict(state.get("changes", {})),
                vllm=vllm,
                progress=progress,
            )
        exit_code = int((container.attrs.get("State") or {}).get("ExitCode", 1))
        logs = await asyncio.to_thread(container.logs, tail=self._log_tail)
        text = logs.decode("utf-8", errors="replace") if isinstance(logs, bytes) else str(logs)
        await asyncio.to_thread(container.remove, force=True)
        outcome = "failed" if exit_code != 0 else "applied"
        detail = (
            (text.strip() or f"docker compose exited with code {exit_code}")
            if exit_code != 0
            else "Applied."
        )
        self._apply_state_file.write_text(
            json.dumps(
                {
                    "state": outcome,
                    "started_at": state.get("started_at", ""),
                    "finished_at": datetime.now(UTC).isoformat(),
                    "detail": detail,
                    "changes": state.get("changes", {}),
                }
            )
        )
        return ApplyStatus(
            state=outcome,
            started_at=str(state.get("started_at", "")),
            detail=detail,
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
        logs = container.logs(tail=PROGRESS_LOG_TAIL)
        text = logs.decode("utf-8", errors="replace") if isinstance(logs, bytes) else str(logs)
        lines = [line for line in text.replace("\r", "\n").splitlines() if line.strip()]
        last = lines[-1].strip() if lines else ""
        model = settings.docker.vllm.model
        if status == "created":
            progress = Progress(phase="starting", detail="Starting the vLLM container…")
            return VllmStatus(state="starting", detail=progress.detail, progress=progress)
        if status != "running":
            # "restarting" is a crash loop; the last log line names the cause.
            return VllmStatus(state="failed", detail=last or f"container {status}")
        if health == "healthy":
            return VllmStatus(state="ready", detail=f"Serving {model}.")
        progress = vllm_progress(
            text,
            model=model,
            cache_dir=hf_cache_dir(self._dir / "models", model),
            expected_bytes=expected_weight_bytes(settings.docker.models, model),
        )
        return VllmStatus(state="starting", detail=progress.detail, progress=progress)

    async def test_model(self) -> ModelTestResult:
        settings = self._effective_settings()
        if not settings.docker.vllm.enabled:
            raise ValueError("No local model is configured; pick one under Local model first")
        vllm = await asyncio.to_thread(self._vllm_status)
        if vllm is None or vllm.state != "ready":
            raise ValueError("The local model is not serving yet; wait for it to become ready")
        model = settings.docker.vllm.model
        url = f"{vllm_base_url(settings)}/v1/chat/completions"
        body = {
            "model": model,
            "messages": [{"role": "user", "content": MODEL_TEST_PROMPT}],
            "max_tokens": MODEL_TEST_MAX_TOKENS,
            "temperature": 0,
            # Reasoning models think before they answer; the test wants the
            # answer, not the deliberation (vLLM honours this for such models).
            "chat_template_kwargs": {"enable_thinking": False},
        }
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=MODEL_TEST_TIMEOUT_SECONDS) as client:
                response = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            latency = int((time.monotonic() - started) * 1000)
            return ModelTestResult(
                ok=False, model=model, reply="", latency_ms=latency, detail=f"{url}: {exc}"
            )
        latency = int((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            return ModelTestResult(
                ok=False,
                model=model,
                reply="",
                latency_ms=latency,
                detail=f"HTTP {response.status_code}: {response.text[:300]}",
            )
        try:
            reply = str(response.json()["choices"][0]["message"]["content"]).strip()
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return ModelTestResult(
                ok=False,
                model=model,
                reply="",
                latency_ms=latency,
                detail=f"Unexpected response shape: {exc}",
            )
        return ModelTestResult(ok=bool(reply), model=model, reply=reply, latency_ms=latency)

    async def validate_external_integration(
        self,
        source_dir: str,
        definition_files: list[str],
        manifest_file: str = "",
    ) -> ExternalIntegrationValidation:
        return await asyncio.to_thread(
            self._validate_external_integration,
            source_dir,
            definition_files,
            manifest_file,
        )

    async def describe_external_integration(
        self,
        source_dir: str,
        definition_files: list[str],
        manifest_file: str = "",
    ) -> ExternalIntegrationValidation:
        return await asyncio.to_thread(
            self._describe_external_integration,
            source_dir,
            definition_files,
            manifest_file,
        )

    async def external_integrations_root(self) -> str:
        return str((self._dir / "private-integrations").resolve())

    def _resolve_external_integration_source(
        self,
        source_dir: str,
        definition_files: list[str],
        manifest_file: str,
    ) -> tuple[Path, Path | None, ExternalIntegrationValidation | None]:
        """Confine *source_dir* to the managed root or an already-registered mount.

        Returns ``(source, validation_source, error)``: ``error`` is set (and
        ``validation_source`` is ``None``) when the directory is not confined
        or does not exist — this is the check every caller of a staged
        ``external_integrations`` change goes through, whether it arrives via
        the dedicated settings endpoints or ``PUT /stack``.
        """
        source = Path(source_dir).expanduser().resolve()
        managed_root = (self._dir / "private-integrations").resolve()
        validation_source = source
        try:
            source.relative_to(managed_root)
        except ValueError:
            validation_source = self._registered_external_integration_mount(
                source,
                definition_files,
                manifest_file,
            )
            if validation_source is None:
                return (
                    source,
                    None,
                    ExternalIntegrationValidation(
                        ok=False,
                        source_dir=str(source),
                        definition_files=tuple(definition_files),
                        manifest_file=manifest_file,
                        errors=(f"source_dir must be inside {managed_root}",),
                    ),
                )
        if not validation_source.is_dir():
            return (
                source,
                None,
                ExternalIntegrationValidation(
                    ok=False,
                    source_dir=str(source),
                    definition_files=tuple(definition_files),
                    manifest_file=manifest_file,
                    errors=(f"Package directory does not exist: {validation_source}",),
                ),
            )
        return source, validation_source, None

    def _describe_external_integration(
        self,
        source_dir: str,
        definition_files: list[str],
        manifest_file: str = "",
    ) -> ExternalIntegrationValidation:
        """Structural validation only: confinement, existence, and static parsing.

        Never imports or executes anything from the package, so it is safe to
        run for every registered package on every list/add/stage call.
        """
        source, validation_source, error = self._resolve_external_integration_source(
            source_dir, definition_files, manifest_file
        )
        if error is not None:
            return error
        assert validation_source is not None
        try:
            normalized, definition_paths, manifest_path = _normalize_external_integration(
                validation_source, definition_files, manifest_file
            )
            # Static, so it guards every route that can stage a package, not only
            # the endpoint that goes on to import it.
            _check_import_shadowing(validation_source)
            discovered: list[ExternalIntegrationDefinition] = []
            components: list[ExternalModuleComponent] = []
            module_id = ""
            if manifest_path is not None:
                loaded_module = read_external_module_manifest(manifest_path)
                module_id = loaded_module.manifest.id
                definition_paths.extend(loaded_module.integration_definition_files)
                components.extend(
                    ExternalModuleComponent(
                        kind=component.kind,
                        name=component.name,
                        adapter=component.adapter,
                    )
                    for component in loaded_module.manifest.components
                )
            definitions = load_integration_definition_configs(
                IntegrationsConfig(
                    definitions=[],
                    definition_files=[str(path) for path in definition_paths],
                )
            )
            # The same no-overrides rule used at service startup also catches
            # collisions with the built-in catalog before a restart is attempted.
            load_integration_definition_configs(
                IntegrationsConfig(definition_files=[str(path) for path in definition_paths])
            )
            for definition in definitions:
                discovered.append(
                    ExternalIntegrationDefinition(
                        slug=definition.slug,
                        name=definition.name,
                        integration_type=definition.integration_type,
                        adapter=definition.adapter,
                    )
                )
            if not discovered and not components:
                raise ValueError("No integration definitions or module components were found")
        except (AttributeError, ImportError, OSError, TypeError, ValueError) as exc:
            return ExternalIntegrationValidation(
                ok=False,
                source_dir=str(source),
                definition_files=tuple(definition_files),
                manifest_file=manifest_file,
                errors=(str(exc),),
            )
        return ExternalIntegrationValidation(
            ok=True,
            source_dir=str(source),
            definition_files=tuple(normalized["definition_files"]),
            manifest_file=normalized.get("manifest_file", ""),
            module_id=module_id,
            definitions=tuple(discovered),
            components=tuple(components),
        )

    def _validate_external_integration(
        self,
        source_dir: str,
        definition_files: list[str],
        manifest_file: str = "",
    ) -> ExternalIntegrationValidation:
        """Full validation of a UI-managed package: structure, then imports.

        Only the import step actually runs the package's code, in a
        subprocess that cannot shadow the platform (`_check_import_shadowing`,
        `_validate_external_imports`) and is bounded by a configured timeout.
        """
        source, validation_source, error = self._resolve_external_integration_source(
            source_dir, definition_files, manifest_file
        )
        if error is not None:
            return error
        assert validation_source is not None
        try:
            normalized, definition_paths, manifest_path = _normalize_external_integration(
                validation_source, definition_files, manifest_file
            )
            discovered: list[ExternalIntegrationDefinition] = []
            components: list[ExternalModuleComponent] = []
            module_id = ""
            if manifest_path is not None:
                loaded_module = read_external_module_manifest(manifest_path)
                module_id = loaded_module.manifest.id
                definition_paths.extend(loaded_module.integration_definition_files)
                components.extend(
                    ExternalModuleComponent(
                        kind=component.kind,
                        name=component.name,
                        adapter=component.adapter,
                    )
                    for component in loaded_module.manifest.components
                )
            definitions = load_integration_definition_configs(
                IntegrationsConfig(
                    definitions=[],
                    definition_files=[str(path) for path in definition_paths],
                )
            )
            load_integration_definition_configs(
                IntegrationsConfig(definition_files=[str(path) for path in definition_paths])
            )
            _check_import_shadowing(validation_source)
            timeout_seconds = (
                self._current_settings().docker.external_integration_validation_timeout_seconds
            )
            _validate_external_imports(
                validation_source,
                manifest_path=manifest_path,
                adapters=[definition.adapter for definition in definitions if definition.adapter],
                timeout_seconds=timeout_seconds,
            )
            for definition in definitions:
                discovered.append(
                    ExternalIntegrationDefinition(
                        slug=definition.slug,
                        name=definition.name,
                        integration_type=definition.integration_type,
                        adapter=definition.adapter,
                    )
                )
            if not discovered and not components:
                raise ValueError("No integration definitions or module components were found")
        except (AttributeError, ImportError, OSError, TypeError, ValueError) as exc:
            return ExternalIntegrationValidation(
                ok=False,
                source_dir=str(source),
                definition_files=tuple(definition_files),
                manifest_file=manifest_file,
                errors=(str(exc),),
            )
        return ExternalIntegrationValidation(
            ok=True,
            source_dir=str(source),
            definition_files=tuple(normalized["definition_files"]),
            manifest_file=normalized.get("manifest_file", ""),
            module_id=module_id,
            definitions=tuple(discovered),
            components=tuple(components),
        )

    def _check_staged_external_integrations(self, packages: list[dict[str, Any]]) -> None:
        """Structural + collision validation for a whole staged package list.

        Runs whatever confined ``external_integrations`` list is about to be
        staged through the same checks `add_external_integration` applies,
        so ``PUT /stack`` cannot bypass them. Deliberately uses the
        structural-only description (no import execution): a package already
        registered was already import-checked when it was added, so this only
        needs to re-confirm confinement, existence, and that the final list
        has no slug/module/component collisions.
        """
        slugs: set[str] = set()
        module_ids: set[str] = set()
        component_names: set[str] = set()
        for package in packages:
            result = self._describe_external_integration(
                str(package.get("source_dir", "")),
                list(package.get("definition_files", [])),
                str(package.get("manifest_file", "")),
            )
            if not result.ok:
                raise ValueError(
                    f"external_integrations entry {package.get('source_dir')!r} is invalid: "
                    + "; ".join(result.errors)
                )
            duplicate_slugs = sorted(
                definition.slug for definition in result.definitions if definition.slug in slugs
            )
            if duplicate_slugs:
                raise ValueError(
                    "Integration slugs supplied by more than one external package: "
                    + ", ".join(duplicate_slugs)
                )
            slugs.update(definition.slug for definition in result.definitions)
            if result.module_id:
                if result.module_id in module_ids:
                    raise ValueError(
                        f"External module id supplied by more than one package: {result.module_id}"
                    )
                module_ids.add(result.module_id)
            duplicate_components = sorted(
                f"{component.kind}:{component.name}"
                for component in result.components
                if component.name in component_names
            )
            if duplicate_components:
                raise ValueError(
                    "External component names supplied by more than one package: "
                    + ", ".join(duplicate_components)
                )
            component_names.update(component.name for component in result.components)

    def _registered_external_integration_mount(
        self,
        source: Path,
        definition_files: list[str],
        manifest_file: str,
    ) -> Path | None:
        """Resolve an exact deployment-owned package to its container mount."""
        configured = self._current_settings().docker.external_integrations
        requested_definitions = tuple(definition_files)
        for index, integration in enumerate(configured):
            registered_source = Path(integration.source_dir).expanduser().resolve()
            if source != registered_source:
                continue
            if requested_definitions != tuple(integration.definition_files):
                continue
            if manifest_file != integration.manifest_file:
                continue
            return EXTERNAL_INTEGRATION_MOUNT_ROOT / str(index)
        return None


def _normalize_external_integration(
    validation_source: Path,
    definition_files: list[str],
    manifest_file: str,
) -> tuple[dict[str, Any], list[Path], Path | None]:
    """Type-check the request and confirm its definition/manifest files exist.

    Returns the normalized change-set entry, the resolved definition file
    paths (relative ones only; a manifest may add more), and the manifest
    path when one was given.
    """
    normalized = validate_stack_changes(
        {
            "external_integrations": [
                {
                    "source_dir": str(validation_source),
                    "definition_files": definition_files,
                    "manifest_file": manifest_file,
                }
            ]
        }
    )["docker"]["external_integrations"][0]
    definition_paths = [validation_source / name for name in normalized["definition_files"]]
    metadata_paths = list(definition_paths)
    manifest_path = None
    if normalized.get("manifest_file"):
        manifest_path = validation_source / normalized["manifest_file"]
        metadata_paths.append(manifest_path)
    for path in metadata_paths:
        resolved = path.resolve()
        try:
            resolved.relative_to(validation_source)
        except ValueError as exc:
            raise ValueError(f"Definition file resolves outside source_dir: {path}") from exc
        if not resolved.is_file():
            raise ValueError(f"External package file does not exist: {path}")
    return normalized, definition_paths, manifest_path


# Top-level package names this project ships (`packages` in pyproject.toml).
# An external package that ships a module of the same name would shadow the
# platform's own code the moment it is put anywhere on `sys.path`.
PLATFORM_PACKAGE_NAMES: frozenset[str] = frozenset(
    {
        "audit",
        "cli",
        "credentials",
        "features",
        "guild",
        "identity",
        "integrations",
        "niuu",
        "volundr",
        "ting",
        "tracker",
        "skuld",
        "sleipnir",
        "ravn",
        "bifrost",
        "mimir",
        "observatory",
        "personas",
        "tyr",
    }
)

# The validator subprocess needs nothing else: no database URLs, no
# provider tokens, no signing keys. Anything not listed here never reaches
# an external package's import-time code.
_VALIDATOR_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "LC_CTYPE")


def _minimal_validator_environment() -> dict[str, str]:
    """A scrubbed environment for the import-validator subprocess."""
    return {key: os.environ[key] for key in _VALIDATOR_ENV_ALLOWLIST if key in os.environ}


def _check_import_shadowing(source: Path) -> None:
    """Reject a package whose top-level module names would shadow the stdlib,
    an installed distribution, or one of this platform's own packages.

    A package that is merely a dependency of the validated import graph is
    put on `sys.path` (see `_validate_external_imports`); a module named the
    same as `os`, `yaml`, `niuu`, `sitecustomize`, or an installed dependency
    would silently replace it in every process that imports it, not just the
    validator's.
    """
    reserved = set(sys.stdlib_module_names) | PLATFORM_PACKAGE_NAMES
    reserved.update(importlib.metadata.packages_distributions().keys())
    for entry in sorted(source.iterdir()):
        name: str | None = None
        if entry.is_file() and entry.suffix == ".py":
            name = entry.stem
        elif entry.is_dir() and (entry / "__init__.py").is_file():
            name = entry.name
        if name is None or name not in reserved:
            continue
        raise ValueError(
            f"External package module {name!r} in {source} would shadow a stdlib, "
            "installed, or platform module of the same name; rename it before "
            "the package can be validated"
        )


def _validate_external_imports(
    source: Path,
    *,
    manifest_path: Path | None,
    adapters: list[str],
    timeout_seconds: float,
) -> None:
    """Validate external imports in a fresh interpreter with fresh bytecode.

    Run with ``-P`` (never prepend the script/cwd directory to ``sys.path``)
    and the package path passed explicitly as ``--package-path``, which
    ``volundr.external_modules`` appends to the *end* of ``sys.path`` rather
    than the front. Combined with the scrubbed environment (no secrets) and a
    configured timeout (no hang can wedge every list/add call), the package
    can supply the modules it declares but cannot shadow the platform or the
    interpreter that is validating it.
    """
    if manifest_path is None and not adapters:
        return

    command = [
        sys.executable,
        "-P",
        "-m",
        "volundr.external_modules",
        "--package-path",
        str(source),
    ]
    if manifest_path is not None:
        command.extend(("--manifest", str(manifest_path)))
    for adapter in adapters:
        command.extend(("--adapter", adapter))

    environment = _minimal_validator_environment()
    with tempfile.TemporaryDirectory(prefix="niuu-module-pycache-") as pycache:
        environment["PYTHONPYCACHEPREFIX"] = pycache
        try:
            result = subprocess.run(
                command,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                f"External package validation timed out after {timeout_seconds:.0f}s; "
                "raise docker.external_integration_validation_timeout_seconds if the "
                "package legitimately needs longer to import"
            ) from exc
    if result.returncode == 0:
        return
    detail = result.stderr.strip() or result.stdout.strip()
    raise ValueError(detail or f"External package validation exited {result.returncode}")


def stack_view_dict(view: StackView) -> dict[str, Any]:
    """JSON-ready view (dataclasses to dicts) for the REST layer."""
    return {
        "current": asdict(view.current),
        "staged": view.staged,
        "effective": asdict(view.effective),
        "models": [asdict(option) for option in view.models],
        "accelerator_memory_gib": view.accelerator_memory_gib,
    }
