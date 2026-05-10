"""Configuration settings for Tyr.

Configuration is loaded from YAML, with environment variables overriding.

Config file locations (first found wins):
- ./tyr.yaml
- /etc/tyr/config.yaml

Environment variable override format:
- Use double underscore for nested fields: DATABASE__HOST
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from niuu.config import CorsConfig


# Config file search paths (in order of priority).
# NIUU_CONFIG env var (set by the CLI --config flag) takes precedence.
def _config_paths() -> list[Path]:
    env = os.environ.get("NIUU_CONFIG")
    if env:
        return [Path(env)]
    return [
        Path("./tyr.yaml"),
        Path("/etc/tyr/config.yaml"),
    ]


CONFIG_PATHS = _config_paths()
BUNDLED_FLOCK_FLOWS_PATH = (Path(__file__).parent / "flock_flows.yaml").resolve()


class DatabaseConfig(BaseModel):
    """PostgreSQL database configuration."""

    host: str = Field(default="localhost")
    port: int = Field(default=5432)
    user: str = Field(default="tyr")
    password: str = Field(default="tyr")
    name: str = Field(default="tyr")
    min_pool_size: int = Field(default=5)
    max_pool_size: int = Field(default=20)

    @property
    def dsn(self) -> str:
        """Return PostgreSQL connection string."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field(default="info")
    format: str = Field(default="text")


class VolundrConfig(BaseModel):
    """Volundr API connection configuration."""

    url: str = Field(default="http://localhost:8080")
    use_connection_factory_in_dev: bool = Field(
        default=False,
        description=(
            "When True, anonymous dev mode still resolves Volundr adapters from "
            "stored CODE_FORGE connections instead of forcing the single local "
            "Volundr adapter. Useful for a local Tyr controlling multiple remote "
            "Volundr instances."
        ),
    )
    trusted_connection_test_urls: list[str] = Field(
        default_factory=list,
        description=(
            "Additional trusted Volundr API base URLs that Tyr may contact for "
            "server-side credentialed code_forge connection tests."
        ),
    )


