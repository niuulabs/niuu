from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ravn.adapters.resident_state.gbrain import GBrainResidentStateAdapter
from ravn.adapters.resident_state.mimir import LocalResidentState, MimirResidentState
from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import (
    ResidentMemoryEntry,
    ResidentPolicyObservation,
    ResidentScheduledWakeRecord,
    ResidentTurnRecord,
    ResidentWorkingStateRecord,
)
from ravn.resident_continuation import _scheduled_wake_at


class RecordingGBrainResidentState(GBrainResidentStateAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mcp_calls: list[tuple[str, dict]] = []
        self.ingests: list[tuple[str, str]] = []

    async def _call_mcp_tool(self, name: str, arguments: dict):
        self.mcp_calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "[]"}]}

    async def _ingest_gbrain(self, slug: str, content: str) -> None:
        self.ingests.append((slug, content))


@pytest.mark.asyncio
async def test_local_resident_state_is_single_memory_boundary(tmp_path):
    state = LocalResidentState(tmp_path)

    turn_ref = await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            prompt="inspect resident state",
            response="resident state recorded",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
    )

    assert turn_ref.startswith("resident/continuation/turns/")
    assert (tmp_path / turn_ref).exists()
    assert turn_ref in await state.list_refs()


@pytest.mark.asyncio
async def test_local_resident_state_rejects_refs_outside_root(tmp_path):
    root = tmp_path / "resident-state"
    state = LocalResidentState(root)
    await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            prompt="inspect resident state",
            response="resident state recorded",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.md").write_text("not resident state", encoding="utf-8")

    assert await state.list_refs("../outside") == []
    assert await state.list_refs(str(outside)) == []
    assert len(await state.list_refs()) == 1


@pytest.mark.asyncio
async def test_mimir_resident_state_is_single_memory_boundary(tmp_path):
    from mimir.adapters.markdown import MarkdownMimirAdapter

    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    state = MimirResidentState(mimir)

    turn_ref = await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            prompt="inspect resident state",
            response="resident state recorded",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
    )

    assert turn_ref.startswith("resident/continuation/turns/")
    assert "resident state recorded" in await mimir.read_page(turn_ref)
    assert turn_ref in await state.list_refs()

    working_ref = await state.write_working_state(
        ResidentWorkingStateRecord(
            resident_id="resident-alpha",
            state={"unknowns": ["whether the source is reachable"]},
            source_turn_ref=turn_ref,
            source_case_id="case-1",
            source_task_id="task-1",
        )
    )
    working = await state.read_working_state("resident-alpha")
    assert working_ref == "resident/continuation/working-state/resident-alpha.md"
    assert working is not None
    assert "whether the source is reachable" in working.content


@pytest.mark.asyncio
async def test_gbrain_resident_state_prefers_synchronous_put_page_with_mcp(tmp_path):
    state = RecordingGBrainResidentState(
        tmp_path,
        mcp_url="http://127.0.0.1:3131/mcp",
        ingest_url="http://127.0.0.1:3131/ingest",
        api_token="token",
    )

    await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            prompt="inspect resident state",
            response="resident state recorded",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
    )

    assert [name for name, _arguments in state.mcp_calls] == ["put_page"]
    assert state.ingests == []
    name, arguments = state.mcp_calls[0]
    assert name == "put_page"
    assert arguments["slug"].startswith("resident/continuation/turns/")
    projected_name = arguments["slug"].rsplit("/", 1)[-1]
    assert projected_name.startswith("t")
    assert "T" not in projected_name
    assert "Z" not in projected_name
    assert "Ravn resident memory" in arguments["content"]


@pytest.mark.asyncio
async def test_gbrain_resident_state_can_use_explicit_ingest_mode(tmp_path):
    state = RecordingGBrainResidentState(
        tmp_path,
        mcp_url="http://127.0.0.1:3131/mcp",
        ingest_url="http://127.0.0.1:3131/ingest",
        api_token="token",
        write_mode="ingest",
    )

    await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            prompt="inspect resident state",
            response="resident state recorded",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
    )

    assert state.mcp_calls == []
    assert len(state.ingests) == 1
    assert state.ingests[0][0].startswith("resident/continuation/turns/")


