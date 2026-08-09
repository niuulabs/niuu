"""Configuration for the Mímir service.

``MimirServiceConfig`` loads from (first wins):

1. explicit constructor arguments (the ``serve`` CLI flags)
2. ``MIMIR__``-prefixed environment variables with ``__`` nesting, e.g.
   ``MIMIR__EMBEDDING_MODEL=all-MiniLM-L6-v2``,
   ``MIMIR__RANKING__TITLE_MATCH_BOOST=1.5``, ``MIMIR__EVAL_CAPTURE=false``
3. a YAML config file: ``$MIMIR_CONFIG`` if set, else ``./mimir.yaml`` or
   ``/etc/mimir/config.yaml``

This makes every setting reachable in every deployment mode — including the
platform plugin, which constructs the config with no arguments.
"""

from __future__ import annotations

import os
from pathlib import Path as _Path

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


def _config_paths() -> list[_Path]:
    env = os.environ.get("MIMIR_CONFIG")
    if env:
        return [_Path(env)]
    return [
        _Path("./mimir.yaml"),
        _Path("/etc/mimir/config.yaml"),
    ]


CONFIG_PATHS = _config_paths()


class RankingConfig(BaseModel):
    """Tunable weights for the post-retrieval ranking layer (NIU-1057/1058/1062).

    All boosts are multiplicative on the base retrieval score. Every value
    here exists so nothing is hardcoded in business logic; defaults are the
    eval-validated starting points.
    """

    enabled: bool = Field(default=True, description="Apply domain ranking boosts to search.")
    overfetch_factor: int = Field(
        default=4,
        description="Candidates fetched from the search port per requested result.",
    )
    recency_half_life_days: float = Field(
        default=90.0,
        description="Page score halves every N days since last update.",
    )
    recency_floor: float = Field(
        default=0.5,
        description="Minimum recency factor so old-but-relevant pages still surface.",
    )
    title_match_boost: float = Field(
        default=1.25,
        description="Boost when all query tokens appear in the page title.",
    )
    confidence_boosts: dict[str, float] = Field(
        default={},
        description=(
            "Multiplier per frontmatter confidence level. Defaults to empty "
            "(neutral): the NIU-1057 hybrid ablation measured the "
            "high=1.15/low=0.85 preset at -7.0 pts P@5 — high-confidence "
            "pages crowded out correct answers. Opt in per deployment."
        ),
    )
    page_type_weights: dict[str, float] = Field(
        default={"directive": 1.1, "decision": 1.1},
        description="Multiplier per page type; unlisted types get 1.0.",
    )
    backlink_alpha: float = Field(
        default=0.0,
        description=(
            "Backlink centrality boost strength: 1 + alpha*log1p(backlinks). "
            "Defaults to neutral: the NIU-1057 hybrid ablation measured "
            "alpha=0.1 at -4.6 pts P@5 (hub pages crowd out answers) and "
            "+0.0 in FTS-only mode. Opt in per deployment."
        ),
    )
    zone_weights: dict[str, float] = Field(
        default={"timeline": 0.9},
        description=(
            "Chunk-level zone multipliers by section (hierarchical recall, "
            "NIU-1062): compiled-truth chunks default to 1.0, timeline "
            "evidence is slightly demoted."
        ),
    )
    graph_injection_base: float = Field(
        default=0.3,
        description=(
            "Base score for pages injected by the relational arm (entity "
            "match + 1-hop neighbors) when retrieval missed them entirely."
        ),
    )
    graph_neighbor_boost: float = Field(
        default=1.0,
        description=(
            "Boost for retrieved pages that are 1-hop from a query-matched "
            "entity. Defaults to neutral: the NIU-1057 ablation showed 1.2 "
            "cost MRR -0.049 with no P@5 gain on the golden set."
        ),
    )
    graph_entity_boost: float = Field(
        default=1.5,
        description="Boost for a page whose entity is named in the query.",
    )


