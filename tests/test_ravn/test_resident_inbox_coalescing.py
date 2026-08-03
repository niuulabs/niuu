"""The archive is complete; the queue is bounded.

These tests pin the properties the coalescing design depends on: ingestion cost
does not grow with history, the queue is bounded by structure rather than
volume, nothing the resident has not seen is acknowledged, and no path can
delete raw evidence.
"""

from __future__ import annotations

import time

import pytest

from ravn.resident_inbox import (
    LocalResidentInbox,
    ResidentInboxSignal,
    ResidentInboxStatus,
    ShapeAggregate,
    field_paths,
    shape_key,
)
from ravn.resident_inbox import aggregate_summary_lines as _summary_lines
from ravn.resident_inbox.shape import fold_aggregate


def _tick(
    index: int,
    *,
    progress: float = 0.0,
    temperature: float = 40.0,
    **extra,
) -> ResidentInboxSignal:
    """One machine status observation: same shape, moving values."""
    payload = {
        "task": "print-7",
        "state": "printing",
        "progress": progress,
        "temperature": temperature,
        **extra,
    }
    return ResidentInboxSignal(
        id=f"tick-{index}",
        source="laevateinn",
        kind="workshop.status",
        summary=f"print-7 at {progress}%",
        payload=payload,
        observed_at=f"2026-08-03T10:{index:02d}:00Z",
    )


# ---------------------------------------------------------------------------
# Shape derivation
# ---------------------------------------------------------------------------


def test_shape_key_ignores_values_but_not_structure() -> None:
    same = shape_key(source="s", kind="k", payload={"a": 1, "b": "x"})
    other_values = shape_key(source="s", kind="k", payload={"a": 999, "b": "y"})
    added_field = shape_key(source="s", kind="k", payload={"a": 1, "b": "x", "error": "boom"})
    retyped = shape_key(source="s", kind="k", payload={"a": "1", "b": "x"})

    assert same == other_values
    assert added_field != same
    assert retyped != same


def test_field_paths_are_sorted_and_typed() -> None:
    paths = field_paths({"b": 1, "a": {"c": "x"}, "d": [1, 2]})
    assert paths == (("a.c", "str"), ("b", "int"), ("d[]", "int"))


def test_bool_is_categorical_not_numeric() -> None:
    aggregate = fold_aggregate(
        ShapeAggregate(), {"flag": True}, max_distinct_values=8, max_extreme_payloads=4
    )
    aggregate = fold_aggregate(
        aggregate, {"flag": False}, max_distinct_values=8, max_extreme_payloads=4
    )
    assert "flag" not in aggregate.numeric
    assert set(aggregate.categorical["flag"]) == {"True", "False"}


def test_high_cardinality_paths_stop_growing() -> None:
    aggregate = ShapeAggregate()
    for index in range(10):
        aggregate = fold_aggregate(
            aggregate,
            {"job": f"job-{index}"},
            max_distinct_values=3,
            max_extreme_payloads=4,
        )
    assert "job" in aggregate.high_cardinality
    assert "job" not in aggregate.categorical


# ---------------------------------------------------------------------------
# Coalescing queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_is_bounded_by_shape_while_archive_keeps_everything(tmp_path) -> None:
    inbox = LocalResidentInbox(tmp_path / "inbox")

    for index in range(500):
        await inbox.write_signal(_tick(index, progress=index / 5.0, temperature=40 + index % 20))

    pending = await inbox.list_signals(limit=50)
    assert len(pending) == 1, "one shape must occupy exactly one queue slot"
    _ref, slot = pending[0]
    assert slot.observation_count == 500
    # Every observation survives, whatever the queue did.
    assert inbox.archive.count() == 500


@pytest.mark.asyncio
async def test_ingestion_cost_does_not_grow_with_history(tmp_path, monkeypatch) -> None:
    """The outage was an O(N) scan per write. Assert the bound, not the clock."""
    from ravn.resident_inbox import backend as backend_module

    inbox = LocalResidentInbox(tmp_path / "inbox", retention_sweep_interval_seconds=3600.0)
    for index in range(300):
        await inbox.write_signal(_tick(index, progress=index))

    parses = 0
    real_parse = backend_module.parse_inbox_signal

    def counting_parse(content):
        nonlocal parses
        parses += 1
        return real_parse(content)

    monkeypatch.setattr(backend_module, "parse_inbox_signal", counting_parse)
    await inbox.write_signal(_tick(301, progress=99))

    assert parses <= 1, f"one write parsed {parses} records; cost must not follow history"