@pytest.mark.asyncio
async def test_gbrain_resident_state_projects_operator_feedback(tmp_path):
    state = RecordingGBrainResidentState(
        tmp_path,
        mcp_url="http://127.0.0.1:3131/mcp",
        api_token="token",
    )

    answer_ref = await state.write_operator_answer(
        "Not approved. Investigate Kanuck Valley Models online instead."
    )
    policy_ref = await state.write_policy_observation(
        ResidentPolicyObservation(
            subject="operator-contact:approval",
            observation="Not approved. Investigate Kanuck Valley Models online instead.",
            source="operator_answer",
            status="candidate",
        )
    )

    slugs = [arguments["slug"] for _name, arguments in state.mcp_calls]
    assert answer_ref == "resident/continuation/operator-answers/latest.md"
    assert policy_ref == "resident/continuation/policy/operator-contact-approval.md"
    assert "resident/continuation/operator-answers/latest" in slugs
    assert "resident/continuation/policy/operator-contact-approval" in slugs


@pytest.mark.asyncio
async def test_gbrain_availability_gates_selection(tmp_path) -> None:
    from ravn.adapters.resident_state import select_resident_state

    fallback = LocalResidentState(tmp_path / "fallback")

    # No remote configured and a command that is not on PATH -> unavailable.
    absent = GBrainResidentStateAdapter(tmp_path / "g1", command="gbrain-not-installed-xyz")
    assert await absent.available() is False
    assert await select_resident_state(absent, fallback) is fallback

    # A configured remote brain -> available and preferred.
    present = GBrainResidentStateAdapter(
        tmp_path / "g2", mcp_url="https://brain.example", api_token="tok"
    )
    assert await present.available() is True
    assert await select_resident_state(present, fallback) is present
    assert await fallback.available() is True


@pytest.mark.parametrize("adapter", ["local", "mimir"])
@pytest.mark.asyncio
async def test_scheduled_wakes_round_trip_through_either_store(tmp_path, adapter):
    """Both stores must persist, list, and retire a wake identically.

    The resident's wake source cannot depend on which memory backend a
    deployment happens to have configured.
    """
    from mimir.adapters.markdown import MarkdownMimirAdapter

    if adapter == "local":
        state = LocalResidentState(tmp_path)
    else:
        state = MimirResidentState(MarkdownMimirAdapter(root=tmp_path / "mimir"))

    wake_at = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    ref = await state.write_scheduled_wake(
        ResidentScheduledWakeRecord(
            case_id="case-filament",
            root_correlation_id="root-filament",
            wake_at=wake_at,
            reason="re-check filament stock",
            mandate="steward the workshop",
            turn_index=2,
        )
    )

    assert ref
    pending = await state.list_scheduled_wakes()
    assert len(pending) == 1
    assert _scheduled_wake_at(pending[0].content) == wake_at
    assert "re-check filament stock" in pending[0].content

    consumed_ref = await state.consume_scheduled_wake(pending[0])

    assert consumed_ref == pending[0].path
    # A consumed wake must never be listed again, or the case re-fires forever.
    assert await state.list_scheduled_wakes() == []


@pytest.mark.asyncio
async def test_consuming_a_wake_whose_page_vanished_still_retires_it(tmp_path):
    """A wake page deleted out from under the runtime must still be retired."""
    from mimir.adapters.markdown import MarkdownMimirAdapter

    state = MimirResidentState(MarkdownMimirAdapter(root=tmp_path / "mimir"))
    missing = ResidentMemoryEntry(
        path="resident/continuation/cases/case-gone/scheduled-wake/latest.md",
        summary="Resident Scheduled Wake",
        content="# Resident Scheduled Wake\n\n- status: pending\n- case_id: case-gone\n",
    )

    assert await state.consume_scheduled_wake(missing) == missing.path
    assert await state.list_scheduled_wakes() == []
