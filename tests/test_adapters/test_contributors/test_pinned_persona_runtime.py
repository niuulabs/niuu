from __future__ import annotations

import yaml

from ravn.adapters.personas.loader import PersonaConfig
from ravn.domain.persona_document import portable_persona_from_config
from volundr.adapters.outbound.contributors.ravn_flock import _build_ravn_config


def test_ravn_sidecar_uses_inline_pinned_persona_source() -> None:
    document = portable_persona_from_config(
        PersonaConfig(name="reviewer", system_prompt_template="Pinned behavior")
    )

    config = yaml.safe_load(
        _build_ravn_config(
            persona="review",
            persona_override={
                "name": "review",
                "portable_definition": document.to_dict(),
            },
            global_llm=None,
            index=1,
            peer_id="review-peer",
            base_port=7480,
            all_personas=["review"],
            skuld_peer_id="skuld-peer",
            static_mesh_peers=[],
            mimir_config={},
            sleipnir_publish_urls=[],
            persona_source_mode="http",
            persona_source_http_base_url="https://mutable-catalog.example",
        )
    )

    assert config["persona_source"] == {
        "adapter": "ravn.adapters.personas.inline.InlinePersonaAdapter",
        "kwargs": {"definitions": {"review": document.to_dict()}},
    }
