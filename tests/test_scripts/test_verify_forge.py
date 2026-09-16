"""The stability gate must distinguish execution from skipped/empty success."""

import pytest

from scripts.verify_forge import command_for, inspect_junit


@pytest.mark.parametrize(
    "body",
    ["", '<testcase name="missing"><skipped/></testcase>', "<testcase><failure/></testcase>"],
)
def test_empty_skipped_or_failed_suite_cannot_pass(tmp_path, body):
    report = tmp_path / "junit.xml"
    report.write_text(f"<testsuite>{body}</testsuite>")
    with pytest.raises(ValueError):
        inspect_junit(report)


def test_required_lane_rejects_even_one_unexpected_skip(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuite><testcase/><testcase name="missing"><skipped/></testcase></testsuite>'
    )
    with pytest.raises(ValueError, match="did not execute"):
        inspect_junit(report, strict_skips=True)


@pytest.mark.parametrize("skip_type", ["pytest.xfail", "pytest.skip"])
def test_required_lane_rejects_skipping_the_former_multiselect_gap(tmp_path, skip_type):
    classname = "tests.test_skuld.test_forge_questions"
    name = "test_e4_multiselect_maps_selection_then_submit"
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuite><testcase name="ran"/>'
        f'<testcase classname="{classname}" name="{name}">'
        f'<skipped type="{skip_type}"/></testcase></testsuite>'
    )
    with pytest.raises(ValueError, match="did not execute"):
        inspect_junit(report, strict_skips=True)


def test_unit_gate_excludes_live_providers_without_dropping_fake_e2e(tmp_path):
    command = command_for("unit", tmp_path / "unit.xml")
    expression = command[command.index("-m", 3) + 1]
    assert "not live_cli" in expression
    assert "not e2e" not in expression


def test_tmux_gate_selects_actual_tmux_tests(tmp_path):
    command = command_for("tmux", tmp_path / "tmux.xml")
    assert command[command.index("-m", 3) + 1] == "tmux"
