# Skill System

Skills are reusable procedures that Ravn can discover, learn, and execute.
They bridge the gap between one-off agent actions and repeatable workflows.

## How Skills Work

Skills are Markdown files with YAML frontmatter containing:

- **Name and description** for discovery
- **Tool requirements** (which tools the skill needs)
- **Instructions** in natural language (the skill body)

When the agent runs `skill_run`, the skill's instruction content is loaded
and injected into the agent's context as a structured prompt.

## Built-in Skills

Ravn ships with four built-in skills:

| Skill | Description |
|-------|-------------|
| `code-review` | Review code changes for quality, correctness, and style. |
| `fix-tests` | Diagnose and fix failing tests. |
| `refactor` | Refactor code for clarity, maintainability, or performance. |
| `write-docs` | Write documentation for code or features. |

Built-in skills are located in `src/ravn/skills/` and included by default
(controllable via `skill.include_builtin`).

## Automatic Skill Extraction

Ravn's evolution system monitors task outcomes and automatically suggests
skills when it detects recurring patterns:

1. Track tool sequences across SUCCESS episodes
2. When ≥ `suggestion_threshold` (default: 3) episodes share the same
   tool pattern, suggest a skill
3. Run `ravn evolve` to see suggestions

This is suggestion-only — skills are not auto-created.

## Skill Discovery

Skills are loaded from three sources (in order):

1. **Project-local**: `.ravn/skills/` in the current project
2. **User-level**: `~/.ravn/skills/`
3. **Built-in**: `src/ravn/skills/` (if `include_builtin` is true)
4. **Extra directories**: paths in `skill.skill_dirs`

## Skill Storage Backends

| Backend | Description |
|---------|-------------|
| `file` (default) | Markdown files on disk. Simple, human-editable. |
| `sqlite` | SQLite database. Better for large skill collections. |

## Creating Custom Skills

Create a Markdown file in `~/.ravn/skills/` or `.ravn/skills/`:

```markdown
---
name: deploy-check
description: Verify a deployment succeeded and run smoke tests
tools:
  - bash
  - web_fetch
  - git_log
tags:
  - deployment
  - verification
---

## Deploy Verification Procedure

1. Check the deployment status:
   - Run `kubectl get pods -n production` to verify pod health
   - Check that all replicas are ready

2. Run smoke tests:
   - Fetch the health endpoint and verify 200 response
   - Check recent git log to confirm expected commit is deployed

3. Report results:
   - Summarize pod status, health check results, and deployed version
   - Flag any issues found
```

### Frontmatter Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | str | Yes | Unique skill identifier. |
| `description` | str | Yes | What the skill does (shown in `skill_list`). |
| `tools` | list[str] | No | Tools the skill requires. |
| `tags` | list[str] | No | Tags for categorization and search. |

## Agent Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `skill_list` | `skill:read` | List all available skills with names and descriptions. |
| `skill_run` | `skill:read` | Execute a skill by name. Loads instructions into context. |

## Configuration

```yaml
skill:
  enabled: true
  backend: file
  path: "~/.ravn/skills.db"
  suggestion_threshold: 3
  cache_max_entries: 128
  skill_dirs: []
  include_builtin: true
```

Related: [NIU-436](https://linear.app/niuulabs/issue/NIU-436)
