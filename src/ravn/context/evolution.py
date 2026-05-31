"""Pattern extraction and prompt evolution for Ravn self-improvement.

Analyses episodic memory to surface three kinds of signal that could improve
future performance:

* **Recurring tool sequences** (SUCCESS episodes) → suggest as new skills
* **Systematic errors** (FAILURE/PARTIAL episodes) → add as system-prompt warnings
* **Effective strategies** (SUCCESS episodes grouped by domain tag) → inject in
  relevant persona prompts

The extractor never modifies prompts automatically.  It produces a
``PromptEvolution`` value whose ``as_diff()`` method renders a human-readable
proposal.  The user decides which changes to accept.

Usage::

    from ravn.context.evolution import PatternExtractor, load_state, save_state, should_run
    from ravn.config import EvolutionConfig

    config = EvolutionConfig()
    state = load_state(Path(config.state_path).expanduser())
    current_count = await memory.count_episodes()

    if should_run(state, current_count, min_new=config.min_new_outcomes):
        extractor = PatternExtractor(memory, config=config)
        evolution = await extractor.extract()
        if not evolution.is_empty():
            print(evolution.as_diff())
        state.outcome_count_at_last_run = current_count
        state.last_run_at = datetime.now(UTC)
        save_state(Path(config.state_path).expanduser(), state)
"""

from __future__ import annotations
from contextlib import suppress

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ravn.domain.models import Episode, Outcome
from ravn.ports.memory import MemoryPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Broad queries used to pull a diverse sample of episodes.
_BROAD_EPISODE_QUERIES: tuple[str, ...] = (
    "task completed",
    "code git",
    "error failed",
    "test deploy",
    "research analysis",
)

# Error-related keywords used to cluster failure patterns.
_ERROR_KEYWORDS: frozenset[str] = frozenset(
    {
        "error",
        "failed",
        "exception",
        "traceback",
        "permission",
        "denied",
        "timeout",
        "invalid",
        "missing",
    }
)


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillSuggestion:
    """A proposed new skill based on recurring successful tool combinations.

    ``tool_pattern`` is a sorted, deduplicated tuple of tool names observed
    together in at least ``occurrence_count`` SUCCESS episodes.
    """

    tool_pattern: tuple[str, ...]
    description: str
    source_episode_ids: list[str]
    occurrence_count: int


@dataclass(frozen=True)
class SystemWarning:
    """A proposed warning to add to the system prompt based on recurring failures.

    ``warning_text`` is a short, actionable message derived from the most
    commonly observed error keyword in FAILURE/PARTIAL outcomes.
    """

    warning_text: str
    source_outcome_ids: list[str]
    occurrence_count: int


@dataclass(frozen=True)
class StrategyInjection:
    """A proposed strategy hint for a specific task type.

    Derived from SUCCESS episodes that share a common domain tag (e.g. ``git``,
    ``testing``, ``deployment``).  ``strategy_text`` summarises what approaches
    appeared to work well across those episodes.
    """

    task_type: str
    strategy_text: str
    source_episode_ids: list[str]
    success_count: int