@pytest.mark.asyncio
async def test_a_changed_shape_never_folds(tmp_path) -> None:
    inbox = LocalResidentInbox(tmp_path / "inbox")

    await inbox.write_signal(_tick(1, progress=10))
    await inbox.write_signal(_tick(2, progress=20))
    # A payload that gains a field is a different shape and must surface.
    await inbox.write_signal(_tick(3, progress=30, error="thermal runaway"))

    pending = await inbox.list_signals(limit=50)
    assert len(pending) == 2
    shapes = {slot.shape_key for _ref, slot in pending}
    assert len(shapes) == 2
    errored = [slot for _ref, slot in pending if "error" in slot.payload]
    assert errored and errored[0].observation_count == 1


@pytest.mark.asyncio
async def test_numeric_excursion_survives_in_the_aggregate(tmp_path) -> None:
    inbox = LocalResidentInbox(tmp_path / "inbox")

    for index in range(50):
        await inbox.write_signal(_tick(index, progress=index, temperature=40.0))
    await inbox.write_signal(_tick(51, progress=51, temperature=95.0))
    for index in range(52, 80):
        await inbox.write_signal(_tick(index, progress=index, temperature=40.0))

    _ref, slot = (await inbox.list_signals(limit=1))[0]
    low, high = slot.aggregate.numeric["temperature"]
    assert (low, high) == (40.0, 95.0)
    # The payload at the extreme is kept whole, not just its bound.
    assert slot.aggregate.extreme_payloads["temperature:max"]["temperature"] == 95.0


@pytest.mark.asyncio
async def test_directed_operator_messages_never_coalesce(tmp_path) -> None:
    inbox = LocalResidentInbox(tmp_path / "inbox")

    for index in range(3):
        await inbox.write_directed_message(
            content=f"operator instruction {index}",
            metadata={"message_id": str(index)},
        )

    pending = await inbox.list_signals(limit=10)
    assert len(pending) == 3, "each operator message keeps its own slot"
    assert all(slot.observation_count == 1 for _ref, slot in pending)


@pytest.mark.asyncio
async def test_pending_is_served_oldest_first(tmp_path) -> None:
    inbox = LocalResidentInbox(tmp_path / "inbox")

    await inbox.write_signal(_tick(1, progress=1))
    await inbox.write_signal(
        ResidentInboxSignal(
            id="later",
            source="other",
            kind="workshop.other",
            summary="later shape",
            payload={"different": "shape"},
            observed_at="2026-08-03T23:00:00Z",
        )
    )

    refs = [slot.first_observed_at for _ref, slot in await inbox.list_signals(limit=10)]
    assert refs == sorted(refs), "oldest waiting slot is served first"


# ---------------------------------------------------------------------------
# Acknowledgement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acknowledge_leaves_observations_that_arrived_mid_turn(tmp_path) -> None:
    inbox = LocalResidentInbox(tmp_path / "inbox")
    for index in range(3):
        await inbox.write_signal(_tick(index, progress=index))

    ref, seen = (await inbox.list_signals(limit=1))[0]
    assert seen.observation_count == 3

    # Two more arrive while the resident is judging what it saw.
    await inbox.write_signal(_tick(4, progress=40, temperature=41.0))
    await inbox.write_signal(_tick(5, progress=50, temperature=42.0))

    assert await inbox.acknowledge((ref,), expected={ref: seen.last_archive_ref}) == (ref,)

    still_pending = await inbox.list_signals(limit=10)
    assert len(still_pending) == 1
    _pending_ref, remainder = still_pending[0]
    assert remainder.observation_count == 2, "only the unseen observations remain"
    assert remainder.aggregate.numeric["temperature"] == (41.0, 42.0)

    judged = await inbox.list_signals(status=ResidentInboxStatus.REMEMBERED.value, limit=10)
    assert len(judged) == 1
    assert judged[0][1].observation_count == 3


