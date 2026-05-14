"""Saga structure validation — shared by one-shot and interactive decomposition paths.

Validates raw JSON (from any source: LLM adapter, planning session, manual input)
against the SagaStructure schema and returns typed domain objects.
"""

from __future__ import annotations

import json
import re

from ting.domain.models import PhaseSpec, RunSpec, SagaStructure


class ValidationError(Exception):
    """Raised when decomposition output fails structural validation."""


def parse_and_validate(
    raw: str,
    *,
    min_estimate_hours: float = 2.0,
    max_estimate_hours: float = 8.0,
) -> SagaStructure:
    """Parse raw JSON string and validate against SagaStructure schema."""
    # Strip markdown fences if LLM included them despite instructions
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.index("\n")
        cleaned = cleaned[first_newline + 1 :]
    if cleaned.endswith("```"):
        cleaned = cleaned[: cleaned.rfind("```")]
    cleaned = cleaned.strip()

    data = json.loads(cleaned)

    if not isinstance(data, dict):
        raise ValidationError("Response must be a JSON object")

    name = data.get("name")
    if not name or not isinstance(name, str):
        raise ValidationError("Missing or invalid 'name' field")

    phases_raw = data.get("phases")
    if not isinstance(phases_raw, list) or not phases_raw:
        raise ValidationError("'phases' must be a non-empty list")

    phases: list[PhaseSpec] = []
    for pi, phase_data in enumerate(phases_raw):
        if not isinstance(phase_data, dict):
            raise ValidationError(f"Phase {pi} must be an object")

        phase_name = phase_data.get("name")
        if not phase_name or not isinstance(phase_name, str):
            raise ValidationError(f"Phase {pi}: missing or invalid 'name'")

        runs_raw = phase_data.get("runs")
        if not isinstance(runs_raw, list) or not runs_raw:
            raise ValidationError(f"Phase '{phase_name}': 'runs' must be a non-empty list")

        runs: list[RunSpec] = []
        for ri, run_data in enumerate(runs_raw):
            runs.append(
                validate_run(
                    run_data,
                    phase_name,
                    ri,
                    min_estimate_hours=min_estimate_hours,
                    max_estimate_hours=max_estimate_hours,
                )
            )

        phases.append(PhaseSpec(name=phase_name, runs=runs))

    return SagaStructure(name=name, phases=phases)


def validate_run(
    data: object,
    phase_name: str,
    index: int,
    *,
    min_estimate_hours: float = 2.0,
    max_estimate_hours: float = 8.0,
) -> RunSpec:
    """Validate a single run dict and return a RunSpec."""
    prefix = f"Phase '{phase_name}', run {index}"

    if not isinstance(data, dict):
        raise ValidationError(f"{prefix}: must be an object")

    run_name = data.get("name")
    if not run_name or not isinstance(run_name, str):
        raise ValidationError(f"{prefix}: missing or invalid 'name'")

    description = data.get("description")
    if not description or not isinstance(description, str):
        raise ValidationError(f"{prefix}: missing or invalid 'description'")

    criteria = data.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValidationError(f"{prefix}: 'acceptance_criteria' must be a non-empty list")
    for ci, c in enumerate(criteria):
        if not isinstance(c, str) or not c.strip():
            raise ValidationError(f"{prefix}: acceptance_criteria[{ci}] must be a non-empty string")

    files = data.get("declared_files", [])
    if not isinstance(files, list):
        files = []
    files = [f for f in files if isinstance(f, str) and f.strip()]

    estimate = data.get("estimate_hours", min_estimate_hours)
    if not isinstance(estimate, (int, float)):
        estimate = min_estimate_hours
    estimate = max(0.5, min(float(estimate), 40.0))

    confidence = data.get("confidence", 0.5)
    if not isinstance(confidence, (int, float)):
        confidence = 0.5
    confidence = max(0.0, min(1.0, float(confidence)))

    return RunSpec(
        name=run_name,
        description=description,
        acceptance_criteria=criteria,
        declared_files=files,
        estimate_hours=estimate,
        confidence=confidence,
    )


_JSON_BLOCK_RE = re.compile(r"```(?:json)?[ \t]*\n([^`]*(?:`(?!``)[^`]*)*)```")


def try_extract_structure(text: str) -> SagaStructure | None:
    """Scan freeform text for a JSON block matching the SagaStructure schema.

    Returns the first valid SagaStructure found, or None if no match.
    """
    candidates: list[str] = []

    for match in _JSON_BLOCK_RE.finditer(text):
        candidates.append(match.group(1).strip())

    if not candidates:
        # Try parsing the whole text as JSON (no fences)
        try:
            data = json.loads(text.strip())
            if isinstance(data, dict) and "phases" in data:
                candidates.append(text.strip())
        except (json.JSONDecodeError, ValueError):
            pass

    for candidate in candidates:
        try:
            return parse_and_validate(candidate)
        except (ValidationError, json.JSONDecodeError, ValueError, KeyError):
            continue

    return None
