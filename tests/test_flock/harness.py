"""FlockTestHarness — in-process smoke test harness for the runing party loop.

Wires up the full integration path without K8s, real CLI, or real LLM:

    InProcessMesh (Ravn mesh)
        ├─ SkuldMeshAdapter  ←→  MockCLITransport  (work_request → outcome)
    InProcessBus (Sleipnir)
        ├─ RavnOutcomeHandler  →  ReviewEngine  →  StubTracker
    CompositeMimirAdapter
        ├─ InMemoryMimirPort (local)
        └─ InMemoryMimirPort (hosted)

Usage::

    harness = FlockTestHarness(cli_responses=["verdict: approve\\ntests_passing: true\\n..."])
    async with harness:
        run = make_run(status=RunStatus.RUNNING, session_id="sess-001")
        await harness.dispatch_run(run)
        await harness.assert_run_state(run.tracker_id, RunStatus.MERGED)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from niuu.domain.mimir import (
    MimirLintReport,
    MimirPage,
    MimirPageMeta,
    MimirQueryResult,
    MimirSource,
    MimirSourceMeta,
    ThreadState,
)
from niuu.ports.mimir import MimirPort
from ravn.adapters.mimir.composite import CompositeMimirAdapter
from ravn.domain.events import RavnEvent
from ravn.domain.mimir import MimirMount, WriteRouting
from ravn.ports.mesh import MeshPort
from skuld.config import MeshConfig
from skuld.mesh_adapter import SkuldMeshAdapter
from skuld.transports import CLITransport, TransportCapabilities
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.catalog import ravn_session_ended
from tests.test_ting.stubs import (
    StubTracker,
    StubTrackerFactory,
    StubVolundrFactory,
    StubVolundrPort,
)
from ting.adapters.ravn_outcome_handler import RavnOutcomeHandler
from ting.config import ReviewConfig
from ting.domain.models import PRStatus, Run, RunStatus
from ting.domain.services.review_engine import ReviewEngine
from ting.ports.git import GitPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Outcome block templates
# ---------------------------------------------------------------------------

OUTCOME_APPROVE = """\
---outcome---
verdict: approve
tests_passing: true
scope_adherence: 0.95
pr_url: https://github.com/niuulabs/test/pull/1
summary: Implementation complete with full test coverage
authoritative: true
checks:
  - node: verify
    verdict: pass
