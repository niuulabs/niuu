"""Tests for ``ravn resident`` — operator inspection of durable resident state.

These run against a real ``LocalResidentState`` on a tmp dir rather than a
mock: the thing worth proving is that the commands read the refs an actual
resident writes, and a fake store would let a ref-shape change pass silently.
``_open_state`` is the one seam, so the adapter is real while the daemon's
config-dependent wiring stays out of the way.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.cli import resident_commands as resident_mod
from ravn.cli.resident_commands import (
    _case_id_from_ref,
    _CaseSummary,
    _leaf_from_ref,
    _state_prefix,
    resident_app,
)
from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import (
    ResidentDecisionStreakRecord,
    ResidentScheduledWakeRecord,
    ResidentTurnRecord,
    ResidentWorkingStateRecord,
)

runner = CliRunner()

RESIDENT_ID = "regin"


def _turn(case_id: str, *, turn_index: int = 0, response: str = "watching") -> ResidentTurnRecord:
    return ResidentTurnRecord(
        turn_index=turn_index,
        prompt="check NIU-1118",
        response=response,
        outcome_fields={"decision": "watch"},
        tool_names=("mimir_search",),
        usage=TokenUsage(input_tokens=10, output_tokens=5),
        case_id=case_id,
    )


@pytest.fixture
def state_root(tmp_path: Path) -> Path:
    return tmp_path / "resident-state"


@pytest.fixture
def state(state_root: Path) -> LocalResidentState:
    return LocalResidentState(state_root, environment_id="demo-env")


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, state: LocalResidentState) -> LocalResidentState:
    """Point the commands at the real adapter, skipping daemon config wiring."""

    class _ResidentStateSettings:
        repeated_decision_escalate_after = 5

    class _Environment:
        id = "demo-env"

    class _Settings:
        resident_state = _ResidentStateSettings()
        environment = _Environment()

    async def _open(_settings: object) -> tuple[LocalResidentState, str]:
        return state, RESIDENT_ID

    monkeypatch.setattr(resident_mod, "_load_settings", lambda _config: _Settings())
    monkeypatch.setattr(resident_mod, "_open_state", _open)
    return state


@pytest.fixture
def populated(wired: LocalResidentState) -> LocalResidentState:
    """One resumable-by-wake case, one blocked-on-question case, one inert.

    Seeded synchronously: the commands under test call ``asyncio.run``
    themselves, so an ambient loop here would make every invocation fail.
    """

    async def _seed() -> None:
        await wired.write_turn(_turn("watch-case"))
        await wired.write_scheduled_wake(
            ResidentScheduledWakeRecord(
                case_id="watch-case",
                root_correlation_id="corr-1",
                wake_at=datetime.now(UTC) + timedelta(minutes=30),
                reason="waiting for research campaign findings",
            )
        )
        await wired.write_operator_needed(
            question="Did the campaign actually launch?",
            reason="no artifacts after 30 attempts",
            turn=_turn("escalation-case", turn_index=1),
            case_id="escalation-case",
        )
        await wired.write_turn(_turn("inert-case", response="done"))
        await wired.write_decision_streak(
            ResidentDecisionStreakRecord(
                resident_id=RESIDENT_ID,
                fingerprint="watch:research-findings",
                count=34,
                decision="watch",
                rationale="waiting for research campaign findings",
                case_id="watch-case",
            )
        )
        await wired.write_working_state(
            ResidentWorkingStateRecord(
                resident_id=RESIDENT_ID,
                state={"objectives": ["ship NIU-1118"]},
                source_turn_ref="",
                source_case_id="watch-case",
                source_task_id="",
            )
        )

    asyncio.run(_seed())
    return wired


# ---------------------------------------------------------------------------
# ref parsing
# ---------------------------------------------------------------------------


def test_case_id_is_read_from_a_prefixed_ref() -> None:
    ref = "resident/continuation/cases/watch-case/scheduled-wake/latest.md"
    assert _case_id_from_ref(ref, prefix="resident/continuation") == "watch-case"


def test_refs_outside_the_prefix_are_not_cases() -> None:
    ref = "somewhere/else/cases/watch-case/turns/0.md"
    assert _case_id_from_ref(ref, prefix="resident/continuation") == ""


def test_non_case_refs_under_the_prefix_are_not_cases() -> None:
    ref = "resident/continuation/working-state/regin.md"
    assert _case_id_from_ref(ref, prefix="resident/continuation") == ""


def test_a_bare_case_dir_without_a_leaf_is_not_a_case() -> None:
    ref = "resident/continuation/cases/watch-case"
    assert _case_id_from_ref(ref, prefix="resident/continuation") == ""


def test_windows_separators_resolve_to_the_same_case() -> None:
    ref = "resident\\continuation\\cases\\watch-case\\turns\\0.md"
    assert _case_id_from_ref(ref, prefix="resident/continuation") == "watch-case"


def test_state_prefix_normalises_a_path_valued_prefix(state: LocalResidentState) -> None:
    """The local store keeps a Path here and the Mimir one a str."""
    assert isinstance(_state_prefix(state), str)
    assert _state_prefix(state) == "resident/continuation"


def test_state_prefix_falls_back_when_the_adapter_exposes_none() -> None:
    assert _state_prefix(object()) == "resident/continuation"


def test_leaf_is_the_part_below_the_case_directory() -> None:
    ref = "resident/continuation/cases/watch-case/scheduled-wake/latest.md"
    assert _leaf_from_ref(ref, "watch-case") == "scheduled-wake/latest.md"


def test_leaf_falls_back_to_the_whole_ref_when_the_case_is_absent() -> None:
    ref = "resident/continuation/working-state/regin.md"
    assert _leaf_from_ref(ref, "watch-case") == ref


# ---------------------------------------------------------------------------
# case summaries
# ---------------------------------------------------------------------------


def test_a_case_with_neither_wake_nor_question_is_inert() -> None:
    case = _CaseSummary(
        case_id="inert-case", refs=(), has_pending_wake=False, has_pending_question=False
    )
    assert case.resumable is False
    assert case.resume_reason == ""


def test_a_case_names_both_of_its_resume_paths() -> None:
    case = _CaseSummary(case_id="both", refs=(), has_pending_wake=True, has_pending_question=True)
    assert case.resumable is True
    assert case.resume_reason == "scheduled wake + operator question"


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_reports_the_streak_as_stuck_past_the_threshold(
    populated: LocalResidentState,
) -> None:
    result = runner.invoke(resident_app, ["status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["resident"] == RESIDENT_ID
    assert payload["adapter"] == "LocalResidentState"
    assert payload["decisionStreak"]["count"] == 34
    assert payload["decisionStreak"]["stuck"] is True
    assert payload["decisionStreak"]["decision"] == "watch"


def test_status_takes_case_counts_from_the_store_itself(
    populated: LocalResidentState,
) -> None:
    """These must match ravn.resident.cases.live/.total, not re-derive them."""
    result = runner.invoke(resident_app, ["status", "--json"])

    payload = json.loads(result.stdout)
    assert payload["cases"] == {"total": 3, "live": 2, "inert": 1, "countedBy": "store"}
    assert len(payload["pendingQuestions"]) == 1
    assert len(payload["scheduledWakes"]) == 1


def test_status_agrees_with_the_stores_own_count(populated: LocalResidentState) -> None:
    live, total = asyncio.run(populated.count_cases())

    payload = json.loads(runner.invoke(resident_app, ["status", "--json"]).stdout)

    assert (payload["cases"]["live"], payload["cases"]["total"]) == (live, total)


def test_status_walks_the_refs_when_the_store_declines_to_count(
    populated: LocalResidentState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Mimir adapter returns None rather than pay a remote walk per refresh."""

    async def _declines() -> None:
        return None

    monkeypatch.setattr(populated, "count_cases", _declines)

    payload = json.loads(runner.invoke(resident_app, ["status", "--json"]).stdout)

    assert payload["cases"] == {"total": 3, "live": 2, "inert": 1, "countedBy": "walk"}


