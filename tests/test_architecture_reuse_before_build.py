from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BOUNDARY_DIRS = {"ports", "adapters", "transports", "executors", "runners"}
DOC_NOTE_FILENAMES = {"readme.md", "architecture.md"}


def test_new_boundary_files_have_reuse_before_build_note() -> None:
    base = _diff_base()
    if not base:
        pytest.skip("no git base available for Reuse Before Build guardrail")

    changed = _git_lines("diff", "--name-status", "--diff-filter=AM", f"{base}...HEAD")
    added_boundaries = [
        path
        for status, path in map(_parse_name_status, changed)
        if status == "A" and _is_boundary_path(path)
    ]
    if not added_boundaries:
        return

    docs = [
        path
        for status, path in map(_parse_name_status, changed)
        if status in {"A", "M"} and _is_reuse_note_path(path)
    ]
    if any(_contains_reuse_note(path) for path in docs):
        return

    formatted = "\n".join(f"- {path}" for path in added_boundaries)
    pytest.fail(
        "New boundary files require a PR-visible 'Reuse Before Build' note.\n"
        "Add or update docs with existing primitives inspected, why they were "
        "insufficient, why the new boundary differs, why it is not parallel, "
        "and how it composes with the architecture.\n"
        f"Boundary files:\n{formatted}"
    )


def _diff_base() -> str:
    candidates: list[str] = []
    if os.environ.get("ARCHITECTURE_BASE_REF"):
        candidates.append(os.environ["ARCHITECTURE_BASE_REF"])
    if os.environ.get("GITHUB_BASE_REF"):
        base_ref = os.environ["GITHUB_BASE_REF"]
        candidates.extend([f"origin/{base_ref}", base_ref])
    candidates.extend(["origin/main", "origin/dev", "HEAD~1"])

    for candidate in candidates:
        if _git_ok("diff", "--quiet", f"{candidate}...HEAD"):
            return candidate
        if _git_ok("diff", "--name-only", f"{candidate}...HEAD"):
            return candidate
    return ""


def _git_ok(*args: str) -> bool:
    result = subprocess.run(
        ["git", *args],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.returncode in {0, 1}


def _git_lines(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _parse_name_status(line: str) -> tuple[str, str]:
    parts = line.split("\t")
    return parts[0], parts[-1]


def _is_boundary_path(path: str) -> bool:
    parts = Path(path).parts
    return bool(parts) and parts[0] == "src" and bool(BOUNDARY_DIRS.intersection(parts))


def _is_reuse_note_path(path: str) -> bool:
    candidate = Path(path)
    lower_name = candidate.name.lower()
    return (
        candidate.suffix.lower() == ".md"
        and (
            path.startswith("docs/")
            or path.startswith(".claude/rules/")
            or lower_name in DOC_NOTE_FILENAMES
        )
    )


def _contains_reuse_note(path: str) -> bool:
    candidate = Path(path)
    return candidate.exists() and "Reuse Before Build" in candidate.read_text(encoding="utf-8")