---end---"""

OUTCOME_RETRY = """\
---outcome---
verdict: retry
tests_passing: false
scope_adherence: 0.80
summary: Tests are failing — retry needed
---end---"""

OUTCOME_ESCALATE = """\
---outcome---
verdict: escalate
tests_passing: false
scope_adherence: 0.60
summary: Implementation is incomplete — escalating for human review
---end---"""


# ---------------------------------------------------------------------------
# MockCLITransport
# ---------------------------------------------------------------------------


class MockCLITransport(CLITransport):
    """CLI transport that serves canned responses with ``---outcome---`` blocks.

    Accepts a list of response strings.  Each call to ``send_message`` pops the
    next response from the list (cycling on the last entry when exhausted).
    """

    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        if not responses:
            raise ValueError("MockCLITransport requires at least one response")
        self._responses = list(responses)
        self._call_index = 0
        self.received_prompts: list[str] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send_message(self, content: str) -> None:
        self.received_prompts.append(content)
        response = self._responses[min(self._call_index, len(self._responses) - 1)]
        self._call_index += 1
        if self._event_callback is not None:
            await self._event_callback({"type": "result", "result": response})

    @property
    def session_id(self) -> str | None:
        return "mock-cli-session"

    @property
    def last_result(self) -> dict | None:
        return None

    @property
    def is_alive(self) -> bool:
        return True

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(send_message=True)


# ---------------------------------------------------------------------------
# InProcessMesh — in-process ravn mesh with RPC support
# ---------------------------------------------------------------------------


class InProcessMesh(MeshPort):
    """Minimal in-process Ravn mesh for testing.

    Supports:
    - ``set_rpc_handler``: register the RPC handler (used by SkuldMeshAdapter)
    - ``send``: route directly to the registered RPC handler (ignores peer ID)
    - ``publish`` / ``subscribe`` / ``unsubscribe``: in-process pub/sub
    """

    def __init__(self) -> None:
        self._rpc_handler: Callable[[dict], Awaitable[dict]] | None = None
        self._subscriptions: dict[str, list[Callable[[RavnEvent], Awaitable[None]]]] = {}

    def set_rpc_handler(self, handler: Callable[[dict], Awaitable[dict]]) -> None:
        self._rpc_handler = handler

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self._rpc_handler = None
        self._subscriptions.clear()

    async def publish(self, event: RavnEvent, topic: str) -> None:
        handlers = list(self._subscriptions.get(topic, []))
        for h in handlers:
            await h(event)

    async def subscribe(
        self,
        topic: str,
        handler: Callable[[RavnEvent], Awaitable[None]],
    ) -> None:
        self._subscriptions.setdefault(topic, []).append(handler)

    async def unsubscribe(self, topic: str) -> None:
        self._subscriptions.pop(topic, None)

    async def send(
        self,
        target_peer_id: str,
        message: dict,
        *,
        timeout_s: float = 10.0,
    ) -> dict:
        if self._rpc_handler is None:
            raise RuntimeError(
                f"InProcessMesh.send called but no RPC handler registered "
                f"(target={target_peer_id!r})"
            )
        return await asyncio.wait_for(self._rpc_handler(message), timeout=timeout_s)


# ---------------------------------------------------------------------------
# InMemoryMimirPort — simple in-memory Mimir for testing
# ---------------------------------------------------------------------------


class InMemoryMimirPort(MimirPort):
    """In-memory Mímir port for smoke tests.

    Stores pages by path.  Only ``upsert_page``, ``read_page``, and
    ``list_pages`` are implemented; other operations are no-ops or stubs.
    """

    def __init__(self) -> None:
        self._pages: dict[str, str] = {}

    def clear(self) -> None:
        """Remove all pages (simulates local mount teardown)."""
        self._pages.clear()

    async def upsert_page(
        self,
        path: str,
        content: str,
        mimir: str | None = None,
        meta: MimirPageMeta | None = None,
    ) -> None:
        self._pages[path] = content

    async def read_page(self, path: str) -> str:
        if path not in self._pages:
            raise FileNotFoundError(f"Mímir page not found: {path}")
        return self._pages[path]

    async def get_page(self, path: str) -> MimirPage:
        content = await self.read_page(path)
        return MimirPage(
            meta=MimirPageMeta(
                path=path,
                title=path.split("/")[-1],
                summary="",
                category="test",
                updated_at=datetime.now(UTC),
                source_ids=[],
            ),
            content=content,
        )

    async def list_pages(
        self,
        category: str | None = None,
        prefix: str | None = None,
    ) -> list[MimirPageMeta]:
        result = []
        for path in self._pages:
            if prefix is not None and not path.startswith(prefix):
                continue
            result.append(
                MimirPageMeta(
                    path=path,
                    title=path.split("/")[-1],
                    summary="",
                    category=category or "test",
                    updated_at=datetime.now(UTC),
                    source_ids=[],
                )
            )
        return result

    async def ingest(self, source: MimirSource) -> list[str]:
        return []

    async def query(self, question: str) -> MimirQueryResult:
        return MimirQueryResult(question=question, answer="", sources=[])

    async def search(self, query: str) -> list[MimirPage]:
        return []

    async def lint(self, fix: bool = False) -> MimirLintReport:
        return MimirLintReport(issues=[], pages_checked=len(self._pages))

    async def read_source(self, source_id: str) -> MimirSource | None:
        return None

    async def list_sources(self, *, unprocessed_only: bool = False) -> list[MimirSourceMeta]:
        return []

    async def list_threads(
        self,
        state: ThreadState | None = None,
        limit: int = 100,
    ) -> list[MimirPage]:
        return []

    async def get_thread_queue(
        self,
        owner_id: str | None = None,
        limit: int = 50,
    ) -> list[MimirPage]:
        return []

    async def update_thread_state(self, path: str, state: ThreadState) -> None:
        pass

    async def assign_thread_owner(self, path: str, owner_id: str | None) -> None:
        pass

    async def update_thread_weight(
        self,
        path: str,
        weight: float,
        signals: dict | None = None,
    ) -> None:
        pass


# ---------------------------------------------------------------------------
# StubGit
# ---------------------------------------------------------------------------


class StubGit(GitPort):
    """No-op Git port for unit tests."""

    async def create_branch(self, repo: str, branch: str, base: str) -> None:
        pass

    async def merge_branch(self, repo: str, source: str, target: str) -> None:
        pass

    async def delete_branch(self, repo: str, branch: str) -> None:
        pass

    async def create_pr(self, repo: str, source: str, target: str, title: str) -> str:
        return "pr-stub-001"

    async def get_pr_status(self, pr_id: str) -> PRStatus:
        return PRStatus(
            pr_id=pr_id,
            url=f"https://github.com/niuulabs/test/pull/{pr_id}",
            state="open",
            mergeable=True,
            ci_passed=None,
        )

    async def get_pr_changed_files(self, pr_id: str) -> list[str]:
        return []


# ---------------------------------------------------------------------------
# FlockTestHarness
# ---------------------------------------------------------------------------

_DEFAULT_OWNER = "test-owner"
_DEFAULT_SKULD_SESSION = "skuld-session-001"
_DEFAULT_SKULD_PEER = "skuld-peer-001"

_REVIEW_CONFIG = ReviewConfig()


class FlockTestHarness:
    """In-process test harness for the runing party integration loop.

    Wires SkuldMeshAdapter, RavnOutcomeHandler, ReviewEngine, and
    CompositeMimirAdapter together using in-process transports.

    Parameters
    ----------
    cli_responses:
        Ordered list of canned CLI responses.  Each call to ``dispatch_run``
        consumes the next response; the last response is repeated once
        exhausted.  Use the module-level constants ``OUTCOME_APPROVE``,
        ``OUTCOME_RETRY``, ``OUTCOME_ESCALATE``, or provide custom text.
    owner_id:
        Owner ID used by RavnOutcomeHandler when looking up runs.
    skuld_session_id:
        Session ID given to SkuldMeshAdapter.
    review_config:
        ReviewConfig for ReviewEngine.
    """

    def __init__(
        self,
        cli_responses: list[str],
        *,
        owner_id: str = _DEFAULT_OWNER,
        skuld_session_id: str = _DEFAULT_SKULD_SESSION,
        review_config: ReviewConfig | None = None,
    ) -> None:
        self.owner_id = owner_id
        self.skuld_session_id = skuld_session_id

        # Sleipnir
        self.bus: InProcessBus = InProcessBus()

        # Ravn mesh
        self.mesh: InProcessMesh = InProcessMesh()

        # CLI transport
        self.cli = MockCLITransport(cli_responses)

        # Skuld mesh adapter
        mesh_config = MeshConfig(
            enabled=True,
            peer_id=_DEFAULT_SKULD_PEER,
            persona="coder",
            consumes_event_types=[],
            default_work_timeout_s=10.0,
        )
        from niuu.mesh.participant import MeshParticipant

        _skuld_participant = MeshParticipant(
            mesh=self.mesh,
            discovery=None,
            peer_id=mesh_config.peer_id,
        )
        self.skuld = SkuldMeshAdapter(
            participant=_skuld_participant,
            transport=self.cli,
            config=mesh_config,
            session_id=skuld_session_id,
        )

        # Tracker
        self.tracker = StubTracker()
        self.tracker_factory = StubTrackerFactory(self.tracker)

        # Volundr stub
        self.volundr = StubVolundrPort()
        self.volundr_factory = StubVolundrFactory(self.volundr)

        # Git stub
        self.git = StubGit()

        # Review engine
        _cfg = review_config or _REVIEW_CONFIG
        self.review_engine = ReviewEngine(
            tracker_factory=self.tracker_factory,
            volundr_factory=self.volundr_factory,
            review_config=_cfg,
        )

        # Outcome handler
        self.outcome_handler = RavnOutcomeHandler(
            subscriber=self.bus,
            tracker_factory=self.tracker_factory,
            review_engine=self.review_engine,
            owner_id=owner_id,
        )

        # Mimir mounts
        self.local_mimir = InMemoryMimirPort()
        self.hosted_mimir = InMemoryMimirPort()
        self.mimir = CompositeMimirAdapter(
            mounts=[
                MimirMount(name="local", port=self.local_mimir, role="local", read_priority=0),
                MimirMount(name="hosted", port=self.hosted_mimir, role="shared", read_priority=1),
            ],
            write_routing=WriteRouting(
                rules=[("project/", ["hosted"])],
                default=["local"],
            ),
        )

        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self.skuld.start()
        await self.outcome_handler.start()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        await self.outcome_handler.stop()
        await self.skuld.stop()
        self._started = False

    async def __aenter__(self) -> FlockTestHarness:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Core test methods
    # ------------------------------------------------------------------

    async def dispatch_run(self, run: Run) -> None:
        """Trigger the full runing party flow for *run*.

        Steps:
        1. Register run in StubTracker.
        2. Send a ``work_request`` to Skuld via the in-process mesh
           (simulates coordinator delegating coding work).
        3. Skuld feeds the prompt to MockCLITransport and collects the result.
        4. Extract outcome fields from the work_request response.
        5. Publish canonical ``ravn.session.ended`` on Sleipnir with the
           structured outcome payload and run/session identifiers.
        6. Flush Sleipnir bus so RavnOutcomeHandler processes synchronously.
        """
        if not self._started:
            raise RuntimeError("Call harness.start() or use `async with harness` first")

        await self.tracker.create_run(run)

        work_request = {
            "type": "work_request",
            "prompt": f"Implement: {run.description}",
            "event_type": "code.requested",
            "request_id": f"req-{run.tracker_id}",
            "timeout_s": 10.0,
        }
        response = await self.mesh.send(_DEFAULT_SKULD_PEER, work_request, timeout_s=15.0)

        outcome_payload: dict[str, Any] = {}
        if response.get("outcome") and isinstance(response["outcome"].get("fields"), dict):
            outcome_payload = response["outcome"]["fields"]
        elif response.get("status") != "complete":
            logger.warning("work_request response status=%s", response.get("status"))

        sleipnir_event = ravn_session_ended(
            session_id=run.session_id or run.tracker_id,
            persona="run-executor",
            outcome="success" if outcome_payload.get("verdict") == "approve" else "partial",
            token_count=0,
            duration_s=0.0,
            source="skuld:test",
            correlation_id=run.session_id,
        )
        sleipnir_event.payload["run_id"] = str(run.id)
        sleipnir_event.payload["structured_outcome"] = dict(outcome_payload)
        sleipnir_event.payload["outcome_valid"] = bool(outcome_payload)
        await self.bus.publish(sleipnir_event)
        await self.bus.flush()

    async def assert_run_state(self, tracker_id: str, expected: RunStatus) -> None:
        """Assert that *tracker_id* has the *expected* RunStatus."""
        run = await self.tracker.get_run(tracker_id)
        actual = run.status
        assert actual == expected, (
            f"Run {tracker_id}: expected status {expected!r} but got {actual!r}"
        )

    async def get_run(self, tracker_id: str) -> Run:
        return await self.tracker.get_run(tracker_id)