class CredentialStoreConfig(BaseModel):
    """Dynamic credential store adapter configuration."""

    adapter: str = Field(
        default="niuu.adapters.memory_credential_store.MemoryCredentialStore",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


class AIModelConfig(BaseModel):
    """Available AI model — configured via Helm values.

    Mirrors niuu.domain.models.AIModelConfig but as a pydantic model
    for settings deserialization.
    """

    id: str
    name: str
    provider: str = Field(
        default="",
        description=(
            "Model provider/vendor (e.g. 'anthropic', 'openai', 'google'). "
            "Used to filter the model dropdown by the chosen session runtime."
        ),
    )


class ReviewConfig(BaseModel):
    """Confidence deltas for raid review actions."""

    confidence_delta_approved: float = Field(default=0.15)
    confidence_delta_rejected: float = Field(default=-0.20)
    confidence_delta_retry: float = Field(default=-0.05)
    initial_confidence: float = Field(
        default=0.5,
        description="Starting confidence score for newly committed sagas, phases, and raids.",
    )
    auto_approve_threshold: float = Field(
        default=0.80,
        description="Confidence threshold above which raids are auto-merged.",
    )
    max_retries: int = Field(
        default=3,
        description="Maximum auto-retries before escalation to human review.",
    )
    scope_breach_threshold: float = Field(
        default=0.30,
        description="Fraction of undeclared changed files that flags a scope breach.",
    )
    confidence_delta_ci_pass: float = Field(default=0.30)
    confidence_delta_ci_fail: float = Field(default=-0.30)
    confidence_delta_mergeable: float = Field(default=0.10)
    confidence_delta_conflict: float = Field(default=-0.20)
    confidence_delta_scope_breach: float = Field(default=-0.25)
    confidence_delta_retry_multiplier: float = Field(
        default=-0.05,
        description="Per-retry confidence penalty (multiplied by retry_count).",
    )
    reviewer_session_enabled: bool = Field(
        default=True,
        description="Spawn an LLM-powered reviewer session for raids in REVIEW state.",
    )
    reviewer_model: str = Field(
        default="claude-opus-4-6",
        description="AI model for reviewer sessions.",
    )
    reviewer_profile: str = Field(
        default="reviewer",
        description="Volundr profile name for reviewer sessions.",
    )
    reviewer_confidence_weight: float = Field(
        default=0.60,
        description="Weight applied to the reviewer's confidence score (0.0–1.0).",
    )
    reviewer_spawn_bonus: float = Field(
        default=0.1,
        description="Small confidence bonus applied when a reviewer session is spawned.",
    )
    max_review_rounds: int = Field(
        default=6,
        ge=6,
        description="Maximum review rounds before escalating. Minimum 6.",
    )
    ravn_arbiter_enabled: bool = Field(
        default=False,
        description=(
            "When True, ReviewEngine dispatches to the review-arbiter ravn persona "
            "before falling back to imperative signal-based decisions."
        ),
    )
    ravn_arbiter_model: str = Field(
        default="claude-sonnet-4-6",
        description="AI model used by the review-arbiter ravn persona.",
    )
    ravn_arbiter_timeout: float = Field(
        default=60.0,
        description="HTTP timeout in seconds for review-arbiter ravn dispatch calls.",
    )
    reviewer_system_prompt: str = Field(
        default=(
            "You are a senior code reviewer for the Niuu platform. You do not just check\n"
            "rules — you READ the code, UNDERSTAND it, and provide substantive feedback\n"
            "on quality, design, and correctness.\n"
            "\n"
            "## Setup\n"
            "\n"
            "1. Read CLAUDE.md and `.claude/rules/` — they are the authoritative project rules.\n"
            "2. Ensure tools are available:\n"
            "   - `gh` (GitHub CLI): check `~/` or PATH. Install if missing (`brew install gh`).\n"
            "   - Linear MCP server may be available. If not, use LINEAR_API_KEY env.\n"
            "\n"
            "## Review Process\n"
            "\n"
            "Read the full diff and review EVERY changed file across three dimensions:\n"
            "\n"
            "### 1. Code Reuse\n"
            "- Search the codebase for existing utilities that could replace newly written code.\n"
            "- Flag new functions that duplicate existing functionality —"
            " suggest the existing one.\n"
            "- Flag inline logic that could use an existing utility (string manipulation, path\n"
            "  handling, type guards, etc.).\n"
            "\n"
            "### 2. Code Quality\n"
            "- **Redundant state**: state duplicating other state, values that could be derived.\n"
            "- **Parameter sprawl**: adding params instead of restructuring.\n"
            "- **Copy-paste with variation**: near-duplicate blocks that should be unified.\n"
            "- **Leaky abstractions**: exposing internals, breaking abstraction boundaries.\n"
            "- **Stringly-typed code**: raw strings where constants or enums exist.\n"
            "- **Unnecessary comments**: comments explaining WHAT (delete), keep only WHY.\n"
            "- **Architecture violations**: wrong layer imports, missing port/adapter separation.\n"
            "\n"
            "### 3. Efficiency\n"
            "- **Unnecessary work**: redundant computations, repeated reads, N+1 patterns.\n"
            "- **Missed concurrency**: independent operations run sequentially.\n"
            "- **Hot-path bloat**: blocking work on startup or per-request paths.\n"
            "- **Memory**: unbounded data structures, missing cleanup.\n"
            "- **Overly broad operations**: reading entire files when a portion suffices.\n"
            "\n"
            "### 4. Correctness & Safety\n"
            "- Verify acceptance criteria are met.\n"
            "- Check the PR targets the feature branch, NOT `main`.\n"
            "- Check `codecov/patch` — it is a hard gate, not advisory.\n"
            "- Look for edge cases, error handling gaps, and security issues.\n"
            "- Verify conventional commit messages.\n"
            "\n"
            "## Every Finding Must Be Addressed\n"
            "\n"
            "Every finding — bugs, quality issues, reuse opportunities, efficiency\n"
            "improvements — is blocking. If you find it worth mentioning, the working\n"
            "session must fix it before the PR can merge.\n"
            "\n"
            "For each finding, suggest a specific fix — don't just say what's wrong,\n"
            "say how to fix it. Reference file names and line numbers.\n"
            "\n"
            "## Confidence Scoring\n"
            "\n"
            "| Score | Meaning |\n"
            "|-------|---------|\n"
            "| 1.0 | No findings — clean code, ready to merge. |\n"
            "| 0.80–0.99 | Minor findings — fixable in one round. |\n"
            "| 0.50–0.79 | Significant findings — needs rework. |\n"
            "| <0.50 | Fundamental issues — architecture or design problems. |\n"
            "\n"
            "Only approve (`approved: true`) when `findings` is empty.\n"
            "\n"
            "## Response Format\n"
            "\n"
            "```json\n"
            "{\n"
            '  "confidence": <0.0–1.0>,\n'
            '  "approved": <true only if findings is empty and PR is merged>,\n'
            '  "summary": "<one-line summary of the review>",\n'
            '  "findings": [\n'
            '    "file:line — [category] description and suggested fix"\n'
            "  ]\n"
            "}\n"
            "```\n"
            "\n"
            "Categories: `[bug]`, `[security]`, `[architecture]`, `[reuse]`,\n"
            "`[quality]`, `[efficiency]`, `[test]`, `[style]`."
        ),
        description="System prompt for reviewer sessions.",
    )
    reviewer_initial_prompt_template: str = Field(
        default=(
            "## Review Request\n"
            "\n"
            "**Ticket**: {tracker_id}\n"
            "**Raid**: {raid_name}\n"
            "**Description**: {raid_description}\n"
            "\n"
            "{acceptance_criteria_section}"
            "{pr_section}"
            "{changed_files_section}"
            "{diff_summary_section}"
            "## Instructions\n"
            "\n"
            "1. Read CLAUDE.md and `.claude/rules/` for project conventions.\n"
            "2. Read the full diff — every changed file, not just the summary.\n"
            "3. For each changed file, also read the SURROUNDING code (not just the diff\n"
            "   lines) to understand context and spot missed reuse opportunities.\n"
            "4. Search the codebase for existing utilities that overlap with new code.\n"
            "5. Verify acceptance criteria are met.\n"
            "6. Verify PR targets the feature branch, not `main`.\n"
            "7. Check CI status — `codecov/patch` is a hard gate (85%).\n"
            "\n"
            "{review_loop_section}"
            "## Merging\n"
            "\n"
            "When satisfied (no findings remaining),\n"
            "merge the PR before outputting your assessment:\n"
            "\n"
            "```bash\n"
            "gh pr merge --squash --delete-branch\n"
            "```\n"
            "\n"
            "If `gh` is not found, install it (`brew install gh` or `apt install gh`).\n"
            "If merge fails, set approved=false and explain why.\n"
            "\n"
            "## Final Output\n"
            "\n"
            "```json\n"
            "{{\n"
            '  "confidence": <score>,\n'
            '  "approved": <true only if findings is empty and PR merged>,\n'
            '  "summary": "<one line>",\n'
            '  "findings": ["file:line — [category] description and fix"]\n'
            "}}\n"
            "```"
        ),
        description=(
            "Template for the reviewer's initial prompt. Dynamic sections: "
            "{tracker_id}, {raid_name}, {raid_description}, "
            "{acceptance_criteria_section}, {pr_section}, {changed_files_section}, "
            "{diff_summary_section}, {review_loop_section}."
        ),
    )


class GitConfig(BaseModel):
    """Git provider configuration."""

    token: str = Field(default="")


class PlannerConfig(BaseModel):
    """Planning session configuration."""

    planner_system_prompt: str = Field(
        default=(
            "You are a saga planning assistant for the Niuu platform.\n"
            "\n"
            "Help the user decompose a feature specification into phases and raids\n"
            "(discrete, independently mergeable tasks).\n"
            "\n"
            "## Sizing\n"
            "\n"
            "Use t-shirt sizing for raids:\n"
            "- **S** (Small): well-bounded, single file or function change\n"
            "- **M** (Medium): a few files, clear scope, independently testable\n"
            "- **L** (Large): too big — MUST be decomposed into its own phase\n"
            "\n"
            "Anything larger than M should become its own milestone (phase) with\n"
            "smaller raids inside. Prefer many small, independent tasks.\n"
            "\n"
            "## Constraints\n"
            "\n"
            "- Each raid must be independently testable and mergeable.\n"
            "- Phases run sequentially. Within a phase, raids run in parallel\n"
            "  unless `depends_on` declares an ordering.\n"
            "- Order phases: foundations first, features next, polish last.\n"
            "- Every raid needs acceptance criteria and `declared_files`.\n"
            "\n"
            "## Process\n"
            "\n"
            "1. Ask clarifying questions if the spec is ambiguous.\n"
            "2. Propose a phased breakdown with t-shirt sized raids.\n"
            "3. Iterate with the user until they are satisfied.\n"
            "4. When the user says 'finalize', output the structure as JSON.\n"
            "\n"
            "Repository: {repo}\n"
            "Base branch: {base_branch}\n"
            "Specification:\n{spec}"
        ),
        description=(
            "System prompt for the interactive planning session. "
            "Available placeholders: {repo}, {base_branch}, {spec}."
        ),
    )
    finalize_prompt: str = Field(
        default=(
            "Please finalize the plan now. Output the saga structure as a JSON code block:\n"
            "\n"
            "```json\n"
            "{\n"
            '  "name": "Saga Name",\n'
            '  "phases": [\n'
            "    {\n"
            '      "name": "Phase 1",\n'
            '      "raids": [\n'
            "        {\n"
            '          "name": "Raid name",\n'
            '          "description": "What this raid does",\n'
            '          "acceptance_criteria": ["criterion 1", "criterion 2"],\n'
            '          "declared_files": ["src/path/file.py"],\n'
            '          "size": "S",\n'
            '          "depends_on": ["Other raid name"]\n'
            "        }\n"
            "      ]\n"
            "    }\n"
            "  ]\n"
            "}\n"
            "```\n"
            "\n"
            "Requirements:\n"
            "- Every raid needs name, description, and acceptance criteria.\n"
            "- `declared_files`: likely files the raid will touch.\n"
            "- `size`: S or M. If L, split it into its own phase.\n"
            "- `depends_on`: optional, use when a raid waits for another (by name, same phase)."
        ),
        description="Prompt injected when the user clicks Finalize Plan.",
    )


class PersonaOverride(BaseModel):
    """Per-persona LLM and iteration override for flock dispatch.

    Accepted in ``FlockConfig.default_personas`` alongside bare strings (legacy).
    The ``llm`` dict is forwarded verbatim as the per-persona ``llm`` key in
    ``workload_config.personas``; ravn merges it over the global ``llm_config``.
    """

    name: str = Field(description="Ravn persona name (e.g. 'coordinator', 'reviewer').")
    llm: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-persona LLM override merged over the global llm_config.",
    )
    system_prompt_extra: str | None = Field(
        default=None,
        description="Extra instructions appended to this persona's system prompt.",
    )
    iteration_budget: int | None = Field(
        default=None,
        description="Max iterations for this persona; overrides the persona's own default.",
    )
    consumes_event_types: list[str] = Field(
        default_factory=list,
        description=(
            "Optional replacement event subscription list for this persona within a flock. "
            "Use this to narrow auto-triggered personas to a single canonical path."
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the wire dict format consumed by workload_config.personas."""
        d: dict[str, Any] = {"name": self.name}
        if self.llm:
            d["llm"] = dict(self.llm)
        if self.system_prompt_extra:
            d["system_prompt_extra"] = self.system_prompt_extra
        if self.iteration_budget is not None:
            d["iteration_budget"] = self.iteration_budget
        if self.consumes_event_types:
            d["consumes_event_types"] = list(self.consumes_event_types)
        return d


class FlockConfig(BaseModel):
    """Flock dispatch configuration."""

    enabled: bool = Field(
        default=False,
        description="When True, eligible raids are dispatched as ravn_flock sessions.",
    )
    default_personas: list[PersonaOverride] = Field(
        default_factory=lambda: [
            PersonaOverride(name="coordinator"),
            PersonaOverride(name="coder"),
            PersonaOverride(name="reviewer"),
        ],
        description=(
            "Ravn persona names included in every flock session. "
            "Accepts bare strings (legacy) or per-persona override objects."
        ),
    )
    default_workflow_name: str = Field(
        default="Tyr Raid Flow",
        description=(
            "System Tyr workflow automatically assigned to flock-enabled sagas "
            "when no explicit workflow is selected."
        ),
    )

    @field_validator("default_personas", mode="before")
    @classmethod
    def _coerce_personas(cls, v: Any) -> list[Any]:
        """Accept both legacy list[str] and new list[dict|PersonaOverride]."""
        if not isinstance(v, list):
            return v
        result = []
        for item in v:
            if isinstance(item, str):
                result.append({"name": item})
            else:
                result.append(item)
        return result

    mimir_hosted_url: str = Field(
        default="",
        description="URL of the Mimir knowledge base for coordinator context queries.",
    )
    mimir_registry_path: str = Field(
        default="~/.ravn/mimir/.mimir-registry.json",
        description=(
            "Path to the persisted Mimir registry used to resolve registry-backed "
            "workflow resource mounts into concrete path/url runtime config."
        ),
    )
    sleipnir_publish_urls: list[str] = Field(
        default_factory=list,
        description="Sleipnir publish URLs for flock task event routing.",
    )
    llm_config: dict = Field(
        default_factory=dict,
        description=(
            "LLM provider config for ravn flock nodes. "
            "Dict matching ravn's `llm:` config block (model, max_tokens, timeout, provider). "
            "When empty, ravn nodes use their own default or image-baked config."
        ),
    )


class InProcessDispatchConfig(BaseModel):
    """In-process (single-turn) dispatch configuration.

    Controls the LLM used by :class:`~tyr.adapters.ravn_dispatcher.RavnDispatcher`
    when running single-turn agent calls in-process (as opposed to spinning up a
    flock pod).

    Fallback chain for ``llm_config``:
      1. ``dispatch.in_process.llm_config`` — if non-empty, use this.
      2. ``dispatch.flock.llm_config`` — if in_process is absent/empty, mirror
         flock config so both paths use the same model without duplication.

    Example YAML::

        dispatch:
          flock:
            llm_config:
              model: claude-opus-4-6
              max_tokens: 8192
          # Optional: override model for cheaper in-process single-turn calls.
          in_process:
            llm_config:
              model: claude-sonnet-4-6
              max_tokens: 4096
    """

    llm_config: dict = Field(
        default_factory=dict,
        description=(
            "LLM config for in-process RavnDispatcher calls. "
            "Dict matching ravn's `llm:` config block (model, max_tokens, timeout). "
            "When empty, falls back to dispatch.flock.llm_config."
        ),
    )


class DispatchConfig(BaseModel):
    """Dispatcher configuration."""

    default_system_prompt: str = Field(default="")
    default_model: str = Field(default="claude-sonnet-4-6")
    default_session_definition: str = Field(
        default="",
        description=(
            "Default session_definition (runtime) key when a dispatch request "
            "doesn't specify one — e.g. 'skuldClaude', 'skuldCodex', "
            "'skuldOpenCode'. Empty falls through to volundr's "
            "defaultDefinition (typically skuldClaude)."
        ),
    )
    auto_continue: bool = Field(
        default=False,
        description=(
            "Seed value for the per-owner DispatcherState.auto_continue flag "
            "the first time it's created. Once a row exists in dispatcher_state "
            "this config is not consulted — the API/UI is the source of truth. "
            "Set this to true in solo-dev setups so newly-spawned owners "
            "auto-pick the next ready issue after a phase gate unlocks."
        ),
    )
    flock: FlockConfig = Field(default_factory=FlockConfig)
    in_process: InProcessDispatchConfig = Field(default_factory=InProcessDispatchConfig)
    dispatch_prompt_template: str = Field(
        default=(
            "# Task: {identifier} — {title}\n"
            "\n"
            "{description}\n"
            "\n"
            "Repository: {repo}\n"
            "Feature branch: {feature_branch}\n"
            "Create a working branch for your changes: `{raid_branch}`\n"
            "\n"
            "## Before You Start\n"
            "\n"
            "1. Read the CLAUDE.md and any `.claude/rules/` files — they contain project\n"
            "   conventions you MUST follow.\n"
            "2. Explore the existing codebase in the areas you will change.\n"
            "3. Understand the architecture before writing code.\n"
            "4. Ensure required tools are available:\n"
            "   - `gh` (GitHub CLI) — check `~/` or install via"
            " `brew install gh` / `apt install gh`\n"
            "   - `git` — must be configured with push access\n"
            "   - If a tool is missing, install it before proceeding.\n"
            "\n"
            "## Completion Requirements\n"
            "\n"
            "1. **Update the issue tracker**: Set ticket `{identifier}` to **In Progress**.\n"
            "2. **Implement the task**: Write code and tests, ensure coverage >= 85%.\n"
            "3. **Commit your changes**: Use conventional commits (see CLAUDE.md).\n"
            "4. **Create a PR against `{feature_branch}`** (NOT `main`): include a summary\n"
            "   of all changes in the PR description.\n"
            "5. **Wait for CI**: All checks must pass (tests, lint, coverage).\n"
            "   `codecov/patch` is a hard gate — if it fails, fix coverage and push again.\n"
            "6. **Update the issue tracker**: Add a comment on `{identifier}` with a summary\n"
            "   of what was done and a link to the PR.\n"
            "\n"
            "**Do NOT stop until the PR is created and CI is green.**"
        ),
        description=(
            "Template for the initial prompt sent to coding sessions. "
            "Available placeholders: {identifier}, {title}, {description}, "
            "{repo}, {feature_branch}, {raid_branch}. "
            "Override in tyr.yaml or Helm values."
        ),
    )


class CerbosConfig(BaseModel):
    """Cerbos authorization service configuration."""

    url: str = Field(default="http://localhost:3592")


class PATConfig(BaseModel):
    """Personal access token configuration (matches Volundr's PATConfig)."""

    token_issuer_adapter: str = Field(
        default="niuu.adapters.memory_token_issuer.MemoryTokenIssuer",
        description="Fully-qualified class path for the token issuer adapter.",
    )
    token_issuer_kwargs: dict = Field(
        default_factory=dict,
        description="Kwargs passed to the token issuer adapter constructor.",
    )
    ttl_days: int = Field(
        default=365,
        description="Default PAT lifetime in days.",
    )
    revocation_cache_ttl: float = Field(
        default=300.0,
        description="Seconds to cache valid-token lookups before re-checking the DB.",
    )
    revoked_cache_ttl: float = Field(
        default=60.0,
        description="Seconds to cache revoked-token lookups (shorter for fast propagation).",
    )


class AuthConfig(BaseModel):
    """Authentication configuration."""

    allow_anonymous_dev: bool = Field(
        default=False,
        description=(
            "When True, requests without auth headers fall back to a default developer "
            "identity. Must be False in production."
        ),
    )
    default_user_id: str = Field(
        default="dev-user",
        description="User ID for anonymous dev mode fallback.",
    )


class WebhookConfig(BaseModel):
    """Outbound webhook integration for raid/saga lifecycle notifications.

    Fires whenever the NotificationService dispatches an event for the
    seeded owner — REVIEW (raid ready for review), MERGED (raid merged),
    FAILED (raid failed). The same set of events Telegram receives.
    """

    url: str = Field(
        default="",
        description="POST target URL. Empty disables webhook seeding.",
    )
    secret: str = Field(
        default="",
        description=(
            "Optional HMAC-SHA256 secret. When set, the body is signed and "
            "the digest is sent in the X-Niuu-Signature header as "
            "'sha256=<hex>'. Receivers should verify."
        ),
    )
    min_urgency: str = Field(
        default="low",
        description="Minimum urgency: 'low' | 'medium' | 'high'.",
    )


class TelegramConfig(BaseModel):
    """Telegram bot configuration for deeplink setup and webhook commands."""

    bot_username: str = Field(default="TyrBot")
    bot_token: str = Field(
        default="",
        description="Telegram Bot API token — required for webhook replies.",
    )
    webhook_secret: str = Field(
        default="",
        description=(
            "Secret token set when registering the webhook with Telegram. "
            "Telegram sends it as X-Telegram-Bot-Api-Secret-Token header. "
            "When non-empty, requests without a matching header are rejected with 403."
        ),
    )
    reply_timeout: float = Field(
        default=10.0,
        description="Timeout in seconds for Telegram Bot API reply calls.",
    )
    polling: bool = Field(
        default=False,
        description=(
            "Enable long-polling (getUpdates) instead of relying on Telegram "
            "calling our public webhook URL. Useful for local dev where the "
            "platform isn't reachable from the internet. When enabled, "
            "incoming updates are re-POSTed to /api/v1/tyr/telegram/webhook "
            "in-process so command handlers stay shared with the webhook path."
        ),
    )
    polling_self_url: str = Field(
        default="",
        description=(
            "Base URL the polling shim re-POSTs updates to. When empty, "
            "resolved at startup from NIUU_SERVER_HOST and NIUU_SERVER_PORT "
            "(set by start-dev) so the URL matches the platform's bound "
            "interface — sending to 127.0.0.1 fails when uvicorn binds to a "
            "specific NIC."
        ),
    )
    hmac_key: str = Field(default="")
    hmac_signature_length: int = Field(
        default=32,
        description="Number of hex characters to use from the HMAC-SHA256 signature.",
    )


class LLMConfig(BaseModel):
    """LLM adapter configuration (dynamic adapter pattern)."""

    adapter: str = Field(
        default="tyr.adapters.bifrost.BifrostAdapter",
        description="Fully-qualified class path for the LLM adapter.",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)
    default_model: str = Field(default="claude-sonnet-4-6")
    min_estimate_hours: float = Field(default=2.0)
    max_estimate_hours: float = Field(default=8.0)
    decomposition_system_prompt: str = Field(
        default="",
        description=(
            "System prompt for LLM-powered saga decomposition. "
            "Available placeholders: {repo}, {spec}. "
            "When empty, the built-in DECOMPOSITION_PROMPT in bifrost.py is used."
        ),
    )
    budget_tokens: int = Field(
        default=0,
        description=(
            "Cumulative token budget per BifrostAdapter instance. "
            "0 means unlimited. When exceeded, bifrost.quota.exceeded is emitted."
        ),
    )
    quota_warning_threshold: float = Field(
        default=0.8,
        description=(
            "Fraction of budget_tokens at which bifrost.quota.warning is emitted. "
            "Must be between 0.0 and 1.0. Default: 0.8 (80%)."
        ),
    )
    agent_id: str = Field(
        default="",
        description=(
            "Optional identifier for the agent/saga making LLM calls. "
            "Used as correlation_id in Sleipnir events."
        ),
    )
    ravn_decomposer_enabled: bool = Field(
        default=False,
        description=(
            "When True, BifrostAdapter dispatches to the decomposer ravn persona "
            "before falling back to the direct Anthropic API call."
        ),
    )
    ravn_decomposer_timeout: float = Field(
        default=120.0,
        description="HTTP timeout in seconds for decomposer ravn dispatch calls.",
    )


class LinearConfig(BaseModel):
    """Linear tracker configuration for mini/local mode.

    When api_key is set, a Linear integration is auto-seeded on startup
    so the tracker factory can resolve it without manual UI setup.
    Matches the Go CLI's ``linear:`` config block.
    """

    api_key: str = Field(default="", description="Linear API key.")
    team_id: str = Field(default="", description="Optional Linear team ID filter.")


class TrackerConfig(BaseModel):
    """Tracker adapter configuration."""

    cache_ttl_seconds: float = Field(default=30.0)
    rate_limit_max_retries: int = Field(default=3)


class WatcherConfig(BaseModel):
    """Raid completion watcher configuration."""

    enabled: bool = Field(default=True)
    poll_interval: float = Field(default=30.0, description="Seconds between polls.")
    batch_size: int = Field(default=10, description="Max concurrent session checks.")
    chronicle_on_complete: bool = Field(
        default=True, description="Fetch chronicle summary on completion."
    )
    idle_threshold: float = Field(
        default=30.0,
        description="Seconds of idle before considering work complete.",
    )
    completion_check_delay: float = Field(
        default=5.0,
        description="Seconds to wait after idle before evaluating completion (debounce).",
    )
    require_pr: bool = Field(
        default=False,
        description="If true, PR must exist for completion.",
    )
    require_ci: bool = Field(
        default=False,
        description="If true, CI must pass for completion.",
    )
    confidence_base: float = Field(
        default=0.5,
        description="Base confidence score when completion criteria are met.",
    )
    confidence_pr_bonus: float = Field(
        default=0.2,
        description="Confidence bonus when a PR exists.",
    )
    confidence_ci_bonus: float = Field(
        default=0.2,
        description="Confidence bonus when CI has passed.",
    )
    confidence_idle_bonus: float = Field(
        default=0.1,
        description="Confidence bonus for extended idle beyond threshold.",
    )
    reconnect_delay: float = Field(
        default=5.0,
        description="Seconds to wait before reconnecting after SSE subscription failure.",
    )


class EventBusConfig(BaseModel):
    """Event bus adapter configuration (dynamic adapter pattern)."""

    adapter: str = Field(
        default="tyr.adapters.memory_event_bus.InMemoryEventBus",
        description="Fully-qualified class path for the EventBus adapter.",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)


class SleipnirConfig(BaseModel):
    """Sleipnir platform event bus integration (optional).

    When ``enabled`` is True, Tyr creates a Sleipnir adapter and starts a
    :class:`~tyr.adapters.sleipnir_event_bridge.TyrSleipnirBridge` that
    republishes all Tyr events to the platform-wide bus.

    Example YAML::

        sleipnir:
          enabled: true
          adapter: "sleipnir.adapters.nats_transport.NatsTransport"
          kwargs:
            servers: ["nats://nats:4222"]
    """

    enabled: bool = Field(
        default=False,
        description="Enable Sleipnir platform event bus integration.",
    )
    adapter: str = Field(
        default="sleipnir.adapters.in_process.InProcessBus",
        description="Fully-qualified class path for the Sleipnir adapter.",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)


class EventTriggerRule(BaseModel):
    """A single event-trigger rule mapping a Sleipnir event to a saga template."""

    event: str = Field(description="Sleipnir event type pattern (fnmatch syntax).")
    saga_template: str = Field(description="Name of the saga template to instantiate.")
    auto_start: bool = Field(
        default=True,
        description="When True, saga raids are dispatched immediately. "
        "When False, raids are created in PENDING state and tyr.raid.needs_approval is emitted.",
    )
    filter: dict[str, str] = Field(
        default_factory=dict,
        description="Payload key/value pairs that must all match for the rule to fire.",
    )


class EventTriggerConfig(BaseModel):
    """Configuration for the Sleipnir event trigger adapter.

    Example YAML::

        event_triggers:
          owner_id: dev-user
          templates_dir: ""   # empty = bundled src/tyr/templates/
          rules:
            - event: "github.pr.opened"
              saga_template: review
              auto_start: true
            - event: "github.pr.merged"
              saga_template: deploy
              auto_start: true
              filter:
                branch: main
    """

    enabled: bool = Field(default=False, description="Enable the event trigger adapter.")
    owner_id: str = Field(
        default="dev-user",
        description="Owner ID used when creating event-triggered sagas.",
    )
    templates_dir: str = Field(
        default="",
        description="Path to saga template YAML files. Empty means the bundled templates.",
    )
    default_model: str = Field(
        default="claude-sonnet-4-6",
        description="Default AI model for event-triggered saga sessions.",
    )
    dedup_cache_size: int = Field(
        default=10_000,
        description="Maximum number of correlation IDs held in the deduplication cache.",
    )
    rules: list[EventTriggerRule] = Field(
        default_factory=list,
        description="List of event-trigger rules.",
    )


class RavnOutcomeConfig(BaseModel):
    """Configuration for the RavnOutcomeHandler adapter.

    Example YAML::

        ravn_outcome:
          enabled: true
          scope_adherence_threshold: 0.7
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Enable the Ravn completion subscriber for canonical "
            "ravn.session.ended events plus compatibility ravn.task.completed events."
        ),
    )
    owner_id: str = Field(
        default="api",
        description="Owner ID used when looking up raids from ravn outcome events.",
    )
    scope_adherence_threshold: float = Field(
        default=0.7,
        description=(
            "scope_adherence values below this threshold flag a scope breach. Range 0.0–1.0."
        ),
    )


class FlockFlowsConfig(BaseModel):
    """Flock flow provider configuration (dynamic adapter pattern).

    Example YAML::

        flock_flows:
          adapter: "tyr.adapters.flows.config.ConfigFlockFlowProvider"
          kwargs:
            path: /etc/tyr/flock_flows.yaml
    """

    adapter: str = Field(
        default="tyr.adapters.flows.config.ConfigFlockFlowProvider",
        description="Fully-qualified class path for the flock flow provider.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=lambda: {"path": str(BUNDLED_FLOCK_FLOWS_PATH)},
    )


class NotificationConfig(BaseModel):
    """Notification service configuration."""

    enabled: bool = Field(default=True)
    confidence_threshold: float = Field(
        default=0.3,
        description="Notify when raid confidence drops below this value.",
    )


class EventsConfig(BaseModel):
    """SSE event stream configuration."""

    max_sse_clients: int = Field(default=10)
    keepalive_interval: float = Field(default=15.0)
    activity_log_size: int = Field(
        default=100,
        description="Number of events retained in the dispatcher activity ring buffer.",
    )


class Settings(BaseSettings):
    """Application settings.

    Loads configuration from YAML file with environment variable overrides.

    YAML file locations (first found wins):
    - ./tyr.yaml
    - /etc/tyr/config.yaml

    Environment variable overrides use double underscore for nesting:
    - DATABASE__HOST=myhost -> settings.database.host
    """

    model_config = SettingsConfigDict(
        yaml_file=CONFIG_PATHS,
        yaml_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    cors: CorsConfig = Field(default_factory=CorsConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    volundr: VolundrConfig = Field(default_factory=VolundrConfig)
    ai_models: list[AIModelConfig] = Field(
        default_factory=lambda: [
            AIModelConfig(id="claude-opus-4-7", name="Opus 4.7", provider="anthropic"),
            AIModelConfig(id="claude-opus-4-6", name="Opus 4.6", provider="anthropic"),
            AIModelConfig(
                id="claude-opus-4-5-20251101", name="Opus 4.5", provider="anthropic",
            ),
            AIModelConfig(id="claude-sonnet-4-6", name="Sonnet 4.6", provider="anthropic"),
            AIModelConfig(
                id="claude-haiku-4-5-20251001", name="Haiku 4.5", provider="anthropic",
            ),
            AIModelConfig(id="gpt-5.5", name="GPT-5.5", provider="openai"),
            AIModelConfig(id="gpt-5.4", name="GPT-5.4", provider="openai"),
        ]
    )
    git: GitConfig = Field(default_factory=GitConfig)
    linear: LinearConfig = Field(default_factory=LinearConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)
    dispatch: DispatchConfig = Field(default_factory=DispatchConfig)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    credential_store: CredentialStoreConfig = Field(default_factory=CredentialStoreConfig)
    pat: PATConfig = Field(default_factory=PATConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    cerbos: CerbosConfig = Field(default_factory=CerbosConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    watcher: WatcherConfig = Field(default_factory=WatcherConfig)
    event_bus: EventBusConfig = Field(default_factory=EventBusConfig)
    events: EventsConfig = Field(default_factory=EventsConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    sleipnir: SleipnirConfig = Field(default_factory=SleipnirConfig)
    event_triggers: EventTriggerConfig = Field(default_factory=EventTriggerConfig)
    ravn_outcome: RavnOutcomeConfig = Field(default_factory=RavnOutcomeConfig)
    flock_flows: FlockFlowsConfig = Field(default_factory=FlockFlowsConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customize settings sources.

        Order (first wins):
        1. init_settings - explicit constructor arguments
        2. env_settings - environment variables
        3. yaml - YAML config file
        4. file_secret_settings - /run/secrets files
        """
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