def test_status_names_where_the_counts_came_from(populated: LocalResidentState) -> None:
    result = runner.invoke(resident_app, ["status"])

    assert "cases:       3 (2 live, 1 inert) [counted by store]" in result.stdout


def test_status_marks_a_short_streak_as_not_stuck(
    wired: LocalResidentState,
) -> None:
    asyncio.run(
        wired.write_decision_streak(
            ResidentDecisionStreakRecord(
                resident_id=RESIDENT_ID, fingerprint="watch:x", count=2, decision="watch"
            )
        )
    )

    result = runner.invoke(resident_app, ["status", "--json"])

    assert json.loads(result.stdout)["decisionStreak"]["stuck"] is False


def test_status_renders_an_empty_store_without_failing(
    wired: LocalResidentState,
) -> None:
    result = runner.invoke(resident_app, ["status"])

    assert result.exit_code == 0
    assert "streak:      none recorded" in result.stdout
    assert "working state: none written yet" in result.stdout


def test_status_flags_the_stuck_streak_in_the_human_rendering(
    populated: LocalResidentState,
) -> None:
    result = runner.invoke(resident_app, ["status"])

    assert "** STUCK **" in result.stdout
    assert "waiting for research campaign findings" in result.stdout


# ---------------------------------------------------------------------------
# cases / case
# ---------------------------------------------------------------------------


