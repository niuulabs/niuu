from __future__ import annotations

from pathlib import Path

from scripts.audit_resident_vision_proof import (
    _LAYER_TITLES,
    _REQUIREMENTS,
    LayerAudit,
    _audit_layer,
)


def test_audit_layer_reports_proved_when_required_artifacts_exist(tmp_path: Path) -> None:
    result_file = (
        tmp_path
        / "wiki"
        / "resident"
        / "delegation-results"
        / "delegation-proof-session.md"
    )
    result_file.parent.mkdir(parents=True)
    result_file.write_text(
        "# Delegated Result\n\n- backend_name: workflow\n",
        encoding="utf-8",
    )
    (tmp_path / "wiki" / "resident" / "delegations").mkdir()
    (tmp_path / "wiki" / "resident" / "delegations" / "delegation-proof.md").write_text(
        "# Delegation\n",
        encoding="utf-8",
    )
    (tmp_path / "wiki" / "resident" / "delegation-reviews").mkdir()
    (
        tmp_path
        / "wiki"
        / "resident"
        / "delegation-reviews"
        / "review-delegation-proof.md"
    ).write_text("# Review\n", encoding="utf-8")

    result = _audit_layer(
        LayerAudit(
            key="delegation",
            title=_LAYER_TITLES["delegation"],
            root=tmp_path,
            requirements=_REQUIREMENTS["delegation"],
        )
    )

    assert result.status == "proved"
    assert all(item.status == "proved" for item in result.requirements)


def test_audit_layer_reports_missing_required_content(tmp_path: Path) -> None:
    answer = (
        tmp_path
        / "resident"
        / "continuation"
        / "operator-answers"
        / "latest.md"
    )
    answer.parent.mkdir(parents=True)
    answer.write_text("# Operator Answer\n\n- status: pending\n", encoding="utf-8")
    marker = (
        tmp_path
        / "resident"
        / "continuation"
        / "operator-needed"
        / "latest.md"
    )
    marker.parent.mkdir(parents=True)
    marker.write_text("# Operator Needed\n", encoding="utf-8")

    result = _audit_layer(
        LayerAudit(
            key="operator",
            title=_LAYER_TITLES["operator"],
            root=tmp_path,
            requirements=_REQUIREMENTS["operator"],
        )
    )

    assert result.status == "weak"
    answer_result = next(
        item for item in result.requirements if item.label == "consumed operator answer"
    )
    assert answer_result.status == "missing"
    assert "status: consumed" in answer_result.missing_contains
    assert "consumed_at:" in answer_result.missing_contains
