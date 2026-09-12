"""Stack settings the setup wizard can change on a single-host install.

The wizard never edits compose files itself: it stages a small, validated set
of changes (who can reach the install, which local model to serve), and the
stack controller re-renders the bundle and restarts what changed. These are
the shapes both sides agree on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
class StackSettings:
    """The subset of the docker bundle settings the wizard shows and edits."""

    bind_host: str
    external_host: str
    port: int
    project_name: str
    skuld_image: str
    vllm: VllmSettings

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
class VllmStatus:
    """Where the local model container is: absent, starting, ready or failed."""

    state: str
    detail: str = ""


@dataclass(frozen=True)
class ApplyStatus:
    """Progress of the last apply: idle, applying, applied or failed."""

    state: str
    started_at: str = ""
    detail: str = ""
    changes: dict[str, Any] = field(default_factory=dict)
    vllm: VllmStatus | None = None


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


def validate_stack_changes(changes: dict[str, Any]) -> dict[str, Any]:
    """Whitelist and type-check what the wizard may stage.

    Returns the change set in the nested ``{"docker": {...}}`` form the bundle
    settings use. Anything else is rejected with the remedy in the message.
    """
    docker: dict[str, Any] = {}
    vllm: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "bind_host":
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
        else:
            raise ValueError(
                f"Unknown stack setting {key!r}; the wizard can change bind_host, "
                "vllm_enabled, vllm_model, vllm_max_model_len and vllm_gpu_memory_utilization"
            )
    if vllm.get("enabled") and not vllm.get("model", "") and "model" in vllm:
        raise ValueError("vllm_model is required when vllm_enabled is true")
    if vllm:
        docker["vllm"] = vllm
    return {"docker": docker} if docker else {}
