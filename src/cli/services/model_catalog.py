"""The local models the wizard offers, read from ``docker.models`` in the CLI config.

Sizes are what vLLM reserves for weights plus a 64k-token KV cache, rounded
up, so a "fits" verdict leaves room for session sandboxes. Anything not
listed can still be entered as a custom Hugging Face id. The list itself is
configuration (the installer writes it into ``~/.niuu/config.yaml``), so a
new model, image tag or serve flag never needs a new platform image.
"""

from __future__ import annotations

from collections.abc import Sequence

from cli.config import DockerModelConfig
from niuu.domain.stack import ModelOption

# Memory kept free for the platform and session sandboxes when judging fit.
SANDBOX_HEADROOM_GIB = 12


def find_model(models: Sequence[DockerModelConfig], model: str) -> DockerModelConfig | None:
    """The configured entry serving *model*, or None for a custom id."""
    for entry in models:
        if entry.model == model:
            return entry
    return None


def model_trusts_remote_code(models: Sequence[DockerModelConfig], model: str) -> bool:
    """True for a configured model whose repository ships code vLLM has to run."""
    entry = find_model(models, model)
    return entry is not None and entry.trust_remote_code


def model_serve_args(models: Sequence[DockerModelConfig], model: str) -> list[str]:
    """Extra `vllm serve` arguments configured for *model* (none for a custom id)."""
    entry = find_model(models, model)
    return list(entry.serve_args) if entry is not None else []


def expected_weight_bytes(models: Sequence[DockerModelConfig], model: str) -> int:
    """Size of a configured model's weights on disk, 0 for a model not in the list."""
    entry = find_model(models, model)
    return entry.weight_gib * 1024**3 if entry is not None else 0


def model_options(
    models: Sequence[DockerModelConfig], accelerator_memory_gib: int
) -> list[ModelOption]:
    """Configured models with a fit verdict for *accelerator_memory_gib* (0 = unknown)."""
    options: list[ModelOption] = []
    for entry in models:
        needed = entry.weight_gib + SANDBOX_HEADROOM_GIB
        fits = None if accelerator_memory_gib <= 0 else needed <= accelerator_memory_gib
        options.append(
            ModelOption(
                id=entry.id,
                model=entry.model,
                name=entry.name,
                description=entry.description,
                weight_gib=entry.weight_gib,
                recommended=entry.recommended,
                fits=fits,
                memory_needed_gib=needed,
            )
        )
    return options