@pytest.mark.asyncio
async def test_unrelated_traffic_cannot_truncate_the_unjudged_tail(tmp_path) -> None:
    """A burst of other shapes must not cause a slot's tail to be lost.

    The tail is rebuilt from the archive with a bound sized to the slot. If that
    bound counted unrelated records, a noisy neighbour would fill it and the
    slot's own unseen observations would be acknowledged without ever being
    shown to the resident.
    """
    inbox = LocalResidentInbox(tmp_path / "inbox")
    await inbox.write_signal(_tick(1, progress=1))
    ref, seen = (await inbox.list_signals(limit=1))[0]
    assert seen.observation_count == 1

    # Far more unrelated observations than this slot's own count, all landing
    # inside the range the rebuild has to scan.
    for index in range(50):
        await inbox.write_signal(
            ResidentInboxSignal(
                id=f"noise-{index}",
                source="noisy-neighbour",
                kind="other",
                summary="unrelated",
                payload={"noise": index},
                observed_at=f"2026-08-03T11:{index % 60:02d}:00Z",
            )
        )
    # Then two more of the original shape, which the resident has not seen.
    await inbox.write_signal(_tick(2, progress=2))
    await inbox.write_signal(_tick(3, progress=3))

    await inbox.acknowledge((ref,), expected={ref: seen.last_archive_ref})

    remaining = {slot.shape_key: slot for _r, slot in await inbox.list_signals(limit=50)}
    assert seen.shape_key in remaining, "the slot's unseen tail must survive"
    assert remaining[seen.shape_key].observation_count == 2


@pytest.mark.asyncio
async def test_acknowledge_without_new_arrivals_clears_the_slot(tmp_path) -> None:
    inbox = LocalResidentInbox(tmp_path / "inbox")
    for index in range(4):
        await inbox.write_signal(_tick(index, progress=index))

    ref, seen = (await inbox.list_signals(limit=1))[0]
    await inbox.acknowledge((ref,), expected={ref: seen.last_archive_ref})

    assert await inbox.list_signals(limit=10) == []
    assert inbox.archive.count() == 4


@pytest.mark.asyncio
async def test_double_acknowledge_is_a_no_op(tmp_path) -> None:
    inbox = LocalResidentInbox(tmp_path / "inbox")
    await inbox.write_signal(_tick(1))
    ref, _slot = (await inbox.list_signals(limit=1))[0]

    assert await inbox.acknowledge((ref,)) == (ref,)
    # The slot moved; acknowledging the old reference again must not raise or
    # resurrect anything.
    assert await inbox.acknowledge((ref,)) == (ref,)
    assert await inbox.list_signals(limit=10) == []


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_never_touches_the_raw_archive(tmp_path) -> None:
    inbox = LocalResidentInbox(
        tmp_path / "inbox",
        retention_max_pages=1,
        retention_max_age_days=0.0001,
        retention_sweep_interval_seconds=0.0,
    )
    for index in range(20):
        await inbox.write_signal(_tick(index, progress=index))
    ref, _slot = (await inbox.list_signals(limit=1))[0]
    await inbox.acknowledge((ref,))

    time.sleep(0.02)
    await inbox.prune_signals()

    assert inbox.archive.count() == 20, "raw evidence is the only surviving copy"
    assert list(inbox.archive.directory.glob("*.ndjson"))


@pytest.mark.asyncio
async def test_retention_never_prunes_pending_slots(tmp_path) -> None:
    inbox = LocalResidentInbox(
        tmp_path / "inbox",
        retention_max_pages=1,
        retention_max_age_days=0.0001,
        retention_sweep_interval_seconds=0.0,
    )
    for index in range(5):
        await inbox.write_signal(
            ResidentInboxSignal(
                id=f"s-{index}",
                source=f"source-{index}",
                kind="k",
                summary="pending work",
                payload={"n": index},
                observed_at=f"2026-08-03T10:0{index}:00Z",
            )
        )

    time.sleep(0.02)
    assert await inbox.prune_signals() == 0
    assert len(await inbox.list_signals(limit=50)) == 5


# ---------------------------------------------------------------------------
# Invalid outcomes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_invalid_outcomes_block_a_slot(tmp_path) -> None:
    inbox = LocalResidentInbox(tmp_path / "inbox", max_invalid_attempts=3)
    await inbox.write_signal(_tick(1))
    ref, _slot = (await inbox.list_signals(limit=1))[0]

    assert await inbox.record_failed_attempt((ref,), reason="invalid outcome") == ()
    assert await inbox.record_failed_attempt((ref,), reason="invalid outcome") == ()
    assert len(await inbox.list_signals(limit=10)) == 1, "still retried below the bound"

    blocked = await inbox.record_failed_attempt((ref,), reason="invalid outcome")

    assert blocked == (ref,)
    assert await inbox.list_signals(limit=10) == [], "no longer retried forever"
    stuck = await inbox.list_signals(status=ResidentInboxStatus.BLOCKED.value, limit=10)
    assert len(stuck) == 1
    assert stuck[0][1].attempts == 3


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_is_resumable_and_reconciles(tmp_path) -> None:
    root = tmp_path / "inbox"
    legacy = LocalResidentInbox(root)
    # Write flat records the way the pre-coalescing layout did.
    flat_dir = root / "resident/inbox/signals"
    flat_dir.mkdir(parents=True, exist_ok=True)
    from ravn.resident_inbox import render_inbox_signal

    for index in range(30):
        signal = _tick(index, progress=index)
        (flat_dir / f"2026-08-03t10-{index:02d}-00z-tick-{index}.md").write_text(
            render_inbox_signal(signal), encoding="utf-8"
        )

    counts = await legacy.migrate_flat_layout()

    assert counts["read"] == 30
    assert counts["archived"] == 30
    assert legacy.archive.count() == 30
    assert not list(flat_dir.glob("*.md")), "originals removed only after re-filing"
    assert len(await legacy.list_signals(limit=50)) == 1

    # Re-running is a no-op rather than a duplication.
    assert (await legacy.migrate_flat_layout())["read"] == 0
    assert legacy.archive.count() == 30


