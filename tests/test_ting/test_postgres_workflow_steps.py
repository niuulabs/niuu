"""The Postgres step ledger, against a mocked asyncpg pool."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from ting.adapters.postgres_workflow_steps import PostgresWorkflowStepRepository
from ting.domain.workflow_steps import (
    FlagKind,
    StepFlag,
    StepInput,
    StepKind,
    StepState,
    WaitingOn,
    WaitingOnKind,
    WorkflowStep,
)

NOW = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


class _Context:
    def __init__(self, value: Any, on_exit: Any = None) -> None:
        self._value = value
        self._on_exit = on_exit

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *exc: object) -> bool:
        if self._on_exit is not None:
            self._on_exit(*exc)
        return False


class _Connection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_results: list[list[dict[str, Any]]] = []
        self.fetchrow_result: dict[str, Any] | None = None
        self.transaction_exits: list[tuple[object, ...]] = []
        self.in_transaction = False

    def transaction(self) -> _Context:
        self.in_transaction = True

        def left(*exc: object) -> None:
            self.in_transaction = False
            self.transaction_exits.append(exc)

        return _Context(self, left)

    async def execute(self, query: str, *args: Any) -> str:
        assert self.in_transaction
        self.executed.append((query, args))
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.executed.append((query, args))
        return self.fetch_results.pop(0)

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.executed.append((query, args))
        return self.fetchrow_result


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def acquire(self) -> _Context:
        return _Context(self.connection)

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return await self.connection.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return await self.connection.fetchrow(query, *args)


def _step_row(run_id: Any, **changes: Any) -> dict[str, Any]:
    row = {
        "id": uuid4(),
        "run_id": run_id,
        "node_id": "review",
        "member": "reviewer",
        "kind": "stage_member",
        "attempt": 2,
        "generation": 1,
        "state": "active",
        "session_id": "session-1",
        "inputs": json.dumps(
            [{"seq": 3, "event_type": "x.y", "edge_id": "e1", "node_id": "code", "member": "coder"}]
        ),
        "verdict": None,
        "outcome": json.dumps({"k": "v"}),
        "note": "",
        "evidence": {},
        "waiting_on": None,
        "deadline_at": NOW,
        "created_at": NOW,
        "activated_at": NOW,
        "closed_at": None,
    }
    row.update(changes)
    return row


def _flag_row(run_id: Any, activation_id: Any, **changes: Any) -> dict[str, Any]:
    row = {
        "run_id": run_id,
        "activation_id": activation_id,
        "kind": "overdue",
        "since": NOW,
        "raised_at": NOW,
        "detail": "past its deadline",
        "notified_at": None,
        "resolved_at": None,
        "resolution": "",
    }
    row.update(changes)
    return row


def _event_row(run_id: Any, seq: int = 1, **changes: Any) -> dict[str, Any]:
    row = {
        "run_id": run_id,
        "seq": seq,
        "event_type": "engine.run.launched",
        "payload": json.dumps({"a": 1}),
        "source_activation_id": None,
        "generation": 0,
        "created_at": NOW,
    }
    row.update(changes)
    return row


@pytest.fixture
def connection() -> _Connection:
    return _Connection()


@pytest.fixture
def repository(connection: _Connection) -> PostgresWorkflowStepRepository:
    return PostgresWorkflowStepRepository(_Pool(connection))  # type: ignore[arg-type]


async def test_a_transaction_locks_the_run_before_anything_else(
    repository: PostgresWorkflowStepRepository, connection: _Connection
) -> None:
    run_id = uuid4()

    async with repository.transaction(run_id):
        assert connection.in_transaction

    query, args = connection.executed[0]
    assert "pg_advisory_xact_lock" in query
    assert args == (f"workflow-steps:{run_id}",)
    assert connection.in_transaction is False


async def test_a_transaction_that_raises_leaves_through_the_rollback_path(
    repository: PostgresWorkflowStepRepository, connection: _Connection
) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        async with repository.transaction(uuid4()):
            raise RuntimeError("boom")

    assert connection.transaction_exits[0][0] is RuntimeError


async def test_load_reads_steps_oldest_first_and_events_by_sequence(
    repository: PostgresWorkflowStepRepository, connection: _Connection
) -> None:
    run_id = uuid4()
    waiting = WaitingOn(WaitingOnKind.PERSON, "gate", NOW)
    connection.fetch_results = [
        [
            _step_row(run_id),
            _step_row(
                run_id,
                kind="gate",
                member=None,
                state="waiting",
                session_id=None,
                waiting_on=json.dumps(waiting.to_json()),
                outcome={"already": "decoded"},
            ),
        ],
        [_event_row(run_id, 1), _event_row(run_id, 2, payload={"decoded": True}, generation=1)],
        [],
    ]

    state = await repository.load(run_id)

    first, gate = state.steps
    assert (first.node_id, first.member, first.attempt, first.generation) == (
        "review",
        "reviewer",
        2,
        1,
    )
    assert first.inputs == (StepInput(3, "x.y", "e1", "code", "coder"),)
    assert first.outcome == {"k": "v"} and first.evidence == {}
    assert (gate.kind, gate.state, gate.waiting_on) == (StepKind.GATE, StepState.WAITING, waiting)
    assert gate.outcome == {"already": "decoded"}
    assert [(e.seq, e.payload, e.generation) for e in state.events] == [
        (1, {"a": 1}, 0),
        (2, {"decoded": True}, 1),
    ]
    steps_query = connection.executed[0][0]
    assert "FROM workflow_steps" in steps_query and "ORDER BY created_at, id" in steps_query
    assert "ORDER BY seq" in connection.executed[1][0]


async def test_find_step_returns_the_row_or_nothing(
    repository: PostgresWorkflowStepRepository, connection: _Connection
) -> None:
    run_id = uuid4()
    row = _step_row(run_id)
    connection.fetchrow_result = row

    found = await repository.find_step(row["id"])

    assert found is not None and found.id == row["id"] and found.run_id == run_id
    assert connection.executed[0][1] == (row["id"],)

    connection.fetchrow_result = None
    assert await repository.find_step(uuid4()) is None


async def test_save_step_upserts_every_column_as_parameters(
    repository: PostgresWorkflowStepRepository, connection: _Connection
) -> None:
    run_id = uuid4()
    waiting = WaitingOn(WaitingOnKind.EVENT, "wait:w:forge.checks", NOW)
    step = WorkflowStep(
        id=uuid4(),
        run_id=run_id,
        node_id="publish",
        member="coordinator",
        kind=StepKind.STAGE_MEMBER,
        attempt=1,
        generation=0,
        state=StepState.WAITING,
        created_at=NOW,
        session_id="s1",
        inputs=(StepInput(1, "a.b", "e", "n", None),),
        verdict=None,
        outcome={"o": 1},
        note="n",
        evidence={"e": 2},
        waiting_on=waiting,
        deadline_at=NOW,
        activated_at=NOW,
    )

    async with repository.transaction(run_id) as tx:
        await tx.save_step(step)

    query, args = connection.executed[1]
    assert "INSERT INTO workflow_steps" in query and "ON CONFLICT (id) DO UPDATE" in query
    assert "$19" in query and "$20" not in query
    assert args[:9] == (
        step.id,
        run_id,
        "publish",
        "coordinator",
        "stage_member",
        1,
        0,
        "waiting",
        "s1",
    )
    assert json.loads(args[9]) == [step.inputs[0].to_json()]
    assert json.loads(args[11]) == {"o": 1} and json.loads(args[13]) == {"e": 2}
    assert json.loads(args[14]) == waiting.to_json()
    assert args[15:] == (NOW, NOW, NOW, None)


async def test_a_step_that_is_not_waiting_saves_no_handle(
    repository: PostgresWorkflowStepRepository, connection: _Connection
) -> None:
    run_id = uuid4()
    step = WorkflowStep(
        id=uuid4(),
        run_id=run_id,
        node_id="n",
        member="m",
        kind=StepKind.STAGE_MEMBER,
        attempt=1,
        generation=0,
        state=StepState.PENDING,
        created_at=NOW,
    )

    async with repository.transaction(run_id) as tx:
        await tx.save_step(step)

    assert connection.executed[1][1][14] is None


async def test_append_event_numbers_the_event_inside_the_insert(
    repository: PostgresWorkflowStepRepository, connection: _Connection
) -> None:
    run_id = uuid4()
    source = uuid4()
    connection.fetchrow_result = _event_row(
        run_id, 7, event_type="engine.step.closed", source_activation_id=source, generation=2
    )

    async with repository.transaction(run_id) as tx:
        event = await tx.append_event(
            "engine.step.closed",
            {"verdict": "ok"},
            generation=2,
            source_activation_id=source,
            created_at=NOW,
        )

    query, args = connection.executed[1]
    assert "COALESCE(MAX(seq), 0) + 1" in query and "RETURNING" in query
    assert args[0] == run_id and args[1] == "engine.step.closed"
    assert json.loads(args[2]) == {"verdict": "ok"}
    assert args[3:] == (source, 2, NOW)
    assert (event.seq, event.generation, event.source_activation_id) == (7, 2, source)


async def test_the_transaction_loads_the_run_it_locked(
    repository: PostgresWorkflowStepRepository, connection: _Connection
) -> None:
    run_id = uuid4()
    connection.fetch_results = [[_step_row(run_id)], [_event_row(run_id)], []]

    async with repository.transaction(run_id) as tx:
        state = await tx.load()

    assert state.run_id == run_id and len(state.steps) == 1 and len(state.events) == 1
    assert connection.executed[1][1] == (run_id,)


async def test_load_reads_the_runs_flags_with_their_resolution(
    repository: PostgresWorkflowStepRepository, connection: _Connection
) -> None:
    run_id = uuid4()
    step = _step_row(run_id)
    resolved = NOW.replace(minute=5)
    connection.fetch_results = [
        [step],
        [],
        [
            _flag_row(run_id, step["id"]),
            _flag_row(
                run_id,
                step["id"],
                kind="unclosed",
                notified_at=NOW,
                resolved_at=resolved,
                resolution="step is done",
            ),
        ],
    ]

    state = await repository.load(run_id)

    overdue, unclosed = state.flags
    assert (overdue.kind, overdue.is_resolved, overdue.notified_at) == (
        FlagKind.OVERDUE,
        False,
        None,
    )
    assert (unclosed.kind, unclosed.resolved_at, unclosed.resolution) == (
        FlagKind.UNCLOSED,
        resolved,
        "step is done",
    )
    assert "FROM workflow_step_flags" in connection.executed[2][0]
    assert connection.executed[2][1] == (run_id,)


async def test_raising_a_flag_is_one_insert_that_reports_whether_it_was_new(
    repository: PostgresWorkflowStepRepository, connection: _Connection
) -> None:
    run_id, activation_id = uuid4(), uuid4()
    flag = StepFlag(
        run_id=run_id,
        activation_id=activation_id,
        kind=FlagKind.NOT_STARTED,
        since=NOW,
        raised_at=NOW,
        detail="no session",
    )

    async with repository.transaction(run_id) as tx:
        connection.fetchrow_result = {"activation_id": activation_id}
        first = await tx.raise_flag(flag)
        connection.fetchrow_result = None
        again = await tx.raise_flag(flag)

    query, args = connection.executed[1]
    assert "ON CONFLICT (activation_id, kind) DO NOTHING" in query
    assert args == (activation_id, "not_started", run_id, NOW, NOW, "no session")
    assert (first, again) == (True, False)


async def test_resolving_and_notifying_a_flag_touch_only_that_flag(
    repository: PostgresWorkflowStepRepository, connection: _Connection
) -> None:
    run_id, activation_id = uuid4(), uuid4()

    async with repository.transaction(run_id) as tx:
        await tx.resolve_flag(activation_id, FlagKind.OVERDUE, at=NOW, resolution="step is done")
        await tx.mark_flag_notified(activation_id, FlagKind.OVERDUE, at=NOW)

    resolve_query, resolve_args = connection.executed[1]
    notify_query, notify_args = connection.executed[2]
    assert "SET resolved_at = $3, resolution = $4" in resolve_query
    assert "resolved_at IS NULL" in resolve_query
    assert resolve_args == (activation_id, "overdue", NOW, "step is done")
    assert "SET notified_at = $3" in notify_query
    assert notify_args == (activation_id, "overdue", NOW)
