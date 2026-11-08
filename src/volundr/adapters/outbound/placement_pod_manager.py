"""Place each session on the Forge's own machine or in its compute pool.

A Forge has one ``PodManager``. This adapter lets a Forge that runs its own sessions
(``local``, for example ``LocalProcessPodManager``) also run sessions in a compute
pool (``pool``, a ``VmPodManager``, for example on registered hosts). A session goes
to the pool only when it asks for it, and the execution pin the session service
writes before anything runs keeps it there. Nothing is ever moved from one to the
other: a session the pool cannot place does not run locally, and the reverse.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.execution_catalog import ResolvedExecutionPlan
from volundr.domain.models import Session, SessionSpec, SessionStatus
from volundr.domain.ports import PodManager, PodStartResult, SessionCapacity
from volundr.domain.session_runtimes import (
    HOST_SELECTOR_KEY,
    RUNTIME_GRANTS_KEY,
    SessionRuntimeOffer,
)
from volundr.domain.session_startup import StartupStage

#: The workload_config key the session service pins a pool execution under.
COMPUTE_EXECUTION_KEY = "_compute_execution"

#: Keys that ask for the pool when present and non-empty. The pin is the record of a
#: placement already made; the others are a launch's request for one.
_POOL_REQUEST_KEYS = ("execution_profile", "compute_profile", RUNTIME_GRANTS_KEY)


class PlacementPodManager(PodManager):
    """Route sessions between a local pod manager and a compute pool's pod manager."""

    def __init__(self, *, local: PodManager, pool: PodManager, **_extra: object) -> None:
        if not isinstance(local, PodManager) or not isinstance(pool, PodManager):
            raise TypeError("Placement pod manager local and pool must implement PodManager")
        if local is pool:
            raise ValueError("Placement pod manager local and pool must be different managers")
        for name in ("configure_compute", "resolve_execution", "execution_for"):
            if not hasattr(pool, name):
                raise TypeError(
                    f"Placement pod manager pool {type(pool).__name__} has no {name}; "
                    "the pool must be a compute pool pod manager such as VmPodManager"
                )
        self._local = local
        self._pool = pool

    # -- placement ---------------------------------------------------------------

    def runs_on_compute(self, session: Session) -> bool:
        """Whether ``session`` belongs to the pool: pinned there, or asking for it."""
        config = session.workload_config
        if config.get(COMPUTE_EXECUTION_KEY) is not None:
            return True
        if HOST_SELECTOR_KEY in config:
            return True
        return any(config.get(key) for key in _POOL_REQUEST_KEYS)

    def _for(self, session: Session) -> PodManager:
        return self._pool if self.runs_on_compute(session) else self._local

    def _children(self) -> tuple[PodManager, PodManager]:
        return (self._local, self._pool)

    # -- composition hooks -------------------------------------------------------

    def __getattr__(self, name: str) -> Callable[..., None]:
        # Only reached for attributes this class does not define. A ``set_*`` hook
        # exists here exactly when a child has it, so ``hasattr`` composition sees
        # the same hooks it would see on either child.
        if not name.startswith("set_"):
            raise AttributeError(name)
        targets = [child for child in self._children() if hasattr(child, name)]
        if not targets:
            raise AttributeError(name)

        def forward(*args: Any, **kwargs: Any) -> None:
            for child in targets:
                getattr(child, name)(*args, **kwargs)

        return forward

    def configure_compute(self, *args: Any, **kwargs: Any) -> None:
        self._pool.configure_compute(*args, **kwargs)

    def configure_execution(self, *args: Any, **kwargs: Any) -> None:
        self._pool.configure_execution(*args, **kwargs)

    def configure_pool(self, service) -> None:
        self._pool.configure_pool(service)

    @property
    def profile(self) -> str:
        return self._pool.profile

    async def close(self) -> None:
        for child in self._children():
            close = getattr(child, "close", None)
            if close is not None:
                await close()

    # -- execution resolution (SessionExecutionResolver) -------------------------

    async def has_legacy_allocation(self, session: Session) -> bool:
        return await self._pool.has_legacy_allocation(session)

    async def resolve_execution(self, session: Session) -> ResolvedExecutionPlan | None:
        if not self.runs_on_compute(session):
            return None
        return await self._pool.resolve_execution(session)

    async def execution_for(self, session: Session) -> ResolvedExecutionPlan:
        return await self._pool.execution_for(session)

    def execution_reference(self, plan: ResolvedExecutionPlan) -> dict[str, str]:
        return self._pool.execution_reference(plan)

    # -- whole-Forge answers: the default placement, local ------------------------

    @property
    def runtime_backend(self) -> str | None:
        return self._local.runtime_backend

    async def capacity(self) -> SessionCapacity | None:
        return await self._local.capacity()

    async def session_runtimes(self) -> tuple[SessionRuntimeOffer, ...]:
        # The pool's offers are not listed: a launch form that picks the first offer
        # when none is the default would send every launch to the pool. Pool sessions
        # are asked for with host_selector or execution_profile.
        return await self._local.session_runtimes()

    @property
    def delivers_forge_session_token(self) -> bool:
        return all(child.delivers_forge_session_token for child in self._children())

    def delivers_forge_session_token_for(self, session: Session) -> bool:
        """Whether the manager that runs *session* delivers its credential.

        The whole-Forge property above is for the question "can every session get one"; a
        launch only needs the manager it is placed on, so a pool with no runtime does not
        take the credential away from local sessions.
        """
        return self._for(session).delivers_forge_session_token

    @property
    def reports_startup_stages(self) -> bool:
        return any(child.reports_startup_stages for child in self._children())

    # -- per-session lifecycle ----------------------------------------------------

    async def capacity_for(self, session: Session) -> SessionCapacity | None:
        return await self._for(session).capacity_for(session)

    def initial_chat_endpoint(self, session: Session) -> str | None:
        return self._for(session).initial_chat_endpoint(session)

    def initial_code_endpoint(self, session: Session) -> str | None:
        return self._for(session).initial_code_endpoint(session)

    async def broker_target(self, session: Session) -> SessionProxyTarget | None:
        return await self._for(session).broker_target(session)

    async def session_proxy_target(self, session: Session) -> SessionProxyTarget | None:
        child = self._for(session)
        resolve = getattr(child, "session_proxy_target", None)
        if resolve is None:
            return None
        return await resolve(session)

    async def start(self, session: Session, spec: SessionSpec) -> PodStartResult:
        return await self._for(session).start(session, spec)

    async def stop(self, session: Session) -> bool:
        return await self._for(session).stop(session)

    async def destroy(self, session: Session) -> bool:
        return await self._for(session).destroy(session)

    async def status(self, session: Session) -> SessionStatus:
        return await self._for(session).status(session)

    async def status_detail(self, session: Session) -> str | None:
        return await self._for(session).status_detail(session)

    async def startup_stages(self, session: Session) -> tuple[StartupStage, ...] | None:
        child = self._for(session)
        if not child.reports_startup_stages:
            return None
        return await child.startup_stages(session)

    async def wait_for_ready(self, session: Session, timeout: float) -> SessionStatus:
        return await self._for(session).wait_for_ready(session, timeout)