@pytest.mark.asyncio
async def test_deploying_before_migrating_leaves_the_backlog_intact(tmp_path) -> None:
    """The rollout depends on this: deploy first, migrate deliberately later.

    A flat backlog written by the previous layout must be invisible to the queue
    and untouchable by retention until an operator migrates it, so a deploy can
    fix the write path without racing 84k files.
    """
    from ravn.resident_inbox import render_inbox_signal

    root = tmp_path / "inbox"
    flat_dir = root / "resident/inbox/signals"
    flat_dir.mkdir(parents=True, exist_ok=True)
    for index in range(25):
        signal = _tick(index, progress=index)
        (flat_dir / f"2026-08-03t10-{index:02d}-00z-tick-{index}.md").write_text(
            render_inbox_signal(signal), encoding="utf-8"
        )

    inbox = LocalResidentInbox(
        root,
        retention_max_pages=1,
        retention_max_age_days=0.0001,
        retention_sweep_interval_seconds=0.0,
    )
    # New observations flow normally.
    await inbox.write_signal(_tick(99, progress=99.0))
    pending = await inbox.list_signals(limit=50)
    assert len(pending) == 1
    assert pending[0][1].observation_count == 1, "legacy files are not in the queue"

    ref, _slot = pending[0]
    await inbox.acknowledge((ref,))
    time.sleep(0.02)
    await inbox.prune_signals()

    assert len(list(flat_dir.glob("*.md"))) == 25, "retention never reaches the old layout"


@pytest.mark.asyncio
async def test_migration_leaves_unreadable_files_in_place(tmp_path) -> None:
    root = tmp_path / "inbox"
    inbox = LocalResidentInbox(root)
    flat_dir = root / "resident/inbox/signals"
    flat_dir.mkdir(parents=True, exist_ok=True)
    (flat_dir / "broken.md").write_text("# not a signal record", encoding="utf-8")

    counts = await inbox.migrate_flat_layout()

    assert counts["unreadable"] == 1
    assert (flat_dir / "broken.md").exists(), "nothing unparsed is destroyed"


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_references_address_exact_records(tmp_path) -> None:
    inbox = LocalResidentInbox(tmp_path / "inbox")
    for index in range(5):
        await inbox.write_signal(_tick(index, progress=index * 10))

    _ref, slot = (await inbox.list_signals(limit=1))[0]
    first = inbox.archive.read(slot.first_archive_ref)
    last = inbox.archive.read(slot.last_archive_ref)

    assert first is not None and last is not None
    assert first["signal"]["payload"]["progress"] == 0
    assert last["signal"]["payload"]["progress"] == 40


@pytest.mark.asyncio
async def test_variable_field_names_warn_instead_of_failing_silently(tmp_path, caplog) -> None:
    """Coalescing only bounds the queue when shapes are stable. Say so loudly."""
    import logging

    inbox = LocalResidentInbox(tmp_path / "inbox", pending_slot_warn_threshold=5)
    with caplog.at_level(logging.WARNING, logger="ravn.resident_inbox.backend"):
        for index in range(8):
            await inbox.write_signal(
                ResidentInboxSignal(
                    id=f"v-{index}",
                    source="chatty",
                    kind="k",
                    # A new field name every time defeats coalescing entirely.
                    payload={f"field_{index}": index},
                    summary="variable shape",
                    observed_at=f"2026-08-03T10:0{index}:00Z",
                )
            )

    warnings = [r for r in caplog.records if "pending shape slots" in r.message]
    assert len(warnings) == 1, "warned exactly once, not on every write"


