"""Stack settings the setup wizard can change on a single-host install.

The wizard never edits compose files itself: it stages a small, validated set
of changes (who can reach the install, which local model to serve), and the
stack controller re-renders the bundle and restarts what changed. These are
the shapes both sides agree on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

LOCAL_BIND_HOST = "127.0.0.1"
NETWORK_BIND_HOST = "0.0.0.0"
ALLOWED_BIND_HOSTS: tuple[str, ...] = (LOCAL_BIND_HOST, NETWORK_BIND_HOST)


@dataclass(frozen=True)
class VllmSettings:
    enabled: bool
    model: str
    image: str
    max_model_len: int
    gpu_memory_utilization: float


@dataclass(frozen=True)
class ModelServerSettings:
    """A model server the operator already runs, routed through the gateway."""

    enabled: bool = False
    base_url: str = ""
    models: tuple[str, ...] = ()
    has_api_key: bool = False


@dataclass(frozen=True)
class ExternalIntegrationSettings:
    """One deployment-owned external package mounted into the platform."""

    source_dir: str
    definition_files: tuple[str, ...]
    manifest_file: str = ""


@dataclass(frozen=True)
class StackSettings:
    """The subset of the docker bundle settings the wizard shows and edits."""

    bind_host: str
    external_host: str
    port: int
    project_name: str
    skuld_image: str
    vllm: VllmSettings
    # Sessions that may run at once (the platform's pod_manager.max_concurrent).
    max_sessions: int = 4
    model_server: ModelServerSettings = field(default_factory=ModelServerSettings)
    external_integrations: tuple[ExternalIntegrationSettings, ...] = ()

    @property
    def access_urls(self) -> list[str]:
        urls = [f"http://{LOCAL_BIND_HOST}:{self.port}"]
        if self.bind_host != LOCAL_BIND_HOST and self.external_host:
            urls.append(f"http://{self.external_host}:{self.port}")
        return urls


@dataclass(frozen=True)
class ModelOption:
    """One curated local model with a fit verdict for this host."""

    id: str
    model: str
    name: str
    description: str
    weight_gib: int
    recommended: bool
    # None when the host's accelerator memory is unknown (no GPU facts).
    fits: bool | None
    memory_needed_gib: int


@dataclass(frozen=True)
class StackView:
    """Current settings, what is staged, and what the result would be."""

    current: StackSettings
    staged: dict[str, Any]
    effective: StackSettings
    models: list[ModelOption] = field(default_factory=list)
    accelerator_memory_gib: int = 0

    @property
    def has_staged_changes(self) -> bool:
        return bool(self.staged)


@dataclass(frozen=True)
class Progress:
    """How far a long step is: a phase, a sentence, and bytes when they are known.

    ``total_bytes`` is 0 when the size is unknown; ``completed_bytes`` still
    counts what has arrived so the UI can show movement without a bar.
    """

    phase: str
    detail: str
    completed_bytes: int = 0
    total_bytes: int = 0


@dataclass(frozen=True)
class VllmStatus:
    """Where the local model container is: absent, starting, ready or failed."""

    state: str
    detail: str = ""
    # What "starting" is doing right now: downloading, loading or warming up.
    progress: Progress | None = None


@dataclass(frozen=True)
class ApplyStatus:
    """Progress of the last apply: idle, applying, applied or failed."""

    state: str
    started_at: str = ""
    detail: str = ""
    changes: dict[str, Any] = field(default_factory=dict)
    vllm: VllmStatus | None = None
    # While applying: what `docker compose up` is doing (image pulls mostly).
    progress: Progress | None = None


@dataclass(frozen=True)
class ModelTestResult:
    """One short completion sent to the local model, and what came back."""

    ok: bool
    model: str
    reply: str
    latency_ms: int
    detail: str = ""


@dataclass(frozen=True)
class ExternalIntegrationDefinition:
    """A definition discovered while validating an external package."""

    slug: str
    name: str
    integration_type: str
    adapter: str


@dataclass(frozen=True)
class ExternalModuleComponent:
    """One typed component discovered from an external module manifest."""

    kind: str
    name: str
    adapter: str


@dataclass(frozen=True)
class ExternalIntegrationValidation:
    """Validation result for one external package directory."""

    ok: bool
    source_dir: str
    definition_files: tuple[str, ...]
    manifest_file: str = ""
    module_id: str = ""
    definitions: tuple[ExternalIntegrationDefinition, ...] = ()
    components: tuple[ExternalModuleComponent, ...] = ()
    errors: tuple[str, ...] = ()


def _external_integrations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("external_integrations must be a list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"external_integrations item {index + 1} must be an object")
        source_dir = item.get("source_dir")
        definition_files = item.get("definition_files", [])
        manifest_file = item.get("manifest_file", "")
        if not isinstance(source_dir, str) or not source_dir.strip():
            raise ValueError(
                f"external_integrations item {index + 1} source_dir must be a non-empty string"
            )
        source_dir = source_dir.strip()
        if source_dir in seen:
            raise ValueError(f"external_integrations contains source_dir {source_dir!r} twice")
        seen.add(source_dir)
        if not isinstance(definition_files, list):
            raise ValueError(
                f"external_integrations item {index + 1} definition_files must be a list"
            )
        files: list[str] = []
        for definition_file in definition_files:
            if not isinstance(definition_file, str) or not definition_file.strip():
                raise ValueError("definition_files entries must be non-empty strings")
            path = PurePosixPath(definition_file.strip())
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("definition_files entries must be paths within source_dir")
            files.append(str(path))
        if not isinstance(manifest_file, str):
            raise ValueError("manifest_file must be a string")
        manifest_file = manifest_file.strip()
        if manifest_file:
            manifest_path = PurePosixPath(manifest_file)
            if manifest_path.is_absolute() or ".." in manifest_path.parts:
                raise ValueError("manifest_file must be a path within source_dir")
            manifest_file = str(manifest_path)
        if not files and not manifest_file:
            raise ValueError(
                f"external_integrations item {index + 1} requires manifest_file or definition_files"
            )
        package = {"source_dir": source_dir, "definition_files": files}
        if manifest_file:
            package["manifest_file"] = manifest_file
        normalized.append(package)
    return normalized


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Return *base* with *overlay* merged in, nested dicts merged recursively."""
    result = dict(base)
    for key, value in overlay.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = deep_merge(current, value)
            continue
        result[key] = value
    return result


