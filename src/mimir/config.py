"""Configuration for the standalone Mímir service."""

from __future__ import annotations

from pydantic import BaseModel, Field


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


class MimirServiceConfig(BaseModel):
    """Configuration for a standalone Mímir service instance."""

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
            "sentence-transformers model name for semantic search "
            "(e.g. 'all-MiniLM-L6-v2'). "
            "Set to null for FTS-only mode (no sentence-transformers required)."
        ),
    )
    eval_capture: bool = Field(
        default=False,
        description=(
            "When true, every /mimir/search query and its result paths are "
            "appended to <path>/evals/queries-YYYY-Www.jsonl for offline "
            "retrieval-quality replay (python -m mimir eval replay)."
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