def test_cases_says_what_resumes_each_case(populated: LocalResidentState) -> None:
    result = runner.invoke(resident_app, ["cases", "--json"])

    payload = {case["caseId"]: case for case in json.loads(result.stdout)}
    assert payload["watch-case"]["resumeReason"] == "scheduled wake"
    assert payload["escalation-case"]["resumeReason"] == "operator question"
    assert payload["inert-case"]["resumable"] is False


def test_cases_filters_to_resumable(populated: LocalResidentState) -> None:
    result = runner.invoke(resident_app, ["cases", "--resumable", "--json"])

    ids = {case["caseId"] for case in json.loads(result.stdout)}
    assert ids == {"watch-case", "escalation-case"}


def test_cases_filters_to_inert(populated: LocalResidentState) -> None:
    result = runner.invoke(resident_app, ["cases", "--inert", "--json"])

    ids = {case["caseId"] for case in json.loads(result.stdout)}
    assert ids == {"inert-case"}


def test_cases_refuses_contradictory_filters(populated: LocalResidentState) -> None:
    result = runner.invoke(resident_app, ["cases", "--resumable", "--inert"])

    assert result.exit_code != 0


def test_cases_reports_an_empty_store(wired: LocalResidentState) -> None:
    result = runner.invoke(resident_app, ["cases"])

    assert result.exit_code == 0
    assert "No cases found." in result.stdout


def test_cases_labels_each_case_in_the_human_rendering(
    populated: LocalResidentState,
) -> None:
    result = runner.invoke(resident_app, ["cases"])

    assert "watch-case  [resumable: scheduled wake]" in result.stdout
    assert "escalation-case  [resumable: operator question]" in result.stdout
    assert "inert-case  [inert]" in result.stdout


def test_case_lists_every_ref_it_holds(populated: LocalResidentState) -> None:
    result = runner.invoke(resident_app, ["case", "watch-case", "--json"])

    payload = json.loads(result.stdout)
    assert payload["caseId"] == "watch-case"
    assert payload["resumable"] is True
    leaves = {ref["leaf"] for ref in payload["refs"]}
    assert "scheduled-wake/latest.md" in leaves


def test_case_omits_content_unless_asked(populated: LocalResidentState) -> None:
    result = runner.invoke(resident_app, ["case", "watch-case", "--json"])

    assert all(ref["content"] == "" for ref in json.loads(result.stdout)["refs"])


def test_case_includes_content_on_request(populated: LocalResidentState) -> None:
    result = runner.invoke(resident_app, ["case", "watch-case", "--content", "--json"])

    contents = [ref["content"] for ref in json.loads(result.stdout)["refs"]]
    assert any("waiting for research campaign findings" in text for text in contents)


def test_case_exits_nonzero_when_the_case_is_unknown(
    populated: LocalResidentState,
) -> None:
    result = runner.invoke(resident_app, ["case", "no-such-case"])

    assert result.exit_code == 1


def test_case_renders_leaves_and_content_for_a_human(
    populated: LocalResidentState,
) -> None:
    result = runner.invoke(resident_app, ["case", "watch-case", "--content"])

    assert "resumable: True (scheduled wake)" in result.stdout
    assert "scheduled-wake/latest.md" in result.stdout
    assert "waiting for research campaign findings" in result.stdout


def test_case_says_when_nothing_can_resume_it(populated: LocalResidentState) -> None:
    result = runner.invoke(resident_app, ["case", "inert-case"])

    assert "resumable: False (nothing can resume it)" in result.stdout


# ---------------------------------------------------------------------------
# streak / questions / wakes / working-state
# ---------------------------------------------------------------------------


def test_streak_reports_the_fingerprint_and_threshold(
    populated: LocalResidentState,
) -> None:
    result = runner.invoke(resident_app, ["streak", "--json"])

    payload = json.loads(result.stdout)
    assert payload["fingerprint"] == "watch:research-findings"
    assert payload["escalateAfter"] == 5
    assert payload["caseId"] == "watch-case"


def test_streak_says_so_when_none_is_recorded(wired: LocalResidentState) -> None:
    result = runner.invoke(resident_app, ["streak"])

    assert result.exit_code == 0
    assert "No decision streak recorded." in result.stdout