def _model_list(value: Any) -> list[str]:
    """model_server_models: a list of ids, or one string with commas or newlines."""
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace("\n", ",").split(",")]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        parts = [item.strip() for item in value]
    else:
        raise ValueError(
            "model_server_models must be a list of model ids or a comma-separated string"
        )
    models = [part for part in parts if part]
    if not models:
        raise ValueError("model_server_models must name at least one model id")
    return models


def validate_stack_changes(changes: dict[str, Any]) -> dict[str, Any]:
    """Whitelist and type-check what the wizard may stage.

    Returns the change set in the nested ``{"docker": {...}}`` form the bundle
    settings use. Anything else is rejected with the remedy in the message.
    """
    docker: dict[str, Any] = {}
    vllm: dict[str, Any] = {}
    model_server: dict[str, Any] = {}
    pod_manager: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "model_server_enabled":
            if not isinstance(value, bool):
                raise ValueError("model_server_enabled must be true or false")
            model_server["enabled"] = value
        elif key == "model_server_url":
            if not isinstance(value, str) or not value.strip().startswith(("http://", "https://")):
                raise ValueError("model_server_url must be an http(s) URL")
            model_server["base_url"] = value.strip()
        elif key == "model_server_models":
            model_server["models"] = _model_list(value)
        elif key == "model_server_api_key":
            if not isinstance(value, str):
                raise ValueError("model_server_api_key must be a string")
            model_server["api_key"] = value.strip()
        elif key == "max_sessions":
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError("max_sessions must be a positive integer")
            pod_manager["max_concurrent"] = value
        elif key == "bind_host":
            if value not in ALLOWED_BIND_HOSTS:
                raise ValueError(
                    f"bind_host must be one of {', '.join(ALLOWED_BIND_HOSTS)}; got {value!r}"
                )
            docker["bind_host"] = value
        elif key == "vllm_enabled":
            if not isinstance(value, bool):
                raise ValueError("vllm_enabled must be true or false")
            vllm["enabled"] = value
        elif key == "vllm_model":
            if not isinstance(value, str):
                raise ValueError("vllm_model must be a Hugging Face model id")
            vllm["model"] = value.strip()
        elif key == "vllm_max_model_len":
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError("vllm_max_model_len must be a positive integer")
            vllm["max_model_len"] = value
        elif key == "vllm_gpu_memory_utilization":
            if not isinstance(value, int | float) or not 0 < float(value) <= 1:
                raise ValueError("vllm_gpu_memory_utilization must be between 0 and 1")
            vllm["gpu_memory_utilization"] = float(value)
        elif key == "vllm_trust_remote_code":
            if not isinstance(value, bool):
                raise ValueError("vllm_trust_remote_code must be true or false")
            vllm["trust_remote_code"] = value
        elif key == "external_integrations":
            docker["external_integrations"] = _external_integrations(value)
        else:
            raise ValueError(
                f"Unknown stack setting {key!r}; the wizard can change bind_host, "
                "max_sessions, vllm_enabled, vllm_model, vllm_max_model_len, "
                "vllm_gpu_memory_utilization, vllm_trust_remote_code, model_server_enabled, "
                "model_server_url, model_server_models, model_server_api_key and "
                "external_integrations"
            )
    if vllm.get("enabled") and not vllm.get("model", "") and "model" in vllm:
        raise ValueError("vllm_model is required when vllm_enabled is true")
    if vllm:
        docker["vllm"] = vllm
    if model_server:
        docker["model_server"] = model_server
    result: dict[str, Any] = {}
    if docker:
        result["docker"] = docker
    if pod_manager:
        result["pod_manager"] = pod_manager
    return result