@dataclass
class PromptEvolution:
    """The full set of proposed prompt changes from one extraction pass.

    Produced by ``PatternExtractor.extract()``.  Render with ``as_diff()``
    to get a human-readable proposal; check ``is_empty()`` before presenting
    to avoid showing a report with no actionable items.
    """

    extracted_at: datetime
    episodes_analyzed: int
    outcomes_analyzed: int
    suggested_skills: list[SkillSuggestion] = field(default_factory=list)
    system_warnings: list[SystemWarning] = field(default_factory=list)
    strategy_injections: list[StrategyInjection] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Return True when no changes are proposed."""
        return not (self.suggested_skills or self.system_warnings or self.strategy_injections)

    def as_diff(self) -> str:
        """Return a human-readable diff of proposed prompt changes.

        The format mimics a unified diff to make it easy to review:
        lines prefixed with ``+`` represent additions to the existing prompt
        configuration.  No lines are removed by this proposal.
        """
        ts = self.extracted_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        header = (
            f"## Ravn Prompt Evolution Proposal\n"
            f"Extracted: {ts}\n"
            f"Analysed: {self.episodes_analyzed} episodes, "
            f"{self.outcomes_analyzed} outcomes\n"
        )

        sections: list[str] = [header]

        if self.suggested_skills:
            sections.append(f"\n### Suggested New Skills ({len(self.suggested_skills)})\n")
            for skill in self.suggested_skills:
                tools_str = " + ".join(skill.tool_pattern)
                sections.append(
                    f"\n+ **skill: {tools_str}** ({skill.occurrence_count} successful uses)\n"
                    f"  {skill.description}\n"
                )

        if self.system_warnings:
            sections.append(
                f"\n### Proposed System Prompt Warnings ({len(self.system_warnings)})\n"
            )
            for warning in self.system_warnings:
                sections.append(
                    f"\n+ **Warning** ({warning.occurrence_count} occurrences):\n"
                    f"  {warning.warning_text}\n"
                )

        if self.strategy_injections:
            sections.append(
                f"\n### Proposed Strategy Injections ({len(self.strategy_injections)})\n"
            )
            for strategy in self.strategy_injections:
                sections.append(
                    f"\n+ **For '{strategy.task_type}' tasks**"
                    f" ({strategy.success_count} successful episodes):\n"
                    f"  {strategy.strategy_text}\n"
                )

        sections.append(
            "\n---\n"
            "To apply: review and update your RAVN.md or system prompt configuration.\n"
            "Ravn will not apply these changes automatically.\n"
        )

        return "".join(sections)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


@dataclass
class EvolutionState:
    """Persisted state for the self-improvement loop.

    Tracks when the last extraction ran and how many outcomes had been
    recorded at that point, so we can decide whether enough new signal has
    accumulated to warrant another pass.
    """

    last_run_at: datetime | None = None
    outcome_count_at_last_run: int = 0

    def to_dict(self) -> dict:
        return {
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "outcome_count_at_last_run": self.outcome_count_at_last_run,
        }

    @classmethod
    def from_dict(cls, data: dict) -> EvolutionState:
        last_run_at: datetime | None = None
        raw_ts = data.get("last_run_at")
        if raw_ts:
            with suppress(ValueError, TypeError):
                last_run_at = datetime.fromisoformat(raw_ts)
        return cls(
            last_run_at=last_run_at,
            outcome_count_at_last_run=int(data.get("outcome_count_at_last_run", 0)),
        )


def load_state(path: Path) -> EvolutionState:
    """Load persisted evolution state from *path*, or return a blank state."""
    try:
        text = path.read_text(encoding="utf-8")
        return EvolutionState.from_dict(json.loads(text))
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return EvolutionState()


def save_state(path: Path, state: EvolutionState) -> None:
    """Persist *state* to *path*, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def should_run(state: EvolutionState, current_count: int, *, min_new: int) -> bool:
    """Return True when enough new outcomes have accumulated since the last run.

    Args:
        state: The persisted evolution state.
        current_count: The total number of outcomes currently stored.
        min_new: Minimum number of *new* outcomes required to trigger extraction.
    """
    if min_new <= 0:
        return False
    new_since_last = current_count - state.outcome_count_at_last_run
    return new_since_last >= min_new


# ---------------------------------------------------------------------------
# Pattern extraction helpers
# ---------------------------------------------------------------------------


def _describe_skill(pattern: tuple[str, ...], episodes: list[Episode]) -> str:
    """Generate a short description for a skill pattern from episode summaries."""
    tools_str = " + ".join(pattern)
    example_tasks = [ep.task_description for ep in episodes[:3] if ep.task_description]
    if example_tasks:
        examples = "; ".join(f'"{t[:60]}"' for t in example_tasks)
        return f"Recurring workflow using {tools_str}. Example tasks: {examples}."
    return f"Recurring workflow using {tools_str} across multiple successful tasks."


def _describe_warning(keyword: str, episodes: list[Episode]) -> str:
    """Generate a short warning message from recurring error patterns."""
    example_errors = []
    for episode in episodes[:3]:
        for err in episode.errors[:1]:
            if keyword in err.lower():
                example_errors.append(err[:80])
    if example_errors:
        example = example_errors[0]
        return (
            f"Recurring '{keyword}' errors detected across {len(episodes)} failed tasks. "
            f"Example: {example!r}. Check for this pattern before proceeding."
        )
    return (
        f"Recurring '{keyword}' errors detected across {len(episodes)} failed tasks. "
        f"Verify preconditions when this error type may occur."
    )


def _describe_strategy(tag: str, episodes: list[Episode]) -> str:
    """Generate a strategy hint for a task type from successful episode summaries."""
    summaries = [ep.summary for ep in episodes[:3] if ep.summary]
    if summaries:
        example = summaries[0][:100]
        return (
            f"Effective approach for '{tag}' tasks observed across {len(episodes)} "
            f"successful episodes. Example: {example!r}. Apply this pattern consistently."
        )
    return (
        f"Multiple successful '{tag}' task completions recorded "
        f"({len(episodes)} episodes). Continue applying the observed approach."
    )


# ---------------------------------------------------------------------------
# Pattern extractor
# ---------------------------------------------------------------------------


