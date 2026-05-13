# Self-Improvement / Evolution

Ravn's evolution system analyzes accumulated task outcomes and episodic memory
to surface patterns that improve future performance. It extracts skill
suggestions, error warnings, and effective strategies.

## How It Works

Run the evolution pass manually:

```bash
ravn evolve
```

The `PatternExtractor` analyzes:

1. **Recent episodes** — up to `max_episodes_to_analyze` (default: 100)
2. **Task outcomes** — up to `max_outcomes_to_analyze` (default: 50)

And produces three categories of insights:

### Skill Suggestions

When ≥ `skill_suggestion_min_occurrences` (default: 3) SUCCESS episodes share
the same tool sequence pattern, the system suggests extracting a reusable skill.

Example output:

```
SKILL SUGGESTION: "test-fix-commit"
  Pattern: [grep_search, edit_file, bash("pytest"), git_add, git_commit]
  Seen in 5 SUCCESS episodes
  → Consider creating a skill for this workflow
```

### System Warnings

When ≥ `error_warning_min_occurrences` (default: 3) episodes contain the same
error pattern, the system flags it as a systematic issue.

Example output:

```
WARNING: "timeout on web_fetch"
  Seen in 4 episodes (3 FAILURE, 1 PARTIAL)
  → Consider increasing web.fetch.timeout or adding retry logic
```

### Strategy Injections

When ≥ `strategy_min_occurrences` (default: 3) SUCCESS episodes share the
same tag (domain/context), the system identifies effective strategies for
that domain.

Example output:

```
STRATEGY: "database migrations"
  5/6 SUCCESS episodes used: [read_file(migration), bash("make test"), git_commit]
  → When working on migrations, read existing migrations first
```

## Output Format

`ravn evolve` prints a human-readable `PromptEvolution` diff. No automatic
modifications are made — review and apply manually.

The output includes:
- Suggested skill definitions (Markdown with YAML frontmatter)
- Warning summaries with affected episodes
- Strategy descriptions with recommended approaches

## Trigger Threshold

Evolution analysis only runs when there are at least `min_new_outcomes`
(default: 10) new outcomes since the last analysis. The state is tracked
in `evolution_state.json`.

## Configuration

```yaml
evolution:
  enabled: true
  min_new_outcomes: 10
  state_path: "~/.ravn/evolution_state.json"
  max_episodes_to_analyze: 100
  max_outcomes_to_analyze: 50
  skill_suggestion_min_occurrences: 3
  error_warning_min_occurrences: 3
  strategy_min_occurrences: 3
  max_skill_suggestions: 5
  max_system_warnings: 5
  max_strategy_injections: 3
```

Related: [NIU-501](https://linear.app/niuulabs/issue/NIU-501)
