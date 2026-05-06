"""Saga template dataclasses and YAML loader.

Templates describe predefined saga workflows as YAML files.  The loader
parses a template, substitutes ``{event.field}`` placeholders, validates the
structure, and returns a :class:`SagaTemplate` ready for execution.

Supports two template formats:

**Legacy format** (phases with raids)::

    name: "Review: {event.repo}#{event.pr_number}"
    feature_branch: "{event.branch}"
    base_branch: "{event.base_branch}"
    repos:
      - "{event.repo}"
    phases:
      - name: Code Review
        raids:
          - name: "Review PR #{event.pr_number}"
            persona: reviewer
            prompt: "..."

**Pipeline format** (stages with parallel/sequential participants)::

    name: "Review: {event.repo}#{event.pr_number}"
    feature_branch: "{event.branch}"
    base_branch: "{event.base_branch}"
    repos:
      - "{event.repo}"
    flock_flow: code-review-flow          # optional named flock flow (NIU-644)
    stages:
      - name: parallel-review
        parallel:
          - persona: reviewer
            prompt: "Review the diff"
            persona_overrides:            # optional per-stage overrides (NIU-644)
              llm:
                primary_alias: powerful
                thinking_enabled: true
              system_prompt_extra: |
                Production-critical change; be thorough.
          - persona: security-auditor
            prompt: "Security audit"
        fan_in: all_must_pass
      - name: test
        sequential:
          - persona: qa-agent
            prompt: "Run tests"
        condition: "stages.parallel-review.verdict == pass"
      - name: approval
        gate: human
        notify: [slack]
        condition: "stages.test.verdict == pass"

``persona_overrides`` merges onto the matching persona from ``flock_flow``.
The keys ``allowed_tools`` and ``forbidden_tools`` are rejected at parse
time — they are a security boundary that cannot be overridden at the
pipeline layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Regex matching {event.field_name} placeholders in template strings.
_EVENT_PLACEHOLDER_RE = re.compile(r"\{event\.([^}]+)\}")


# ---------------------------------------------------------------------------
# Template data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TemplateRaid:
    """A single raid within a template phase."""

    name: str
    description: str
    acceptance_criteria: list[str]
    declared_files: list[str]
    estimate_hours: float
    prompt: str
    persona: str = ""
    persona_overrides: dict | None = None  # NIU-644: per-stage flock persona overrides


@dataclass(frozen=True)
class TemplatePhase:
    """A phase within a saga template."""

    name: str
    raids: list[TemplateRaid]
    needs_approval: bool = False
    parallel: bool = False
    fan_in: str = "merge"  # all_must_pass | any_pass | majority | merge
    condition: str | None = None
    gate: str | None = None  # "human" for human approval gate
    notify: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SagaTemplate:
    """A fully-loaded and interpolated saga template."""

    name: str
    feature_branch: str
    base_branch: str
    repos: list[str]
    phases: list[TemplatePhase]
    flock_flow: str | None = None  # NIU-644: named flock flow for all stages in this pipeline


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------


def _interpolate(text: str, payload: dict) -> str:
    """Replace ``{event.field}`` placeholders with values from *payload*.

    Unknown fields are left as-is so templates remain renderable even when
    some payload keys are absent.
    """

    def _replace(m: re.Match) -> str:
        key = m.group(1)
        value = payload.get(key)
        if value is None:
            return m.group(0)
        return str(value)

    return _EVENT_PLACEHOLDER_RE.sub(_replace, text)


def _interpolate_value(value: Any, payload: dict) -> Any:
    """Recursively interpolate ``{event.*}`` placeholders in parsed YAML data.

    Operates on already-parsed Python objects (dicts, lists, strings) so that
    payload values containing YAML metacharacters cannot alter the document
    structure.
    """
    if isinstance(value, str):
        return _interpolate(value, payload)
    if isinstance(value, dict):
        return {k: _interpolate_value(v, payload) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_value(item, payload) for item in value]
    return value


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_raid_from_dict(r: dict, phase_name: str) -> TemplateRaid:
    """Parse a single raid dict into a TemplateRaid."""
    return TemplateRaid(
        name=r.get("name", ""),
        description=r.get("description", ""),
        acceptance_criteria=r.get("acceptance_criteria", []),
        declared_files=r.get("declared_files", []),
        estimate_hours=float(r.get("estimate_hours", 2.0)),
        prompt=r.get("prompt", ""),
        persona=r.get("persona", ""),
        persona_overrides=r.get("persona_overrides") or None,
    )


def _parse_participant_as_raid(p: dict, stage_name: str) -> TemplateRaid:
    """Convert a pipeline participant dict to a TemplateRaid."""
    persona = p.get("persona", "")
    name = p.get("name") or f"{persona} in {stage_name}"
    return TemplateRaid(
        name=name,
        description=p.get("description", ""),
        acceptance_criteria=p.get("acceptance_criteria", []),
        declared_files=p.get("declared_files", []),
        estimate_hours=float(p.get("estimate_hours", 1.0)),
        prompt=p.get("prompt", ""),
        persona=persona,
        persona_overrides=p.get("persona_overrides") or None,
    )


def _parse_stages(data: dict) -> list[TemplatePhase]:
    """Parse pipeline-format ``stages`` key into TemplatePhase list."""
    phases: list[TemplatePhase] = []
    for stage in data.get("stages", []):
        name = stage.get("name", "")
        fan_in = stage.get("fan_in", "merge")
        condition = stage.get("condition") or None
        gate = stage.get("gate") or None
        notify = stage.get("notify", [])
        needs_approval = gate == "human"

        parallel_list = stage.get("parallel")
        sequential_list = stage.get("sequential")

        if gate == "human" and not parallel_list and not sequential_list:
            # Human gate with no participants: create a placeholder raid
            raids = [
                TemplateRaid(
                    name=f"Human approval: {name}",
                    description=f"Human approval gate for stage '{name}'.",
                    acceptance_criteria=[],
                    declared_files=[],
                    estimate_hours=0.25,
                    prompt="",
                    persona="",
                )
            ]
            phases.append(
                TemplatePhase(
                    name=name,
                    raids=raids,
                    needs_approval=True,
                    parallel=False,
                    fan_in=fan_in,
                    condition=condition,
                    gate=gate,
                    notify=list(notify),
                )
            )
            continue

        participants = parallel_list or sequential_list or []
        is_parallel = parallel_list is not None
        raids = [_parse_participant_as_raid(p, name) for p in participants]

        phases.append(
            TemplatePhase(
                name=name,
                raids=raids,
                needs_approval=needs_approval,
                parallel=is_parallel,
                fan_in=fan_in,
                condition=condition,
                gate=gate,
                notify=list(notify),
            )
        )
    return phases


def _parse_phases(data: dict) -> list[TemplatePhase]:
    """Parse legacy-format ``phases`` key into TemplatePhase list."""
    phases: list[TemplatePhase] = []
    for p in data.get("phases", []):
        raids = [_parse_raid_from_dict(r, p.get("name", "")) for r in p.get("raids", [])]
        phases.append(
            TemplatePhase(
                name=p["name"],
                raids=raids,
                needs_approval=bool(p.get("needs_approval", False)),
                parallel=bool(p.get("parallel", False)),
                fan_in=p.get("fan_in", "merge"),
                condition=p.get("condition") or None,
                gate=p.get("gate") or None,
                notify=list(p.get("notify", [])),
            )
        )
    return phases


# ---------------------------------------------------------------------------
# Security boundary keys that cannot be overridden at the pipeline layer
# ---------------------------------------------------------------------------

_SECURITY_OVERRIDE_KEYS = frozenset({"allowed_tools", "forbidden_tools"})


def _check_persona_overrides_security(overrides: dict, path: str) -> None:
    """Reject security-boundary keys inside a persona_overrides block.

    :raises ValueError: When ``allowed_tools`` or ``forbidden_tools`` appear
        in *overrides*.  The error message cites the offending path so the
        caller can quickly locate the problem in their YAML.
    """
    for key in _SECURITY_OVERRIDE_KEYS:
        if key in overrides:
            raise ValueError(
                f"persona_overrides at '{path}' sets '{key}', which is a "
                "security boundary and cannot be overridden at the pipeline "
                "layer. Remove it from the YAML."
            )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_template(data: dict) -> None:
    """Validate parsed (and interpolated) template data.

    :raises ValueError: When mandatory fields are missing or the template
        structure is invalid, including security-boundary violations in
        ``persona_overrides`` blocks.
    """
    if not data.get("name"):
        raise ValueError("Template is missing required field 'name'")

    has_phases = "phases" in data
    has_stages = "stages" in data

    if has_stages:
        for stage_idx, stage in enumerate(data.get("stages", [])):
            stage_name = stage.get("name") or f"stage[{stage_idx}]"
            if not stage.get("name"):
                raise ValueError(f"Stage at index {stage_idx} is missing required field 'name'")
            gate = stage.get("gate")
            parallel = stage.get("parallel")
            sequential = stage.get("sequential")
            if gate != "human" and not parallel and not sequential:
                raise ValueError(
                    f"Stage '{stage_name}' must have 'parallel', 'sequential', or 'gate: human'"
                )
            # Validate persona_overrides security boundaries in all participants
            for participant_list_key in ("parallel", "sequential"):
                for p_idx, p in enumerate(stage.get(participant_list_key) or []):
                    overrides = p.get("persona_overrides")
                    if overrides:
                        path = (
                            f"stages.{stage_name}.{participant_list_key}[{p_idx}].persona_overrides"
                        )
                        _check_persona_overrides_security(overrides, path)
        return

    if not has_phases:
        raise ValueError("Template must have 'phases' or 'stages'")

    for phase_idx, phase in enumerate(data.get("phases", [])):
        phase_name = phase.get("name") or f"phase[{phase_idx}]"
        if not phase.get("name"):
            raise ValueError(f"Phase at index {phase_idx} is missing required field 'name'")
        raids = phase.get("raids", [])
        if not raids:
            raise ValueError(
                f"Phase '{phase_name}' has no raids — every phase must have at least one raid"
            )
        for raid_idx, raid in enumerate(raids):
            raid_name = raid.get("name") or f"raid[{raid_idx}]"
            if not raid.get("name"):
                raise ValueError(
                    f"Raid at index {raid_idx} in phase '{phase_name}'"
                    " is missing required field 'name'"
                )
            if not raid.get("persona"):
                raise ValueError(
                    f"Raid '{raid_name}' in phase '{phase_name}'"
                    " is missing required field 'persona'"
                )
            overrides = raid.get("persona_overrides")
            if overrides:
                path = f"phases.{phase_name}.raids[{raid_idx}].persona_overrides"
                _check_persona_overrides_security(overrides, path)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

# Path to bundled templates shipped with the package.
BUNDLED_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def load_template(name: str, templates_dir: Path, payload: dict) -> SagaTemplate:
    """Load and interpolate a YAML saga template.

    :param name: Template name without extension (e.g. ``"review"``).
    :param templates_dir: Directory to search first; falls back to bundled dir.
    :param payload: Sleipnir event payload used for ``{event.*}`` substitution.
    :raises FileNotFoundError: When the template cannot be found.
    :raises ValueError: When the template fails validation.
    """
    candidates = [
        templates_dir / f"{name}.yaml",
        BUNDLED_TEMPLATES_DIR / f"{name}.yaml",
    ]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        raise FileNotFoundError(
            f"Saga template {name!r} not found in {templates_dir} or bundled templates"
        )

    raw = path.read_text(encoding="utf-8")
    # Parse YAML first, then interpolate — prevents payload values with YAML
    # metacharacters from altering the document structure.
    data = _interpolate_value(yaml.safe_load(raw), payload)

    _validate_template(data)

    if "stages" in data:
        phases = _parse_stages(data)
    else:
        phases = _parse_phases(data)

    return SagaTemplate(
        name=data["name"],
        feature_branch=data.get("feature_branch", "main"),
        base_branch=data.get("base_branch", "main"),
        repos=data.get("repos", []),
        phases=phases,
        flock_flow=data.get("flock_flow") or None,
    )


def load_template_from_string(yaml_str: str, payload: dict) -> SagaTemplate:
    """Load and interpolate a YAML saga template from a string.

    Behaves like :func:`load_template` but reads from *yaml_str* directly
    instead of a file.  Used by the dynamic pipeline API.

    :param yaml_str: Raw YAML template string.
    :param payload: Context payload used for ``{event.*}`` substitution.
    :raises ValueError: When the template fails validation.
    """
    data = _interpolate_value(yaml.safe_load(yaml_str), payload)
    _validate_template(data)

    if "stages" in data:
        phases = _parse_stages(data)
    else:
        phases = _parse_phases(data)

    return SagaTemplate(
        name=data.get("name", "pipeline"),
        feature_branch=data.get("feature_branch", "main"),
        base_branch=data.get("base_branch", "main"),
        repos=data.get("repos", []),
        phases=phases,
        flock_flow=data.get("flock_flow") or None,
    )