def test_streak_renders_the_full_record_for_a_human(
    populated: LocalResidentState,
) -> None:
    result = runner.invoke(resident_app, ["streak"])

    assert "count:       34 (escalates at 5)" in result.stdout
    assert "stuck:       True" in result.stdout
    assert "fingerprint: watch:research-findings" in result.stdout


def test_questions_lists_what_the_resident_is_blocked_on(
    populated: LocalResidentState,
) -> None:
    result = runner.invoke(resident_app, ["questions", "--json"])

    refs = [entry["ref"] for entry in json.loads(result.stdout)]
    assert any("escalation-case/operator-needed" in ref for ref in refs)


def test_questions_can_show_unconsumed_answers(
    populated: LocalResidentState,
) -> None:
    asyncio.run(populated.write_operator_answer("yes, it launched", case_id="escalation-case"))

    result = runner.invoke(resident_app, ["questions", "--answers", "--json"])

    refs = [entry["ref"] for entry in json.loads(result.stdout)]
    assert any("escalation-case/operator-answers" in ref for ref in refs)


def test_questions_reports_none_pending(wired: LocalResidentState) -> None:
    result = runner.invoke(resident_app, ["questions"])

    assert "No pending questions." in result.stdout


def test_wakes_lists_scheduled_wakes(populated: LocalResidentState) -> None:
    result = runner.invoke(resident_app, ["wakes", "--json"])

    refs = [entry["ref"] for entry in json.loads(result.stdout)]
    assert any("watch-case/scheduled-wake" in ref for ref in refs)


def test_wakes_reports_none_scheduled(wired: LocalResidentState) -> None:
    result = runner.invoke(resident_app, ["wakes"])

    assert "No scheduled wakes." in result.stdout


def test_working_state_prints_what_the_resident_believes(
    populated: LocalResidentState,
) -> None:
    result = runner.invoke(resident_app, ["working-state"])

    assert result.exit_code == 0
    assert "ship NIU-1118" in result.stdout


def test_working_state_says_so_when_none_is_written(
    wired: LocalResidentState,
) -> None:
    result = runner.invoke(resident_app, ["working-state"])

    assert "No working state written yet." in result.stdout


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------


def test_load_settings_honours_an_explicit_config_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "ravn.yaml"
    config.write_text("environment:\n  id: from-file\n", encoding="utf-8")
    monkeypatch.delenv("RAVN_CONFIG", raising=False)

    settings = resident_mod._load_settings(str(config))

    assert settings.environment.id == "from-file"


@pytest.mark.asyncio
async def test_open_state_builds_the_configured_adapter_and_resident_id(
    monkeypatch: pytest.MonkeyPatch, state: LocalResidentState, tmp_path: Path
) -> None:
    """The daemon's builders are reused so the two cannot read different stores."""
    from ravn.cli import commands as commands_mod

    monkeypatch.setattr(commands_mod, "_resolve_workspace", lambda _settings: tmp_path)
    monkeypatch.setattr(commands_mod, "_build_mimir", lambda _settings: None)

    async def _build(_settings: object, *, workspace: Path, mimir: object) -> LocalResidentState:
        assert workspace == tmp_path
        return state

    monkeypatch.setattr(commands_mod, "_build_resident_state", _build)

    class _Mesh:
        own_peer_id = ""

    class _Environment:
        resident_name = "regin"

    class _Initiative:
        default_persona = "product-steward"

    class _Settings:
        mesh = _Mesh()
        environment = _Environment()
        initiative = _Initiative()

    built, resident_id = await resident_mod._open_state(_Settings())

    assert built is state
    assert resident_id == "regin"


@pytest.mark.asyncio
async def test_open_state_prefers_the_mesh_peer_id_for_the_resident_id(
    monkeypatch: pytest.MonkeyPatch, state: LocalResidentState, tmp_path: Path
) -> None:
    from ravn.cli import commands as commands_mod

    monkeypatch.setattr(commands_mod, "_resolve_workspace", lambda _settings: tmp_path)
    monkeypatch.setattr(commands_mod, "_build_mimir", lambda _settings: None)

    async def _build(_settings: object, *, workspace: Path, mimir: object) -> LocalResidentState:
        return state

    monkeypatch.setattr(commands_mod, "_build_resident_state", _build)

    class _Mesh:
        own_peer_id = "peer-regin"

    class _Environment:
        resident_name = "regin"

    class _Initiative:
        default_persona = ""

    class _Settings:
        mesh = _Mesh()
        environment = _Environment()
        initiative = _Initiative()

    _built, resident_id = await resident_mod._open_state(_Settings())

    assert resident_id == "peer-regin"
