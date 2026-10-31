from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from niuu.ports.cli import CLITransport, TransportCapabilities
from ravn.adapters.executors.cli import CliTransportExecutor
from ravn.adapters.executors.cli_threads import CliThreadStore, workflow_thread_key
from ravn.domain.models import Session


@dataclass
class _Step:
    root_correlation_id: str = "run-1"
    workflow_node_id: str = "plan-draft"
    persona: str | None = "ticket-planner"


class _Channel:
    async def emit(self, event: object) -> None:
        return None


class _ThreadTransport(CLITransport):
    started: list[str] = []
    fail_resume = False

    def __init__(self, workspace_dir: str, resume_session_id: str = "") -> None:
        super().__init__()
        self._resume_session_id = resume_session_id

    async def start(self) -> None:
        if self._resume_session_id and self.fail_resume:
            raise RuntimeError("Could not resume the existing Codex conversation")
        _ThreadTransport.started.append(self._resume_session_id)

    async def stop(self) -> None:
        return None

    async def send_message(self, content: str) -> None:
        return None

    @property
    def session_id(self) -> str | None:
        return self._resume_session_id or f"thread-{len(_ThreadTransport.started)}"

    @property
    def last_result(self) -> dict | None:
        return None

    @property
    def is_alive(self) -> bool:
        return True

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(session_resume=True)


class _NoResumeTransport(_ThreadTransport):
    def __init__(self, workspace_dir: str) -> None:
        super().__init__(workspace_dir)


@pytest.fixture(autouse=True)
def _reset_transport() -> None:
    _ThreadTransport.started = []
    _ThreadTransport.fail_resume = False


def _agent(store: CliThreadStore, key: str = "run-1/planner/draft", **executor_kwargs: object):
    adapter = executor_kwargs.pop("adapter", _ThreadTransport)
    executor = CliTransportExecutor(
        transport_adapter=f"{adapter.__module__}.{adapter.__qualname__}",
        **executor_kwargs,
    )
    return executor.build(
        workspace_dir="/tmp/workspace",
        session=Session(),
        permission_mode="workspace-write",
        channel=_Channel(),
        workflow_thread_store=store,
        workflow_thread_key=lambda: key,
    )


async def _run_step(store: CliThreadStore, **kwargs: object) -> None:
    agent = _agent(store, **kwargs)
    await agent._ensure_transport()
    await agent.close()


def test_key_names_the_run_persona_and_node() -> None:
    assert workflow_thread_key(_Step()) == "run-1/ticket-planner/plan-draft"


@pytest.mark.parametrize(
    "task",
    [None, _Step(root_correlation_id=""), _Step(workflow_node_id="")],
)
def test_only_a_workflow_step_has_a_thread(task: _Step | None) -> None:
    assert workflow_thread_key(task) == ""


def test_store_survives_a_process_restart(tmp_path: Path) -> None:
    path = tmp_path / "state" / "cli_threads.json"
    store = CliThreadStore(path)
    assert store.claim("k") == ""
    store.release("k", "thread-a")

    assert CliThreadStore(path).claim("k") == "thread-a"


def test_a_held_thread_has_no_second_writer(tmp_path: Path) -> None:
    store = CliThreadStore(tmp_path / "cli_threads.json")
    store.claim("k")

    assert store.claim("k") is None

    store.release("k", "thread-a")
    assert store.claim("k") == "thread-a"


def test_release_without_a_thread_keeps_the_last_one(tmp_path: Path) -> None:
    store = CliThreadStore(tmp_path / "cli_threads.json")
    store.claim("k")
    store.release("k", "thread-a")
    store.claim("k")
    store.release("k", "")

    assert store.claim("k") == "thread-a"


async def test_second_step_of_a_node_resumes_its_thread(tmp_path: Path) -> None:
    store = CliThreadStore(tmp_path / "cli_threads.json")

    await _run_step(store)
    await _run_step(store)

    assert _ThreadTransport.started == ["", "thread-1"]


async def test_another_node_starts_its_own_thread(tmp_path: Path) -> None:
    store = CliThreadStore(tmp_path / "cli_threads.json")

    await _run_step(store, key="run-1/reviewer/check")
    await _run_step(store, key="run-1/planner/draft")

    assert _ThreadTransport.started == ["", ""]


async def test_a_task_outside_a_workflow_stores_nothing(tmp_path: Path) -> None:
    path = tmp_path / "cli_threads.json"

    await _run_step(CliThreadStore(path), key="")

    assert not path.exists()
    assert _ThreadTransport.started == [""]


async def test_a_concurrent_step_leaves_the_held_thread_alone(tmp_path: Path) -> None:
    store = CliThreadStore(tmp_path / "cli_threads.json")
    await _run_step(store)

    holder = _agent(store)
    await holder._ensure_transport()
    await _run_step(store)
    await holder.close()

    assert _ThreadTransport.started == ["", "thread-1", ""]
    assert store.claim("run-1/planner/draft") == "thread-1"


async def test_operator_can_turn_resuming_off(tmp_path: Path) -> None:
    store = CliThreadStore(tmp_path / "cli_threads.json")

    await _run_step(store, resume_workflow_threads=False)
    await _run_step(store, resume_workflow_threads=False)

    assert _ThreadTransport.started == ["", ""]


async def test_transport_that_cannot_resume_is_left_alone(tmp_path: Path) -> None:
    path = tmp_path / "cli_threads.json"

    await _run_step(CliThreadStore(path), adapter=_NoResumeTransport)

    assert not path.exists()


async def test_failed_resume_is_loud_and_names_the_remedy(tmp_path: Path) -> None:
    store = CliThreadStore(tmp_path / "cli_threads.json")
    await _run_step(store)
    _ThreadTransport.fail_resume = True

    agent = _agent(store)
    with pytest.raises(RuntimeError, match="resume_workflow_threads: false") as failure:
        await agent._ensure_transport()
    await agent.close()

    assert "thread-1" in str(failure.value)
    assert store.claim("run-1/planner/draft") == "thread-1"