class EvidenceConfig(BaseModel):
    """Thresholds for evidence-counted beliefs (NIU-1062).

    Deterministic — every value here drives the zero-LLM evidence pass that
    computes proof counts and freshness trends for compiled-truth facts.
    """

    min_token_overlap: int = Field(
        default=2,
        description="Significant tokens a timeline entry must share with a fact to support it.",
    )
    stale_after_days: float = Field(
        default=90.0,
        description="No supporting evidence newer than this → trend 'stale'.",
    )
    weakening_after_days: float = Field(
        default=60.0,
        description="No supporting evidence newer than this → trend 'weakening'.",
    )
    strengthening_min_proofs: int = Field(
        default=3,
        description="Fresh facts with at least this many proofs trend 'strengthening'.",
    )
    consolidate_on_ingest: bool = Field(
        default=True,
        description=(
            "Run write-time scoped consolidation (the 'micro-dream') after "
            "every source ingest: bearing pages get their evidence recomputed "
            "immediately instead of waiting for the nightly dream cycle."
        ),
    )


class MimirServiceConfig(BaseSettings):
    """Configuration for a Mímir service instance (standalone or plugin).

    See the module docstring for the YAML/env/constructor source order.
    """

    model_config = SettingsConfigDict(
        yaml_file=CONFIG_PATHS,
        yaml_file_encoding="utf-8",
        env_prefix="MIMIR__",
        env_nested_delimiter="__",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    path: str = Field(
        default="~/.ravn/mimir",
        description="Root directory for the Mímir knowledge base.",
    )
    host: str = Field(
        default="0.0.0.0",
        description="Host address to bind the service to.",
    )
    port: int = Field(
        default=7477,
        description="Port to bind the service to.",
    )
    name: str = Field(
        default="local",
        description="Instance name used in Sleipnir announce events.",
    )
    role: str = Field(
        default="local",
        description="Instance role: 'shared', 'local', or 'domain'.",
    )
    categories: list[str] | None = Field(
        default=None,
        description="Category filter for domain-scoped Mímirs. None means all categories.",
    )
    announce_url: str | None = Field(
        default=None,
        description=(
            "Public URL this service is reachable at, announced on Sleipnir. "
            "If None, announcement is skipped."
        ),
    )
    search_db: str | None = Field(
        default=None,
        description=(
            "Path to the SQLite database for the hybrid search index. "
            "Defaults to <path>/search.db when None."
        ),
    )
    embedding_model: str | None = Field(
        default=None,
        description=(
            "Embedding model for semantic search. With embedding_base_url set "
            "this is the model name sent to that endpoint (e.g. "
            "'Qwen/Qwen3-Embedding-0.6B'); without it, a sentence-transformers "
            "model loaded in-process (e.g. 'all-MiniLM-L6-v2'). "
            "Null means FTS-only, which is a deliberate choice, not a fallback."
        ),
    )
    embedding_base_url: str = Field(
        default="",
        description=(
            "OpenAI-compatible /v1 base URL serving embeddings (vLLM, TGI, "
            "Ollama, OpenAI). Preferred over loading a model in-process: it "
            "needs only httpx, so no heavy dependency in the image."
        ),
    )
    embedding_api_key: str = Field(
        default="",
        description="Bearer token for embedding_base_url. Empty for unauthenticated servers.",
    )
    eval_capture: bool = Field(
        default=True,
        description=(
            "Append every /mimir/search query and its result paths to "
            "<path>/evals/queries-YYYY-Www.jsonl. Feeds the Analytics query "
            "traffic view and offline replay (python -m mimir eval replay). "
            "On by default; disable for privacy-sensitive deployments."
        ),
    )
    ranking: RankingConfig = Field(
        default_factory=RankingConfig,
        description="Post-retrieval ranking boosts (NIU-1057/1058/1062).",
    )
    evidence: EvidenceConfig = Field(
        default_factory=EvidenceConfig,
        description="Evidence-counted belief thresholds (NIU-1062).",
    )
