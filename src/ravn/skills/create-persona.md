# skill: create-persona

Guide the user through creating a new Ravn persona interactively — like cookiecutter but LLM-driven.

**Template Reference:** See `docs/ravn/persona-template.md` for the full persona structure,
voice guidelines, escalation protocol, and examples.

## PersonaConfig schema reference

### Top-level fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | **required** | Kebab-case identifier (e.g. `draft-a-note`, `code-reviewer`) |
| `system_prompt_template` | string | `""` | The agent's identity and behaviour instructions |
| `allowed_tools` | list[string] | `[]` | Tool groups or names the agent may use |
| `forbidden_tools` | list[string] | `[]` | Tool groups or names explicitly denied |
| `permission_mode` | string | `""` | File-system permission level |
| `iteration_budget` | int | `0` (use settings default) | Maximum agent turns before stopping |

### Tool groups

| Group | Includes |
|-------|---------|
| `file` | read_file, write_file, edit_file, glob_search, grep_search |
| `git` | git_status, git_diff, git_add, git_commit, git_checkout, git_log, git_pr |
| `web` | web_fetch, web_search |
| `terminal` | terminal (bash, shell) |
| `mimir` | mimir_read, mimir_write, mimir_search, mimir_list, mimir_ingest |
| `cascade` | cascade_delegate, cascade_broadcast |
| `ravn` | persona_validate, persona_save, skill_list, skill_run |
| `volundr` | volundr_session, volundr_git |

### Permission modes

| Value | Meaning |
|-------|---------|
| `read-only` | Can read files, cannot write or execute |
| `workspace-write` | Can read and write within the workspace |
| `full-access` | Unrestricted — use with care |

### LLM config (`llm:` section)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `primary_alias` | string | `""` | Model tier alias |
| `thinking_enabled` | bool | `false` | Enable extended thinking |
| `max_tokens` | int | `0` | Override max output tokens (0 = settings default) |

**LLM aliases:**

| Alias | Meaning |
|-------|---------|
| `balanced` | Mid-tier model — good for most tasks |
| `powerful` | Large model — for complex reasoning |
| `fast` | Small/nano model — for quick tasks |

### Pipeline fields

These are optional — only used when the persona participates in a multi-agent pipeline.

#### `produces:` — structured output this persona emits

```yaml
produces:
  event_type: review.completed   # dot-separated event name
  schema:
    verdict:
      type: enum
      values: [pass, fail, needs_changes]
      description: review verdict
    summary:
      type: string
      description: one-line review summary
```

**OutcomeField types:** `string`, `number`, `boolean`, `enum`

For `enum`, include a `values` list. All fields default to `required: true`.

#### `consumes:` — what input this persona expects

```yaml
consumes:
  event_types: [code.changed, review.requested]
  injects: [repo, branch, diff_url]
```

#### `fan_in:` — how output combines with parallel peers

```yaml
fan_in:
  strategy: all_must_pass   # how to combine verdicts
  contributes_to: review.verdict
```

**Fan-in strategies:**

| Strategy | Meaning |
|----------|---------|
| `all_must_pass` | All parallel agents must succeed |
| `any_pass` | At least one agent must succeed |
| `majority` | More than half must succeed |
| `merge` | Combine all outputs (default) |

---

## Examples

### Simple persona (no pipeline)

```yaml
name: draft-a-note
system_prompt_template: |
  You are a note-taking agent for the Mímir knowledge base.
  Given a topic, draft a concise, well-structured page.
  Write in clear prose. Keep pages under 500 words.
  Always add front matter with title, tags, and date.
allowed_tools: [mimir, web]
forbidden_tools: [terminal, git]
permission_mode: read-only
llm:
  primary_alias: balanced
  thinking_enabled: false
iteration_budget: 10
```

### Pipeline persona (produces/consumes/fan_in)

```yaml
name: reviewer
system_prompt_template: |
  You are a code reviewer. Read diffs, identify issues, and produce
  a structured verdict with detailed findings.
  Apply 'pass' when ready to merge, 'needs_changes' for non-blocking
  issues, 'fail' for blocking problems.
allowed_tools: [file, git, web, ravn]
forbidden_tools: [terminal, cascade]
permission_mode: read-only
llm:
  primary_alias: balanced
  thinking_enabled: true
iteration_budget: 20
produces:
  event_type: review.completed
  schema:
    verdict:
      type: enum
      values: [pass, fail, needs_changes]
      description: review verdict
    findings_count:
      type: number
      description: total number of findings
    summary:
      type: string
      description: one-line review summary
consumes:
  event_types: [code.changed, review.requested]
  injects: [repo, branch, diff_url]
fan_in:
  strategy: all_must_pass
  contributes_to: review.verdict
```

---

## Conversational creation flow

Follow these steps — be conversational, use sensible defaults, and don't dump all fields at once.

1. **Ask what the persona should do.** One open question: "What should this persona do? Describe its role and main purpose."

2. **Suggest a kebab-case name.** Based on the description, propose a name like `code-reviewer` or `data-analyst`. Confirm with the user.

3. **Draft `system_prompt_template`.** Write a concise system prompt that captures the role. Show it to the user for approval. Offer to refine.

4. **Determine `allowed_tools` and `forbidden_tools`.** Ask which tool groups the persona needs. Suggest sensible defaults based on the role (e.g. a read-only research agent probably needs `[web, mimir]` but not `terminal`).

5. **Set `permission_mode`.** Suggest `read-only` for agents that only read, `workspace-write` for agents that create or edit files, `full-access` for autonomous agents.

6. **Configure `llm`.** Ask if the default `balanced` model is suitable, or whether the persona needs `powerful` (complex reasoning) or `fast` (quick tasks). Ask about `thinking_enabled` only if it seems relevant.

7. **Set `iteration_budget`.** Suggest a default based on complexity (5–10 for simple, 20–40 for complex). Ask if the user wants to change it.

8. **Ask if this is a pipeline persona.** "Will this persona run as part of a multi-agent pipeline (producing structured output for other agents)?" If no, skip to step 9. If yes, help configure `produces`, `consumes`, and `fan_in`.

9. **Call `persona_validate`** with the assembled YAML. If validation fails, show the errors and fix them with the user.

10. **Fix any validation errors** by going back to the relevant step and revising.

11. **Call `persona_save`** to write the file. Confirm the save path with the user first. Use the optional `directory` parameter to save to a project-local `.ravn/personas/` if the user wants that.

12. **Confirm with the user.** Show the final file path and a summary of the persona's key settings.

**Style notes:**
- Be conversational and guide the user one step at a time
- Offer sensible defaults so the user does not have to know every field
- Validate early and often — call `persona_validate` before saving
- Explain what each field does when it might be unclear
- For most users, skip pipeline fields entirely unless they ask