class PatternExtractor:
    """Analyse episodic memory to surface improvement signals.

    Args:
        memory: Episodic memory backend (``MemoryPort``).
        max_episodes_to_analyze: Maximum episodes loaded per pass.
        skill_suggestion_min_occurrences: Minimum times a tool pattern must appear
            before a skill suggestion is produced.
        error_warning_min_occurrences: Minimum times an error keyword must appear
            before a system-prompt warning is proposed.
        strategy_min_occurrences: Minimum times a domain tag must appear in SUCCESS
            episodes before a strategy injection is proposed.
        max_skill_suggestions: Maximum skill suggestions per proposal.
        max_system_warnings: Maximum system-prompt warnings per proposal.
        max_strategy_injections: Maximum strategy injections per proposal.
    """

    def __init__(
        self,
        memory: MemoryPort,
        *,
        max_episodes_to_analyze: int = 100,
        skill_suggestion_min_occurrences: int = 3,
        error_warning_min_occurrences: int = 3,
        strategy_min_occurrences: int = 3,
        max_skill_suggestions: int = 5,
        max_system_warnings: int = 5,
        max_strategy_injections: int = 3,
    ) -> None:
        self._memory = memory
        self._max_episodes = max_episodes_to_analyze
        self._skill_min = skill_suggestion_min_occurrences
        self._error_min = error_warning_min_occurrences
        self._strategy_min = strategy_min_occurrences
        self._max_skills = max_skill_suggestions
        self._max_warnings = max_system_warnings
        self._max_strategies = max_strategy_injections

    async def extract(self) -> PromptEvolution:
        """Run the full pattern extraction pass and return proposed changes."""
        episodes = await self._load_episodes()
        failure_eps = [e for e in episodes if e.outcome in (Outcome.FAILURE, Outcome.PARTIAL)]

        skills = self._extract_skill_patterns(episodes)
        warnings = self._extract_error_patterns(failure_eps)
        strategies = self._extract_strategy_patterns(episodes)

        return PromptEvolution(
            extracted_at=datetime.now(UTC),
            episodes_analyzed=len(episodes),
            outcomes_analyzed=len(failure_eps),
            suggested_skills=skills,
            system_warnings=warnings,
            strategy_injections=strategies,
        )

    async def _load_episodes(self) -> list[Episode]:
        """Load a diverse sample of recent episodes via broad queries."""
        seen: set[str] = set()
        episodes: list[Episode] = []

        per_query = max(1, self._max_episodes // len(_BROAD_EPISODE_QUERIES)) + 5

        for query in _BROAD_EPISODE_QUERIES:
            try:
                matches = await self._memory.query_episodes(query, limit=per_query)
            except Exception:
                logger.warning("evolution: query_episodes failed for query %r", query)
                continue

            for match in matches:
                ep_id = match.episode.episode_id
                if ep_id in seen:
                    continue
                seen.add(ep_id)
                episodes.append(match.episode)
                if len(episodes) >= self._max_episodes:
                    return episodes

        return episodes

    def _extract_skill_patterns(self, episodes: list[Episode]) -> list[SkillSuggestion]:
        """Find recurring tool combinations in successful episodes."""
        success_eps = [e for e in episodes if e.outcome == Outcome.SUCCESS and e.tools_used]

        pattern_groups: dict[tuple[str, ...], list[Episode]] = defaultdict(list)
        for ep in success_eps:
            pattern = tuple(sorted(set(ep.tools_used)))
            if not pattern:
                continue
            pattern_groups[pattern].append(ep)

        suggestions: list[SkillSuggestion] = []
        for pattern, eps in sorted(pattern_groups.items(), key=lambda x: -len(x[1])):
            if len(eps) < self._skill_min:
                continue
            suggestions.append(
                SkillSuggestion(
                    tool_pattern=pattern,
                    description=_describe_skill(pattern, eps),
                    source_episode_ids=[e.episode_id for e in eps[:5]],
                    occurrence_count=len(eps),
                )
            )
            if len(suggestions) >= self._max_skills:
                break

        return suggestions

    def _extract_error_patterns(self, episodes: list[Episode]) -> list[SystemWarning]:
        """Find recurring error keywords in failed/partial episodes."""
        keyword_groups: dict[str, list[Episode]] = defaultdict(list)
        for episode in episodes:
            combined = " ".join(episode.errors).lower()
            if episode.reflection:
                combined += " " + episode.reflection.lower()
            for kw in _ERROR_KEYWORDS:
                if kw in combined:
                    keyword_groups[kw].append(episode)

        warnings: list[SystemWarning] = []
        for kw, eps in sorted(keyword_groups.items(), key=lambda x: -len(x[1])):
            if len(eps) < self._error_min:
                continue
            warnings.append(
                SystemWarning(
                    warning_text=_describe_warning(kw, eps),
                    source_outcome_ids=[e.episode_id for e in eps[:5]],
                    occurrence_count=len(eps),
                )
            )
            if len(warnings) >= self._max_warnings:
                break

        return warnings

    def _extract_strategy_patterns(self, episodes: list[Episode]) -> list[StrategyInjection]:
        """Find effective strategies per domain tag in successful episodes."""
        success_eps = [e for e in episodes if e.outcome == Outcome.SUCCESS and e.tags]

        tag_groups: dict[str, list[Episode]] = defaultdict(list)
        for ep in success_eps:
            for tag in ep.tags:
                tag_groups[tag].append(ep)

        injections: list[StrategyInjection] = []
        for tag, eps in sorted(tag_groups.items(), key=lambda x: -len(x[1])):
            if len(eps) < self._strategy_min:
                continue
            injections.append(
                StrategyInjection(
                    task_type=tag,
                    strategy_text=_describe_strategy(tag, eps),
                    source_episode_ids=[e.episode_id for e in eps[:5]],
                    success_count=len(eps),
                )
            )
            if len(injections) >= self._max_strategies:
                break

        return injections
