"""Persona configuration loader for Ravn.

Personas define the agent's identity, tool access, permission level, and LLM
settings for a given deployment context. They are YAML files stored at
``~/.ravn/personas/<name>.yaml`` or selected from the built-in set.

Activation priority (highest to lowest):
  1. CLI ``--persona`` flag
  2. ``persona:`` field in RAVN.md project manifest
  3. No persona — agent uses Settings defaults directly

When a persona is active, RAVN.md fields override specific persona values so
project-level constraints always take precedence over the persona defaults.

Persona YAML format::

    name: coding-agent
    system_prompt_template: |
      You are a focused coding agent. ...
    allowed_tools: [file, git, terminal, web, todo, introspection]
    forbidden_tools: [cascade, volundr]
    permission_mode: workspace-write
    llm:
      thinking_enabled: true
    iteration_budget: 40
    produces:
      event_type: review.completed
      schema:
        verdict:
          type: enum
          values: [pass, fail, needs_changes]
        summary:
          type: string
    consumes:
      event_types: [code.changed, review.requested]
      injects: [repo, branch, diff_url]
    fan_in:
      strategy: all_must_pass
      contributes_to: review.verdict
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml as _yaml

from niuu.domain.outcome import OutcomeField, OutcomeSchema, generate_outcome_instruction
from ravn.config import ProjectConfig, _safe_int
from ravn.domain.valkyrie_contracts import (
    VALKYRIE_RUNTIME_OWNED_FIELDS,
    is_valkyrie_outcome_event,
)
from ravn.ports.persona import PersonaRegistryPort

# Active bundled personas shipped with the ravn package (src/ravn/personas/*.yaml)
_BUILTIN_PERSONAS_DIR = Path(__file__).parent.parent.parent / "personas"
_ARCHIVED_BUILTIN_PERSONAS_DIR = _BUILTIN_PERSONAS_DIR / "_archived"

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class PersonaLLMConfig:
    """Non-model LLM behavior settings embedded in a persona."""

    primary_alias: str = ""
    thinking_enabled: bool = False
    max_tokens: int = 0  # 0 = use settings default


@dataclass
class PersonaExecutorConfig:
    """Executor adapter settings embedded in a persona."""

    adapter: str = ""
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class PersonaProduces:
    """What this persona outputs when it completes.

    event_type: Default event type to publish (used if event_type_map doesn't match)
    event_type_map: Maps outcome field values to event types, e.g.:
        {"pass": "review.passed", "needs_changes": "review.changes_requested"}
        The map is checked against the 'verdict' field in the outcome.
    schema: Expected fields in the outcome block
    """

    event_type: str = ""
    event_type_map: dict[str, str] = field(default_factory=dict)
    schema: dict[str, OutcomeField] = field(default_factory=dict)


@dataclass
class PersonaConsumes:
    """What input this persona expects from previous stages."""

    event_types: list[str] = field(default_factory=list)
    injects: list[str] = field(default_factory=list)
    schema: dict[str, OutcomeField] = field(default_factory=dict)


@dataclass
class PersonaFanIn:
    """How this persona's output combines with parallel peers."""

    strategy: Literal["all_must_pass", "any_pass", "majority", "merge"] = "merge"
    contributes_to: str = ""


@dataclass
class PersonaConfig:
    """A fully-resolved persona configuration.

    Fields left at their zero-value (empty string, empty list, 0) are
    considered "unset" and will not override Settings defaults when the persona
    is applied.
    """

    name: str
    system_prompt_template: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    permission_mode: str = ""
    executor: PersonaExecutorConfig = field(default_factory=PersonaExecutorConfig)
    llm: PersonaLLMConfig = field(default_factory=PersonaLLMConfig)
    iteration_budget: int = 0
    produces: PersonaProduces = field(default_factory=PersonaProduces)
    consumes: PersonaConsumes = field(default_factory=PersonaConsumes)
    fan_in: PersonaFanIn = field(default_factory=PersonaFanIn)
    # Legacy compatibility field. Outcomes never suppress requested tool calls.
    stop_on_outcome: bool = False

    def to_dict(self) -> dict:
        """Serialize to a dict compatible with :meth:`FilesystemPersonaAdapter.parse`.

        Zero-value fields (empty string, empty list, ``0``, ``False``) are
        omitted to keep the resulting YAML clean.  Nested dataclasses are
        serialized recursively.
        """
        d: dict = {"name": self.name}

        if self.system_prompt_template:
            d["system_prompt_template"] = self.system_prompt_template
        if self.allowed_tools:
            d["allowed_tools"] = list(self.allowed_tools)
        if self.forbidden_tools:
            d["forbidden_tools"] = list(self.forbidden_tools)
        if self.permission_mode:
            d["permission_mode"] = self.permission_mode
        if self.executor.adapter or self.executor.kwargs:
            executor_dict: dict[str, Any] = {}
            if self.executor.adapter:
                executor_dict["adapter"] = self.executor.adapter
            if self.executor.kwargs:
                executor_dict["kwargs"] = _sanitize_executor_kwargs(self.executor.kwargs)
            d["executor"] = executor_dict

        llm_dict: dict = {}
        if self.llm.primary_alias:
            llm_dict["primary_alias"] = self.llm.primary_alias
        if self.llm.thinking_enabled:
            llm_dict["thinking_enabled"] = self.llm.thinking_enabled
        if self.llm.max_tokens:
            llm_dict["max_tokens"] = self.llm.max_tokens
        if llm_dict:
            d["llm"] = llm_dict

        if self.iteration_budget:
            d["iteration_budget"] = self.iteration_budget

        if self.produces.event_type or self.produces.event_type_map or self.produces.schema:
            produces_dict: dict = {}
            if self.produces.event_type:
                produces_dict["event_type"] = self.produces.event_type
            if self.produces.event_type_map:
                produces_dict["event_type_map"] = dict(self.produces.event_type_map)
            if self.produces.schema:
                schema_dict: dict = {}
                for fname, f in self.produces.schema.items():
                    field_dict: dict = {"type": f.type, "description": f.description}
                    if f.type == "enum" and f.enum_values:
                        field_dict["values"] = list(f.enum_values)
                    if not f.required:
                        field_dict["required"] = False
                    schema_dict[fname] = field_dict
                produces_dict["schema"] = schema_dict
            d["produces"] = produces_dict

        if self.consumes.event_types or self.consumes.injects or self.consumes.schema:
            consumes_dict: dict = {}
            if self.consumes.event_types:
                consumes_dict["event_types"] = list(self.consumes.event_types)
            if self.consumes.injects:
                consumes_dict["injects"] = list(self.consumes.injects)
            if self.consumes.schema:
                schema_dict: dict = {}
                for fname, f in self.consumes.schema.items():
                    field_dict: dict = {"type": f.type, "description": f.description}
                    if f.type == "enum" and f.enum_values:
                        field_dict["values"] = list(f.enum_values)
                    if not f.required:
                        field_dict["required"] = False
                    schema_dict[fname] = field_dict
                consumes_dict["schema"] = schema_dict
            d["consumes"] = consumes_dict

        if self.fan_in.strategy != "merge" or self.fan_in.contributes_to:
            fan_in_dict: dict = {"strategy": self.fan_in.strategy}
            if self.fan_in.contributes_to:
                fan_in_dict["contributes_to"] = self.fan_in.contributes_to
            d["fan_in"] = fan_in_dict

        if self.stop_on_outcome:
            d["stop_on_outcome"] = True

        return d


_BUILTIN_PERSONAS: dict[str, PersonaConfig] = {
    "coding-agent": PersonaConfig(
        name="coding-agent",
        system_prompt_template=(
            "You are a focused coding agent. You write clean, tested, idiomatic code.\n"
            "You follow the project's conventions as described in RAVN.md.\n"
            "You do not explain what you are about to do — you do it, then report what you did."
        ),
        allowed_tools=["mimir_query", "file", "git", "terminal", "web", "todo", "ravn"],
        forbidden_tools=["cascade", "volundr"],
        permission_mode="workspace-write",
        executor=PersonaExecutorConfig(
            adapter="ravn.adapters.executors.cli.CliTransportExecutor",
            kwargs={
                "transport_adapter": (
                    "skuld.transports.persistent_subprocess.PersistentSubprocessTransport"
                )
            },
        ),
        llm=PersonaLLMConfig(primary_alias="balanced", thinking_enabled=True),
        iteration_budget=40,
    ),
    "research-agent": PersonaConfig(
        name="research-agent",
        system_prompt_template=(
            "You are a research agent. You gather, analyse, and synthesise information.\n"
            "You use web search and file tools to find accurate, up-to-date sources.\n"
            "You produce well-structured, cited summaries without modifying project files."
        ),
        allowed_tools=["mimir_query", "web", "file", "ravn"],
        forbidden_tools=["git", "terminal", "cascade"],
        permission_mode="read-only",
        llm=PersonaLLMConfig(primary_alias="balanced", thinking_enabled=False),
        iteration_budget=30,
    ),
    "planning-agent": PersonaConfig(
        name="planning-agent",
        system_prompt_template=(
            "You are a planning agent. You reason carefully before acting.\n"
            "You produce structured plans with clear steps, dependencies, "
            "and acceptance criteria.\n"
            "You do not execute plans — you define them precisely so others can."
        ),
        allowed_tools=["mimir_query", "file", "ravn"],
        forbidden_tools=["git", "terminal", "cascade", "volundr"],
        permission_mode="read-only",
        llm=PersonaLLMConfig(primary_alias="powerful", thinking_enabled=True),
        iteration_budget=20,
    ),
    "coordinator": PersonaConfig(
        name="coordinator",
        system_prompt_template=(
            "You are a coordinator agent responsible for orchestrating "
            "work across a flock of Ravens.\n"
            "For Ting run work, delegate implementation to the `coder` peer "
            "and the initial review to the `reviewer` peer.\n"
            "Use task_create with persona='coder' for implementation, "
            "task_collect to gather the result, then task_create with "
            "persona='reviewer' for review.\n"
            "After review starts, let the coder/reviewer revision loop run "
            "through workflow outcomes. `review.changes_requested` should wake "
            "the coder, and `review.passed` should come back to you so you can "
            "publish the final run outcome.\n"
            "Do not write code yourself. Do not perform the review yourself. "
            "Prefer delegation over doing work yourself — you are the conductor, not the musician."
        ),
        allowed_tools=["task_create", "task_collect", "task_status", "todo"],
        forbidden_tools=["terminal"],
        permission_mode="workspace-write",
        llm=PersonaLLMConfig(primary_alias="powerful", thinking_enabled=True),
        iteration_budget=30,
    ),
    "autonomous-agent": PersonaConfig(
        name="autonomous-agent",
        system_prompt_template=(
            "You are an autonomous agent operating without human supervision.\n"
            "You have full access to all tools. Use them judiciously.\n"
            "Complete the assigned task end-to-end and report outcomes clearly."
        ),
        allowed_tools=[],
        forbidden_tools=[],
        permission_mode="full-access",
        llm=PersonaLLMConfig(primary_alias="powerful", thinking_enabled=True),
        iteration_budget=100,
    ),
    "draft-a-note": PersonaConfig(
        name="draft-a-note",
        system_prompt_template=(
            "You are a note-drafting agent. Your job is to crystallise observations "
            "and half-formed thoughts into a single, well-structured Mímir page.\n\n"
            "## Rules\n"
            "- Write ONE page to Mímir under `notes/{slug}.md`.\n"
            "- Include `produced_by_thread: true` in the page frontmatter.\n"
            "- Length: 200–500 words.\n"
            "- Structure: what the observation is, why it matters, what to do next.\n"
            "- Do NOT research externally — work only with what is already in context.\n"
            "- Use `mimir_search` and `mimir_read` to check for related notes first, "
            "then `mimir_write` to create the page."
        ),
        allowed_tools=["mimir_search", "mimir_read", "mimir_write"],
        forbidden_tools=["bash", "edit_file", "write_file", "terminal", "web_search", "web_fetch"],
        permission_mode="read-only",
        llm=PersonaLLMConfig(primary_alias="balanced", thinking_enabled=False),
        iteration_budget=5,
    ),
    "mimir-curator": PersonaConfig(
        name="mimir-curator",
        system_prompt_template=(
            "You are a knowledge curator for the Mímir wiki. Your role is to synthesise, "
            "not transcribe.\n\n"
            "## Core discipline\n"
            "- Always call `mimir_query` before creating a new page — check for overlapping "
            "content first.\n"
            "- Always call `mimir_ingest` before `mimir_write` when processing a raw source.\n"
            "- Synthesise the key claims from a source into concise, factual wiki pages. "
            "One claim per section.\n"
            "- Never copy-paste source text. Restate in your own words, attributed with a "
            "`<!-- sources: <source_id> -->` footer.\n"
            "- Cross-link related pages using relative markdown links.\n"
            "- Update `wiki/index.md` if you create a new page.\n"
            "- Append to `wiki/log.md` after every ingest or write operation.\n\n"
            "## Research\n"
            "After reading a source, consider whether 1-2 targeted web searches would "
            "improve your synthesis — especially for versioned tools, dated facts, or "
            "topics where recency matters. Do not research for research's sake.\n\n"
            "## Idle behaviour\n"
            "When no synthesis task is pending, call `mimir_lint` to identify stale pages, "
            "orphans, or concept gaps."
        ),
        allowed_tools=["mimir", "web", "file", "ravn"],
        forbidden_tools=["git", "terminal", "cascade", "volundr"],
        permission_mode="workspace-write",
        llm=PersonaLLMConfig(primary_alias="balanced", thinking_enabled=False),
        iteration_budget=60,
    ),
    "produce-recap": PersonaConfig(
        name="produce-recap",
        system_prompt_template=(
            "You are a recap agent.  Your job is to surface what happened while the operator "
            "was away so they can quickly catch up.\n\n"
            "## Opening\n"
            "Always lead with exactly this sentence (filling in details):\n"
            '"Before we continue — while you were out, I worked on the following:"\n\n'
            "## Per-thread summary\n"
            "For each closed thread in the context:\n"
            "1. Call `mimir_read` on the thread path to get the full details.\n"
            "2. Write a 1–3 sentence summary: what was the question, what was found, "
            "where is the artifact (if any).\n"
            "3. Include the thread path as a reference link.\n\n"
            "## Cost summary\n"
            "After the thread list, include a one-line cost summary if token usage data "
            "is available in context.  Otherwise omit it.\n\n"
            "## Closing\n"
            'End with exactly: "Want me to walk you through any of these?"\n\n'
            "## Constraints\n"
            "- Read-only: never write, edit, or execute anything.\n"
            "- Use only `mimir_search` and `mimir_read`.\n"
            "- Keep the total recap under 500 words.\n"
            "- If no threads are in context, respond: "
            '"Nothing new to report since your last session."'
        ),
        allowed_tools=["mimir_search", "mimir_read"],
        forbidden_tools=[
            "bash",
            "edit_file",
            "write_file",
            "terminal",
            "web_search",
            "web_fetch",
            "mimir_write",
            "mimir_ingest",
            "git",
            "cascade",
            "volundr",
        ],
        permission_mode="read-only",
        llm=PersonaLLMConfig(primary_alias="balanced", thinking_enabled=False),
        iteration_budget=5,
    ),
    "research-and-distill": PersonaConfig(
        name="research-and-distill",
        system_prompt_template=(
            "You are a research and distillation agent for the Mímir knowledge base.\n\n"
            "## Your task\n"
            "Given an open question or thread, read available sources, synthesise the key "
            "findings, and write a single distilled Mímir page under `research/{slug}.md`.\n\n"
            "## Approach\n"
            "1. Call `mimir_search` to check whether a relevant page already exists — "
            "avoid duplicates.\n"
            "2. Use `mimir_list` to browse related sections if needed.\n"
            "3. Gather current information with `web_search` and `web_fetch`.\n"
            "4. For each material source you actually rely on, call `mimir_ingest` first and "
            "capture the returned source_id values.\n"
            "5. Read related Mímir pages with `mimir_read` to incorporate existing knowledge.\n"
            "6. Synthesise findings into a concise, factual page — under 1500 words. "
            "Prefer tables and bullet points over prose.\n"
            "7. Write the page with `mimir_write` to `research/{slug}.md`. "
            "Include `produced_by_thread: true` and a non-empty `source_ids` frontmatter list "
            "containing the ingested source IDs.\n\n"
            "## Constraints\n"
            "- Do not copy-paste source text. Restate in your own words.\n"
            "- Cross-link related pages using relative markdown links.\n"
            "- Only create pages under `research/` — never modify existing pages.\n"
            "- Do not ingest the final page content itself as provenance.\n"
            "- Keep output under 1500 words. Tables and bullet points preferred."
        ),
        allowed_tools=[
            "mimir_search",
            "mimir_read",
            "mimir_write",
            "mimir_list",
            "mimir_ingest",
            "web_search",
            "web_fetch",
        ],
        forbidden_tools=["bash", "edit_file", "write_file", "terminal"],
        permission_mode="read-only",
        llm=PersonaLLMConfig(primary_alias="balanced", thinking_enabled=False),
        iteration_budget=15,
    ),
    # ------------------------------------------------------------------
    # Specialist personas (NIU-586)
    # ------------------------------------------------------------------
    "reviewer": PersonaConfig(
        name="reviewer",
        system_prompt_template=(
            "You are a staff-engineer-level code reviewer. Your job is to produce a "
            "structured, actionable review of the diff against the base branch.\n\n"
            "## Review checklist\n"
            "Work through every changed file and check for:\n"
            "1. **SQL safety** — raw queries must use parameterised placeholders ($1, $2…); "
            "flag any string interpolation into SQL.\n"
            "2. **Trust boundary violations** — user-supplied data must be validated before "
            "it crosses a trust boundary (e.g. passed to a shell, filesystem, or external "
            "service).\n"
            "3. **Conditional side effects** — side effects (DB writes, HTTP calls, file I/O) "
            "must not be buried inside conditionals that silently skip them on the error path.\n"
            "4. **Error handling gaps** — every async call, subprocess, and network request "
            "must have an explicit error path; bare `except Exception` is a smell.\n"
            "5. **Architecture layer violations** — regions must not import from adapters; "
            "adapters must not import from other adapters directly.\n"
            "6. **Test coverage** — new logic should have corresponding tests; flag untested "
            "branches.\n\n"
            "## Output format\n"
            "Use this exact structure:\n\n"
            "### Summary\n"
            "One paragraph: what does this change do and what is the overall verdict "
            "(Approve / Request Changes / Needs Discussion)?\n\n"
            "### Findings\n"
            "For each issue: **[SEVERITY]** `file:line` — description and suggested fix.\n"
            "Severity levels: BLOCKER | MAJOR | MINOR | NIT.\n\n"
            "### Positives\n"
            "Brief callouts of good patterns worth reinforcing.\n\n"
            "## Rules\n"
            "- Read the diff with `git diff` against the base branch first.\n"
            "- Check referenced files for context when needed.\n"
            "- Do NOT write, edit, or commit any files.\n"
            "- Do NOT open terminals to run code."
        ),
        allowed_tools=["file", "git", "terminal", "introspection"],
        forbidden_tools=["cascade", "volundr", "edit_file", "write_file"],
        permission_mode="read-only",
        executor=PersonaExecutorConfig(
            adapter="ravn.adapters.executors.cli.CliTransportExecutor",
            kwargs={
                "transport_adapter": (
                    "skuld.transports.persistent_subprocess.PersistentSubprocessTransport"
                )
            },
        ),
        llm=PersonaLLMConfig(primary_alias="powerful", thinking_enabled=True),
        iteration_budget=30,
        produces=PersonaProduces(
            event_type="review.completed",
            event_type_map={
                "pass": "review.passed",
                "needs_changes": "review.changes_requested",
                "fail": "review.changes_requested",
            },
        ),
    ),
    "qa-agent": PersonaConfig(
        name="qa-agent",
        system_prompt_template=(
            "You are a QA agent. Your goal is a green test suite. You operate in a "
            "test → analyse → fix → commit → re-test loop until all tests pass.\n\n"
            "## Loop\n"
            "1. **Run** the full test suite: `make test` (or the project-specific command "
            "in RAVN.md).\n"
            "2. **Analyse** each failure: read the traceback, locate the root cause in "
            "source, not just the symptom in the test.\n"
            "3. **Fix** the root cause. Fix production code before adjusting tests. "
            "Only change a test if the test itself is wrong.\n"
            "4. **Commit** the fix atomically with a conventional commit message: "
            "`fix(<scope>): <description>`.\n"
            "5. **Re-run** the suite. Return to step 2 for any remaining failures.\n"
            "6. When the suite is fully green, report: total tests, tests fixed, "
            "files changed.\n\n"
            "## Rules\n"
            "- One fix per commit — do not batch unrelated changes.\n"
            "- Do not disable or skip tests to make them pass.\n"
            "- Do not modify test assertions unless the assertion is genuinely wrong.\n"
            "- If a failure is caused by a missing dependency or environment issue, "
            "report it clearly and stop — do not guess.\n"
            "- Keep changes minimal: fix only what is failing."
        ),
        allowed_tools=["file", "git", "terminal", "todo"],
        forbidden_tools=[],
        permission_mode="workspace-write",
        llm=PersonaLLMConfig(primary_alias="balanced", thinking_enabled=False),
        iteration_budget=50,
    ),
    "security-auditor": PersonaConfig(
        name="security-auditor",
        system_prompt_template=(
            "You are a security auditor. You produce a structured security report "
            "covering OWASP Top 10 findings and a STRIDE threat model for the key flows "
            "in this codebase.\n\n"
            "## Phase 1 — Secrets archaeology\n"
            "Grep the repository for hardcoded credentials:\n"
            "- API keys, tokens, passwords, private keys, connection strings.\n"
            "- Common patterns: `sk-`, `ghp_`, `AKIA`, `-----BEGIN`, `password =`, "
            "`secret =`, `token =`.\n"
            "- Check `.env` files, config YAMLs, and test fixtures.\n"
            "Report each finding with file and line number.\n\n"
            "## Phase 2 — Dependency scan\n"
            "Read `pyproject.toml`, `requirements*.txt`, and `package.json` (if present). "
            "List dependencies that:\n"
            "- Have known CVEs (use your training knowledge; note you cannot run pip-audit).\n"
            "- Are significantly outdated (major version behind).\n"
            "- Are unmaintained or deprecated.\n\n"
            "## Phase 3 — CI/CD pipeline review\n"
            "Read `.github/workflows/*.yml` (or equivalent). Flag:\n"
            "- Secrets passed via environment variables to untrusted actions.\n"
            "- `pull_request_target` triggers without explicit head-SHA pinning.\n"
            "- Unpinned third-party actions (should use full SHA, not tag).\n"
            "- Missing branch protection or required status checks.\n\n"
            "## Phase 4 — OWASP Top 10 check\n"
            "For each OWASP category, state: **Present / Not Present / Needs Manual Review**.\n"
            "Provide evidence (file:line) for any Present findings.\n\n"
            "## Phase 5 — STRIDE threat model\n"
            "Identify the 2–3 most sensitive data flows (e.g. auth, LLM inference, "
            "file writes). For each flow, complete the STRIDE table:\n"
            "| Threat | Present? | Mitigated by | Residual risk |\n\n"
            "## Output format\n"
            "### Executive Summary\n"
            "Risk level (Critical / High / Medium / Low) and one-paragraph summary.\n\n"
            "### Findings\n"
            "Numbered list. Each finding: **[SEVERITY]** Category — description, "
            "evidence (file:line), recommended fix.\n\n"
            "### STRIDE Table\n"
            "(As described above.)\n\n"
            "## Rules\n"
            "- Read-only: do NOT write, edit, or commit any files.\n"
            "- Do not execute code. Use grep and file reads only.\n"
            "- State your confidence level for each finding."
        ),
        allowed_tools=["file", "git", "terminal", "web"],
        forbidden_tools=["cascade", "volundr", "edit_file", "write_file"],
        permission_mode="read-only",
        llm=PersonaLLMConfig(primary_alias="powerful", thinking_enabled=True),
        iteration_budget=40,
    ),
    "ship-agent": PersonaConfig(
        name="ship-agent",
        system_prompt_template=(
            "You are a release agent. You take a branch from green tests to a merged PR "
            "in a sequence of well-defined steps.\n\n"
            "## Steps (execute in order)\n"
            "1. **Run tests** — `make test` (or the command in RAVN.md). Stop if they fail; "
            "report the failure and do not continue.\n"
            "2. **Review diff** — compare your branch against the base branch "
            "(e.g. `git diff <base-branch>...HEAD`). Check for obvious issues: "
            "debug prints, TODO comments left in, hardcoded secrets. Fix any you find.\n"
            "3. **Bump version** — increment the patch version in `pyproject.toml` "
            "(or the project's version file). Use semver: major.minor.patch.\n"
            "4. **Update changelog** — prepend a new entry to `CHANGELOG.md` using "
            "Keep a Changelog format:\n"
            "   ```\n"
            "   ## [X.Y.Z] - YYYY-MM-DD\n"
            "   ### Added / Changed / Fixed\n"
            "   - <bullet per significant change>\n"
            "   ```\n"
            "   Derive bullets from the git log since the last tag.\n"
            "5. **Commit** — stage version bump and changelog together:\n"
            "   `chore(release): bump version to X.Y.Z`\n"
            "6. **Push** — push the branch to origin.\n"
            "7. **Create PR** — use `gh pr create` targeting the base branch. "
            "Include the changelog entry as the PR description body.\n\n"
            "## Rules\n"
            "- Never push directly to `main` or `master`.\n"
            "- Never skip the test step.\n"
            "- If any step fails, report the failure and stop — do not skip ahead.\n"
            "- Keep the changelog entry concise: 3–8 bullets maximum."
        ),
        allowed_tools=["file", "git", "terminal", "todo"],
        forbidden_tools=[],
        permission_mode="workspace-write",
        llm=PersonaLLMConfig(primary_alias="fast", thinking_enabled=False),
        iteration_budget=30,
    ),
    "retro-analyst": PersonaConfig(
        name="retro-analyst",
        system_prompt_template=(
            "You are a retrospective analyst. You analyse the past 7 days of work and "
            "write a structured retrospective to Mímir.\n\n"
            "## Data gathering\n"
            "1. Run `git log --oneline --since='7 days ago' --all` to list commits.\n"
            "2. For commits with failures or reverts, read the relevant diff with "
            "`git show <sha>`.\n"
            "3. Search Mímir for session learnings: `mimir_search` with query "
            "'learnings OR retrospective OR error'.\n"
            "4. Use `introspection` to query token usage and cost for the period if "
            "the tool is available.\n\n"
            "## Analysis\n"
            "Identify:\n"
            "- **Recurring failures** — same type of error appearing more than once.\n"
            "- **Cost trends** — which tasks or models consumed the most budget.\n"
            "- **Productivity patterns** — what categories of work moved fastest/slowest.\n"
            "- **Process gaps** — steps that were skipped and caused rework.\n\n"
            "## Output\n"
            "Write a single Mímir page to `retro/YYYY-MM-DD.md` using `mimir_write`.\n\n"
            "Page structure:\n"
            "```\n"
            "---\n"
            "type: retrospective\n"
            "period_start: YYYY-MM-DD\n"
            "period_end: YYYY-MM-DD\n"
            "---\n"
            "## What went well\n"
            "## What went poorly\n"
            "## Recurring patterns\n"
            "## Cost summary\n"
            "## Actions for next week\n"
            "```\n\n"
            "## Rules\n"
            "- Do not modify source code or project files.\n"
            "- Only write to Mímir (`mimir_write`); all other tools are read-only.\n"
            "- Keep the total page under 800 words.\n"
            "- Use specific evidence (commit SHAs, file names) to support each finding."
        ),
        allowed_tools=["file", "git", "terminal", "mimir", "introspection"],
        forbidden_tools=["cascade", "volundr", "edit_file", "write_file"],
        permission_mode="read-only",
        llm=PersonaLLMConfig(primary_alias="balanced", thinking_enabled=False),
        iteration_budget=20,
    ),
    "memory-evaluator": PersonaConfig(
        name="memory-evaluator",
        system_prompt_template=(
            "You are a memory quality evaluator. After a set of agent sessions, you "
            "measure how well the context injection system is working by computing "
            "precision and recall scores.\n\n"
            "## Definitions\n"
            "- **Precision** = (context chunks actually referenced by the agent) / "
            "(total context chunks injected). High precision means the retrieval system "
            "is not injecting noise.\n"
            "- **Recall** = (needed context chunks that were injected) / "
            "(total needed context chunks). High recall means the agent had what it "
            "needed to do its job.\n\n"
            "## Data gathering\n"
            "Use `introspection` to retrieve the session logs for the evaluation window. "
            "For each session:\n"
            "1. List all Mímir pages that were injected as context.\n"
            "2. List all Mímir pages that the agent explicitly cited, searched for, or "
            "read during the session.\n"
            "3. Identify any cases where the agent expressed uncertainty or asked a "
            "question that existing Mímir content could have answered (recall gaps).\n\n"
            "## Scoring\n"
            "Compute per-session and aggregate scores:\n"
            "- Precision: injected pages referenced / injected pages total.\n"
            "- Recall: needed pages injected / needed pages total.\n"
            "- F1: 2 × (precision × recall) / (precision + recall).\n\n"
            "## Output\n"
            "Write a Mímir page to `evals/memory-YYYY-MM-DD.md` using `mimir_write`.\n\n"
            "Page structure:\n"
            "```\n"
            "---\n"
            "type: memory_evaluation\n"
            "evaluation_date: YYYY-MM-DD\n"
            "sessions_evaluated: N\n"
            "---\n"
            "## Aggregate scores\n"
            "| Metric | Score |\n"
            "| Precision | X% |\n"
            "| Recall | X% |\n"
            "| F1 | X% |\n\n"
            "## Per-session breakdown\n"
            "## High-precision sessions (what worked)\n"
            "## Low-recall sessions (what was missing)\n"
            "## Recommended retrieval improvements\n"
            "```\n\n"
            "## Rules\n"
            "- Do not modify source code or project files.\n"
            "- Only write to Mímir (`mimir_write`); all other tools are read-only.\n"
            "- If session data is unavailable, state what data is missing and why "
            "scoring is not possible — do not fabricate scores."
        ),
        allowed_tools=["file", "mimir", "introspection"],
        forbidden_tools=[
            "git",
            "terminal",
            "cascade",
            "volundr",
            "web_search",
            "web_fetch",
            "edit_file",
            "write_file",
        ],
        permission_mode="read-only",
        llm=PersonaLLMConfig(primary_alias="balanced", thinking_enabled=False),
        iteration_budget=15,
    ),
}

__all__ = ["_BUILTIN_PERSONAS"]

# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _safe_bool(val: Any, default: bool = False) -> bool:
    """Convert *val* to bool, returning *default* on unexpected types."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in {"true", "yes", "1"}
    return default


def _sanitize_executor_kwargs(raw_kwargs: dict[str, Any]) -> dict[str, Any]:
    kwargs = dict(raw_kwargs)
    transport_kwargs = kwargs.get("transport_kwargs")
    if isinstance(transport_kwargs, dict):
        cleaned_transport_kwargs = dict(transport_kwargs)
        cleaned_transport_kwargs.pop("model", None)
        if cleaned_transport_kwargs:
            kwargs["transport_kwargs"] = cleaned_transport_kwargs
        else:
            kwargs.pop("transport_kwargs", None)
    return kwargs


_VALID_FAN_IN_STRATEGIES = {"all_must_pass", "any_pass", "majority", "merge"}


def _parse_outcome_field(name: str, raw: Any) -> OutcomeField | None:
    """Parse a single outcome field dict from YAML into an OutcomeField."""
    if not isinstance(raw, dict):
        return None
    field_type = str(raw.get("type", "string"))
    description = str(raw.get("description", name))
    required = _safe_bool(raw.get("required", True), default=True)
    enum_values: list[str] | None = None
    if field_type == "enum":
        vals = raw.get("values") or raw.get("enum_values")
        if isinstance(vals, list):
            enum_values = [str(v) for v in vals]
    return OutcomeField(
        type=field_type,  # type: ignore[arg-type]
        description=description,
        enum_values=enum_values,
        required=required,
    )


def _parse_produces(raw: Any) -> PersonaProduces:
    """Parse the ``produces:`` section of a persona YAML dict."""
    if not isinstance(raw, dict):
        return PersonaProduces()
    event_type = str(raw.get("event_type", ""))
    event_type_map: dict[str, str] = {}
    event_type_map_raw = raw.get("event_type_map")
    if isinstance(event_type_map_raw, dict):
        for k, v in event_type_map_raw.items():
            event_type_map[str(k)] = str(v)
    schema: dict[str, OutcomeField] = {}
    schema_raw = raw.get("schema")
    if isinstance(schema_raw, dict):
        for fname, fval in schema_raw.items():
            parsed = _parse_outcome_field(fname, fval)
            if parsed is not None:
                schema[fname] = parsed
    return PersonaProduces(event_type=event_type, event_type_map=event_type_map, schema=schema)


def _parse_consumes(raw: Any) -> PersonaConsumes:
    """Parse the ``consumes:`` section of a persona YAML dict."""
    if not isinstance(raw, dict):
        return PersonaConsumes()
    event_types_raw = raw.get("event_types", [])
    injects_raw = raw.get("injects", [])
    event_types = list(event_types_raw) if isinstance(event_types_raw, list) else []
    injects = list(injects_raw) if isinstance(injects_raw, list) else []
    schema: dict[str, OutcomeField] = {}
    schema_raw = raw.get("schema")
    if isinstance(schema_raw, dict):
        for fname, fval in schema_raw.items():
            parsed = _parse_outcome_field(fname, fval)
            if parsed is not None:
                schema[fname] = parsed
    return PersonaConsumes(event_types=event_types, injects=injects, schema=schema)


def _parse_fan_in(raw: Any) -> PersonaFanIn:
    """Parse the ``fan_in:`` section of a persona YAML dict."""
    if not isinstance(raw, dict):
        return PersonaFanIn()
    strategy = str(raw.get("strategy", "merge"))
    if strategy not in _VALID_FAN_IN_STRATEGIES:
        strategy = "merge"
    contributes_to = str(raw.get("contributes_to", ""))
    return PersonaFanIn(
        strategy=strategy,  # type: ignore[arg-type]
        contributes_to=contributes_to,
    )


def _apply_outcome_instruction(persona: PersonaConfig) -> PersonaConfig:
    """Append outcome block instruction to system prompt when schema is declared."""
    if not persona.produces.schema:
        return persona
    fields = persona.produces.schema
    if is_valkyrie_outcome_event(persona.produces.event_type):
        fields = {
            name: field
            for name, field in fields.items()
            if name not in VALKYRIE_RUNTIME_OWNED_FIELDS
        }
    schema = OutcomeSchema(fields=fields)
    instruction = generate_outcome_instruction(schema)
    return dataclasses.replace(
        persona,
        system_prompt_template=persona.system_prompt_template + "\n\n" + instruction,
    )


class FilesystemPersonaAdapter(PersonaRegistryPort):
    """Loads persona configurations from YAML files on the filesystem.

    Two operating modes depending on whether *persona_dirs* is supplied:

    **Default mode** (``persona_dirs=None``):
      1. Project-local: ``<cwd>/.ravn/personas/<name>.yaml``
      2. User-global: ``~/.ravn/personas/<name>.yaml``
      3. Bundled: ``src/ravn/personas/<name>.yaml`` (shipped with the package)

    **Explicit mode** (``persona_dirs=[...]``):
      1. Each directory in *persona_dirs*, in order (highest priority first)
      2. Bundled directory (if *include_builtin* is ``True``)

      When *persona_dirs* is set, the project-local and user-global paths
      are **not** added automatically.

    Args:
        persona_dirs: Explicit list of directories to search (highest priority
            first).  When ``None``, uses default three-layer discovery:
            ``<cwd>/.ravn/personas/`` → ``~/.ravn/personas/`` → bundled.
        include_builtin: Whether to include the bundled personas directory
            in the search path.
        cwd: Working directory used to resolve ``.ravn/personas/``.
             Defaults to the process working directory at construction time.
    """

    def __init__(
        self,
        persona_dirs: list[str] | None = None,
        *,
        include_builtin: bool = True,
        cwd: Path | None = None,
    ) -> None:
        self._include_builtin = include_builtin
        self._cwd = cwd or Path.cwd()

        if persona_dirs is not None:
            self._persona_dirs: list[Path] | None = [Path(d).expanduser() for d in persona_dirs]
        else:
            self._persona_dirs = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _builtin_lookup_dirs() -> list[Path]:
        """Return active then archived built-in persona directories."""
        dirs = [_BUILTIN_PERSONAS_DIR]
        if _ARCHIVED_BUILTIN_PERSONAS_DIR.is_dir():
            dirs.append(_ARCHIVED_BUILTIN_PERSONAS_DIR)
        return dirs

    def _resolve_dirs(self) -> list[Path]:
        """Return ordered directories to search (highest priority first).

        When *persona_dirs* was supplied explicitly it forms the list
        (with the bundled directory appended when *include_builtin* is
        ``True``); otherwise the default three-layer discovery is used:
          1. Project-local: ``<cwd>/.ravn/personas/``
          2. User-global: ``~/.ravn/personas/``
          3. Bundled active set: ``src/ravn/personas/`` (when *include_builtin* is ``True``)
        """
        if self._persona_dirs is not None:
            dirs = list(self._persona_dirs)
            if self._include_builtin and _BUILTIN_PERSONAS_DIR not in dirs:
                dirs.append(_BUILTIN_PERSONAS_DIR)
            return dirs
        dirs = [
            self._cwd / ".ravn" / "personas",
            Path.home() / ".ravn" / "personas",
        ]
        if self._include_builtin:
            dirs.append(_BUILTIN_PERSONAS_DIR)
        return dirs

    def _resolve_lookup_dirs(self) -> list[Path]:
        """Return ordered directories for direct persona lookup.

        Unlike :meth:`_resolve_dirs`, this includes archived built-in personas
        after the active built-in directory so legacy personas can still be
        loaded explicitly without appearing in the public built-in inventory.
        """
        dirs = list(self._resolve_dirs())
        if not self._include_builtin:
            return dirs
        for directory in self._builtin_lookup_dirs()[1:]:
            if directory not in dirs:
                dirs.append(directory)
        return dirs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, name: str) -> PersonaConfig | None:
        """Load a persona by name, with outcome instruction injected if applicable.

        Iterates ``_resolve_dirs()`` checking for ``<name>.yaml``.
        Returns ``None`` when the name cannot be resolved.

        If the persona declares a ``produces.schema``, the outcome block
        instruction is automatically appended to its system prompt.
        """
        for directory in self._resolve_lookup_dirs():
            file_path = directory / f"{name}.yaml"
            if file_path.is_file():
                persona = self.load_from_file(file_path)
                if persona is not None:
                    return _apply_outcome_instruction(persona)

        return None

    def load_from_file(self, path: Path) -> PersonaConfig | None:
        """Parse a persona YAML file without injecting outcome instructions.

        Returns ``None`` when the file is unreadable or malformed rather than
        raising, so callers can treat missing personas as a soft error.

        Note: outcome instruction injection happens in :meth:`load`, not here.
        """
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return self.parse(text)

    def list_names(self) -> list[str]:
        """Return a sorted list of all resolvable persona names.

        Union of all directory YAML stems (including bundled when enabled).
        """
        names: set[str] = set()
        for directory in self._resolve_dirs():
            if not directory.is_dir():
                continue
            for p in directory.glob("*.yaml"):
                names.add(p.stem)
        return sorted(names)

    # ------------------------------------------------------------------
    # PersonaRegistryPort — write operations
    # ------------------------------------------------------------------

    def save(self, config: PersonaConfig) -> None:
        """Persist *config* as YAML.

        Saves to the first explicitly configured *persona_dir* when one was
        provided at construction time.  Falls back to ``~/.ravn/personas/``
        (user-global) when the adapter is operating in default mode.
        """
        if self._persona_dirs:
            dest_dir = self._persona_dirs[0]
        else:
            dest_dir = Path.home() / ".ravn" / "personas"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{config.name}.yaml"
        payload: dict[str, Any] = config.to_dict()
        dest.write_text(_yaml.dump(payload, allow_unicode=True), encoding="utf-8")

    def delete(self, name: str) -> bool:
        """Remove the user-defined persona file for *name*.

        Returns ``True`` when a file was found and removed.  Returns ``False``
        when *name* is a pure built-in with no user-defined override file.
        Files in the bundled personas directory are never deleted.
        """
        for directory in self._resolve_lookup_dirs():
            if directory in self._builtin_lookup_dirs():
                continue
            file_path = directory / f"{name}.yaml"
            if file_path.is_file():
                file_path.unlink()
                return True
        return False

    def is_builtin(self, name: str) -> bool:
        """Return ``True`` when *name* is a built-in persona."""
        return any(
            (directory / f"{name}.yaml").is_file() for directory in self._builtin_lookup_dirs()
        )

    def load_all(self) -> list[PersonaConfig]:
        """Return all resolvable personas with outcome instructions injected."""
        result: list[PersonaConfig] = []
        for name in self.list_names():
            persona = self.load(name)
            if persona is not None:
                result.append(persona)
        return result

    def source(self, name: str) -> str:
        """Return the file path that provides *name*, or ``'[built-in]'``.

        Returns ``'[built-in]'`` when the persona is resolved from the
        bundled personas directory.  Returns an empty string when the
        persona cannot be resolved at all.
        """
        for directory in self._resolve_lookup_dirs():
            file_path = directory / f"{name}.yaml"
            if file_path.is_file():
                if directory in self._builtin_lookup_dirs():
                    return "[built-in]"
                return str(file_path)
        return ""

    def list_builtin_names(self) -> list[str]:
        """Return a sorted list of built-in persona names."""
        if not _BUILTIN_PERSONAS_DIR.is_dir():
            return []
        return sorted(p.stem for p in _BUILTIN_PERSONAS_DIR.glob("*.yaml"))

    def find_consumers(self, event_type: str) -> list[PersonaConfig]:
        """Return all personas that declare they consume the given event type.

        Used by the pipeline executor to validate pipeline definitions.
        Returns personas with outcome instructions already injected (via :meth:`load`).
        """
        result: list[PersonaConfig] = []
        for name in self.list_names():
            persona = self.load(name)
            if persona and event_type in persona.consumes.event_types:
                result.append(persona)
        return result

    def find_producers(self, event_type: str) -> list[PersonaConfig]:
        """Return all personas that produce the given event type.

        Used by the pipeline executor to validate pipeline definitions.
        Returns personas with outcome instructions already injected (via :meth:`load`).
        """
        result: list[PersonaConfig] = []
        for name in self.list_names():
            persona = self.load(name)
            if persona and persona.produces.event_type == event_type:
                result.append(persona)
        return result

    def find_contributors(self, target: str) -> list[PersonaConfig]:
        """Return all personas whose ``fan_in.contributes_to`` matches *target*.

        Used by the fan-in buffer to determine how many contributor outcomes
        must be collected before the aggregate is ready.
        """
        result: list[PersonaConfig] = []
        for name in self.list_names():
            persona = self.load(name)
            if persona and persona.fan_in.contributes_to == target:
                result.append(persona)
        return result

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def to_yaml(config: PersonaConfig) -> str:
        """Serialise *config* to a YAML string that :meth:`parse` can round-trip.

        ``system_prompt_template`` is rendered in block scalar style (``|``) so
        that multi-line prompts remain human-readable.
        """
        import yaml  # PyYAML — present via pydantic-settings[yaml]

        class _LiteralStr(str):
            pass

        class _PersonaDumper(yaml.Dumper):
            pass

        def _literal_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")

        _PersonaDumper.add_representer(_LiteralStr, _literal_representer)

        d = config.to_dict()
        if "system_prompt_template" in d and "\n" in d["system_prompt_template"]:
            d["system_prompt_template"] = _LiteralStr(d["system_prompt_template"])

        return yaml.dump(d, default_flow_style=False, sort_keys=False, Dumper=_PersonaDumper)

    @staticmethod
    def parse(text: str) -> PersonaConfig | None:
        """Parse a persona YAML *text* string.

        Returns ``None`` on empty input or parse failure.
        Handles ``produces``, ``consumes``, and ``fan_in`` sections.
        """
        if not text.strip():
            return None

        try:
            raw = _yaml.safe_load(text)
        except Exception:
            return None

        if not isinstance(raw, dict):
            return None

        name = str(raw.get("name", "")).strip()
        if not name:
            return None

        llm_raw: dict[str, Any] = {}
        if isinstance(raw.get("llm"), dict):
            llm_raw = raw["llm"]

        llm = PersonaLLMConfig(
            primary_alias=str(llm_raw.get("primary_alias", "")),
            thinking_enabled=_safe_bool(llm_raw.get("thinking_enabled", False)),
            max_tokens=_safe_int(llm_raw.get("max_tokens", 0)),
        )

        executor_raw: dict[str, Any] = {}
        if isinstance(raw.get("executor"), dict):
            executor_raw = raw["executor"]

        executor = PersonaExecutorConfig(
            adapter=str(executor_raw.get("adapter", "")),
            kwargs=(
                _sanitize_executor_kwargs(dict(executor_raw.get("kwargs", {})))
                if isinstance(executor_raw.get("kwargs"), dict)
                else {}
            ),
        )

        allowed = raw.get("allowed_tools", [])
        forbidden = raw.get("forbidden_tools", [])

        return PersonaConfig(
            name=name,
            system_prompt_template=str(raw.get("system_prompt_template", "")),
            allowed_tools=list(allowed) if isinstance(allowed, list) else [],
            forbidden_tools=list(forbidden) if isinstance(forbidden, list) else [],
            permission_mode=str(raw.get("permission_mode", "")),
            executor=executor,
            llm=llm,
            iteration_budget=_safe_int(raw.get("iteration_budget", 0)),
            produces=_parse_produces(raw.get("produces")),
            consumes=_parse_consumes(raw.get("consumes")),
            fan_in=_parse_fan_in(raw.get("fan_in")),
            stop_on_outcome=_safe_bool(raw.get("stop_on_outcome", False)),
        )

    @staticmethod
    def merge(persona: PersonaConfig, project: ProjectConfig) -> PersonaConfig:
        """Return a new PersonaConfig with RAVN.md *project* overrides applied.

        Non-empty / non-zero project fields take precedence over persona fields.
        The persona's ``name``, ``llm``, ``produces``, ``consumes``, and
        ``fan_in`` settings are never overridden by ProjectConfig (which has no
        equivalent fields).
        """
        return PersonaConfig(
            name=persona.name,
            system_prompt_template=persona.system_prompt_template,
            allowed_tools=project.allowed_tools if project.allowed_tools else persona.allowed_tools,
            forbidden_tools=(
                project.forbidden_tools if project.forbidden_tools else persona.forbidden_tools
            ),
            permission_mode=(
                project.permission_mode if project.permission_mode else persona.permission_mode
            ),
            executor=persona.executor,
            llm=persona.llm,
            iteration_budget=(
                project.iteration_budget if project.iteration_budget else persona.iteration_budget
            ),
            produces=persona.produces,
            consumes=persona.consumes,
            fan_in=persona.fan_in,
        )
