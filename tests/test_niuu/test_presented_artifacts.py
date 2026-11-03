"""Reading keyed presented files (artifact versions) out of a present_file turn."""

from __future__ import annotations

from niuu.domain.presented_files import (
    presented_artifact_provider_key,
    presented_artifacts,
    presented_version_label,
)

FILE_ID = "pf_" + "a" * 32


def _part(**tool_input) -> dict:
    return {"type": "tool_use", "name": "present_file", "id": FILE_ID, "input": tool_input}


def test_only_well_formed_keyed_present_file_parts_are_versions() -> None:
    turn = {
        "parts": [
            _part(file_id=FILE_ID, artifact_key="plan", version_label="v2", name=" plan.md "),
            _part(file_id=FILE_ID),  # unkeyed: a file of its own
            _part(file_id="pf_short", artifact_key="plan"),
            _part(file_id=FILE_ID, artifact_key="../plan"),
            {"type": "tool_use", "name": "notify", "input": {"file_id": FILE_ID}},
            {"type": "text", "text": "hi"},
            "not a part",
            {"type": "tool_use", "name": "present_file", "input": "bad"},
        ]
    }
    [artifact] = presented_artifacts("s1", turn)
    assert artifact.provider_key == presented_artifact_provider_key("s1", "plan") == "s1:plan"
    assert (artifact.revision_id, artifact.name, artifact.version_label) == (
        FILE_ID,
        "plan.md",
        "v2",
    )
    assert (artifact.mime, artifact.url) == (None, None)
    assert presented_artifacts("s1", {}) == []


def test_version_labels_count_from_one() -> None:
    assert [presented_version_label(n) for n in (1, 2)] == ["v1", "v2"]
