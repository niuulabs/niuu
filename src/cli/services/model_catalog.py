"""Curated local models the wizard offers for vLLM on a single GPU host.

Sizes are what vLLM reserves for weights plus a 64k-token KV cache, rounded
up, so a "fits" verdict leaves room for session sandboxes. Anything not
listed can still be entered as a custom Hugging Face id.
"""

from __future__ import annotations

from dataclasses import dataclass

from niuu.domain.stack import ModelOption

# Memory kept free for the platform and session sandboxes when judging fit.
SANDBOX_HEADROOM_GIB = 12


@dataclass(frozen=True)
class CuratedModel:
    id: str
    model: str
    name: str
    description: str
    weight_gib: int
    recommended: bool = False


CURATED_MODELS: tuple[CuratedModel, ...] = (
    CuratedModel(
        id="nemotron-3-nano-30b",
        model="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16",
        name="NVIDIA Nemotron 3 Nano 30B",
        description="Fast agentic coder tuned by NVIDIA. Best default for sessions and residents.",
        weight_gib=62,
        recommended=True,
    ),
    CuratedModel(
        id="gpt-oss-120b",
        model="openai/gpt-oss-120b",
        name="OpenAI gpt-oss-120b",
        description="Larger reasoning model. Slower per token, stronger on planning.",
        weight_gib=78,
    ),
    CuratedModel(
        id="qwen3-coder-30b",
        model="Qwen/Qwen3-Coder-30B-A3B-Instruct",
        name="Qwen3-Coder 30B-A3B",
        description="Lean coding model with generous headroom for long contexts.",
        weight_gib=24,
    ),
)


def model_options(accelerator_memory_gib: int) -> list[ModelOption]:
    """Curated models with a fit verdict for *accelerator_memory_gib* (0 = unknown)."""
    options: list[ModelOption] = []
    for entry in CURATED_MODELS:
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