@pytest.mark.asyncio
async def test_archive_range_rebuild_skips_foreign_shapes(tmp_path) -> None:
    inbox = LocalResidentInbox(tmp_path / "inbox")
    await inbox.write_signal(_tick(1, progress=1))
    ref, seen = (await inbox.list_signals(limit=1))[0]

    # An unrelated shape lands between the judged point and the slot's tail.
    await inbox.write_signal(
        ResidentInboxSignal(
            id="unrelated",
            source="elsewhere",
            kind="other",
            summary="different",
            payload={"unrelated": True},
            observed_at="2026-08-03T10:30:00Z",
        )
    )
    await inbox.write_signal(_tick(2, progress=2))

    await inbox.acknowledge((ref,), expected={ref: seen.last_archive_ref})

    remainder = {slot.shape_key: slot for _r, slot in await inbox.list_signals(limit=10)}
    assert remainder[seen.shape_key].observation_count == 1, "foreign records are not folded in"


@pytest.mark.asyncio
async def test_resident_sees_what_a_coalesced_slot_represents(tmp_path) -> None:
    """Judging one tick must never silently acknowledge hundreds."""
    from ravn.adapters.resident_state.mimir import LocalResidentState
    from ravn.domain.models import OutputMode
    from ravn.resident_runtime import ResidentRuntime

    inbox = LocalResidentInbox(tmp_path / "inbox")
    for index in range(200):
        await inbox.write_signal(_tick(index, progress=index / 2.0, temperature=40.0))
    await inbox.write_signal(_tick(201, progress=99.0, temperature=95.0))

    runtime = ResidentRuntime(state=LocalResidentState(tmp_path / "state"), inbox=inbox)
    task = await runtime.next_home_task(limit=50, persona=None, output_mode=OutputMode.AMBIENT)

    assert task is not None
    context = task.initiative_context
    assert "201 observations of this exact shape" in context
    assert "temperature 40–95" in context
    assert "Raw archive range:" in context
    # The excursion travels as a whole payload, not just as a bound.
    assert "Payloads at numeric extremes:" in context
    assert "temperature:max" in context
    # And acknowledgement is pinned to exactly what was shown.
    assert task.resident_inbox_expected


def test_aggregate_summary_lines_render_each_kind() -> None:
    aggregate = ShapeAggregate(
        numeric={"temp": (1.0, 2.5)},
        categorical={"state": ("printing", "idle")},
        high_cardinality=("job",),
    )
    rendered = "\n".join(_summary_lines(aggregate))
    assert "temp 1–2.5" in rendered
    assert "state {printing, idle}" in rendered
    assert "high-cardinality paths: job" in rendered


def test_archive_read_tolerates_bad_references(tmp_path) -> None:
    from ravn.resident_inbox import RawSignalArchive

    archive = RawSignalArchive(tmp_path)
    assert archive.read("") is None
    assert archive.read("not-a-ref") is None
    assert archive.read("2026-08-03:99999") is None
    assert archive.count() == 0
    assert archive.read_range(after="", through="2026-08-03:0", limit=5) == []


def test_archive_ref_sort_key_orders_and_tolerates_garbage() -> None:
    from ravn.resident_inbox import archive_ref_sort_key

    refs = ["2026-08-03:100", "2026-08-02:5", "2026-08-03:20", "garbage"]
    # Malformed sorts first, so an unreadable reference reads as "judged
    # nothing" and leaves observations pending rather than acknowledging them.
    assert sorted(refs, key=archive_ref_sort_key) == [
        "garbage",
        "2026-08-02:5",
        "2026-08-03:20",
        "2026-08-03:100",
    ]


def test_archive_range_is_bounded_and_skips_malformed_lines(tmp_path) -> None:
    from ravn.resident_inbox import RawSignalArchive

    archive = RawSignalArchive(tmp_path)
    refs = [archive.append({"n": index}) for index in range(6)]
    # A corrupted line must not stop the range from reading the rest.
    partition = next(iter(archive.directory.glob("*.ndjson")))
    with partition.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    everything = archive.read_range(after="", through="9999-12-31:0", limit=100)
    assert [record["n"] for _ref, record in everything] == [0, 1, 2, 3, 4, 5]

    bounded = archive.read_range(after=refs[1], through=refs[4], limit=2)
    assert [record["n"] for _ref, record in bounded] == [2, 3]

    assert archive.read_range(after="", through=refs[0], limit=0) == []
    # count() is a line count for reconciliation: a corrupt line is still a
    # record that was written, and must not silently vanish from the total.
    assert archive.count() == 7
