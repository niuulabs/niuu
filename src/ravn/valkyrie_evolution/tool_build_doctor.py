"""Diagnose the Valkyrie -> Ting/Forge tool-build path hop by hop.

A misconfigured tool-build backend otherwise surfaces as a vague
``ToolBuildError`` deep inside a poll loop. The doctor probes each hop of the
path with READ-ONLY calls and reports a precise ``hop N: <status> <reason>`` so
an operator sees exactly where the path breaks.

By design the doctor NEVER raises: a broken hop becomes a ``FAIL`` result and
every later hop that depends on it becomes ``SKIP``. It also never launches an
actual build.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ravn.config import Settings
from ravn.domain.capability_catalog import (
    WorkflowCapability,
    WorkflowSelector,
    select_workflow,
)


class HopStatus(StrEnum):
    """Outcome of a single diagnostic hop."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass(frozen=True)
class HopResult:
    """One line of the doctor checklist."""

    number: int
    title: str
    status: HopStatus
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hop": self.number,
            "title": self.title,
            "status": self.status.value,
            "reason": self.reason,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class DoctorReport:
    """The full checklist produced by :func:`diagnose_tool_build`."""

    hops: list[HopResult]

    @property
    def ok(self) -> bool:
        """True when no hop failed (all PASS/SKIP)."""
        return all(hop.status is not HopStatus.FAIL for hop in self.hops)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "hops": [hop.to_dict() for hop in self.hops],
        }


_TING_WORKFLOWS_PATH = "/api/v1/ting/workflows"
_FORGE_HEALTH_PATH = "/api/v1/forge/health"


async def diagnose_tool_build(settings: Settings) -> DoctorReport:
    """Probe every hop of the tool-build path and return a checklist.

    Exception-safe by design: any unexpected error inside a hop is caught and
    rendered as a ``FAIL`` line rather than propagating.
    """
    hops: list[HopResult] = []

    hop1 = _hop1_config(settings)
    hops.append(hop1)
    if hop1.status is not HopStatus.PASS:
        return DoctorReport(hops=hops)

    hop2, backend = _hop2_construct(settings)
    hops.append(hop2)
    if backend is None:
        hops.append(_skip(3, "auth", "backend not constructed"))
        hops.append(_skip(4, "reachability", "backend not constructed"))
        hops.append(_skip(5, "workflow discovery", "backend not constructed"))
        return DoctorReport(hops=hops)

    kind = _backend_kind(backend)

    hops.append(_hop3_auth(settings, backend))
    hop4 = await _hop4_reachability(backend, kind)
    hops.append(hop4)
    hops.append(await _hop5_workflow(backend, kind, hop4))

    return DoctorReport(hops=hops)


# ---------------------------------------------------------------------------
# Hop 1 — config
# ---------------------------------------------------------------------------


def _hop1_config(settings: Settings) -> HopResult:
    adapter = settings.resident_evolution.tool_build_adapter
    if not adapter:
        return HopResult(
            number=1,
            title="config",
            status=HopStatus.SKIP,
            reason="inline authoring, no backend configured",
        )
    return HopResult(
        number=1,
        title="config",
        status=HopStatus.PASS,
        reason=f"tool_build_adapter={adapter}",
        details={"tool_build_adapter": adapter},
    )


# ---------------------------------------------------------------------------
# Hop 2 — backend construct
# ---------------------------------------------------------------------------


def _hop2_construct(settings: Settings) -> tuple[HopResult, Any | None]:
    try:
        from ravn.cli.commands import _build_tool_build_backend  # noqa: PLC0415

        backend = _build_tool_build_backend(settings)
    except Exception as exc:  # noqa: BLE001 — doctor must never throw
        return (
            HopResult(
                number=2,
                title="backend construct",
                status=HopStatus.FAIL,
                reason=f"{type(exc).__name__}: {exc}",
            ),
            None,
        )

    if backend is None:
        return (
            HopResult(
                number=2,
                title="backend construct",
                status=HopStatus.FAIL,
                reason="adapter configured but constructor returned None",
            ),
            None,
        )

    class_name = type(backend).__name__
    base_url = _backend_base_url(backend)
    return (
        HopResult(
            number=2,
            title="backend construct",
            status=HopStatus.PASS,
            reason=f"{class_name} base_url={base_url or '<unset>'}",
            details={"class": class_name, "base_url": base_url},
        ),
        backend,
    )


# ---------------------------------------------------------------------------
# Hop 3 — auth
# ---------------------------------------------------------------------------


def _hop3_auth(settings: Settings, backend: Any) -> HopResult:
    kwargs = dict(settings.resident_evolution.tool_build_kwargs)
    external_token_env = str(kwargs.get("external_token_env") or "")
    if external_token_env:
        is_set = bool(os.environ.get(external_token_env, "").strip())
        status = HopStatus.PASS if is_set else HopStatus.FAIL
        state = "set" if is_set else "unset"
        return HopResult(
            number=3,
            title="auth",
            status=status,
            reason=f"external_token_env {external_token_env} is {state}",
            details={
                "mechanism": "external_token_env",
                "env_var": external_token_env,
                "env_set": is_set,
            },
        )

    return HopResult(
        number=3,
        title="auth",
        status=HopStatus.PASS,
        reason="workload identity token exchange",
        details={
            "mechanism": "workload_identity",
            "workload_token_file": str(kwargs.get("workload_token_file") or ""),
            "workload_exchange_url": str(kwargs.get("workload_exchange_url") or ""),
        },
    )


# ---------------------------------------------------------------------------
# Hop 4 — reachability
# ---------------------------------------------------------------------------


async def _hop4_reachability(backend: Any, kind: str) -> HopResult:
    base_url = _backend_base_url(backend)
    if not base_url:
        return HopResult(
            number=4,
            title="reachability",
            status=HopStatus.FAIL,
            reason="backend has no base_url to probe",
        )

    client = _backend_client(backend)
    if client is None:
        return HopResult(
            number=4,
            title="reachability",
            status=HopStatus.FAIL,
            reason="backend exposes no HTTP client to probe",
        )

    if kind == "a2a":
        # base_url IS the card URL for the A2A backend — probing it verifies
        # discovery end to end (DNS, TLS, auth headers, card route).
        url = base_url
    else:
        path = _TING_WORKFLOWS_PATH if kind == "ting" else _FORGE_HEALTH_PATH
        url = f"{base_url}{path}"
    try:
        response = await client.get(url)
    except Exception as exc:  # noqa: BLE001 — doctor must never throw
        return HopResult(
            number=4,
            title="reachability",
            status=HopStatus.FAIL,
            reason=f"GET {url} raised {type(exc).__name__}: {exc}",
            details={"url": url},
        )

    status_code = getattr(response, "status_code", 0)
    if 200 <= status_code < 300:
        return HopResult(
            number=4,
            title="reachability",
            status=HopStatus.PASS,
            reason=f"GET {url} -> HTTP {status_code}",
            details={"url": url, "status_code": status_code},
        )
    return HopResult(
        number=4,
        title="reachability",
        status=HopStatus.FAIL,
        reason=f"GET {url} -> HTTP {status_code}",
        details={"url": url, "status_code": status_code},
    )


# ---------------------------------------------------------------------------
# Hop 5 — workflow discovery (Ting only)
# ---------------------------------------------------------------------------


async def _hop5_workflow(backend: Any, kind: str, hop4: HopResult) -> HopResult:
    if kind == "forge":
        return _skip(5, "workflow discovery", "not applicable to the forge_session backend")

    workflow_id = getattr(backend, "workflow_id", "")
    if workflow_id:
        return HopResult(
            number=5,
            title="workflow discovery",
            status=HopStatus.PASS,
            reason=f"workflow_id={workflow_id} configured directly",
            details={"workflow_id": workflow_id},
        )

    selector = getattr(backend, "workflow_selector", None)
    if not isinstance(selector, WorkflowSelector) or not selector.configured:
        return HopResult(
            number=5,
            title="workflow discovery",
            status=HopStatus.FAIL,
            reason="no workflow_id and no workflow selector (names/tags) configured",
        )

    if hop4.status is not HopStatus.PASS:
        return _skip(5, "workflow discovery", "reachability failed; cannot list workflows")

    if kind == "a2a":
        workflows = await _list_a2a_skills(backend)
        source = "agent card"
    else:
        workflows = await _list_ting_workflows(backend)
        source = f"GET {_TING_WORKFLOWS_PATH}"
    if workflows is None:
        return HopResult(
            number=5,
            title="workflow discovery",
            status=HopStatus.FAIL,
            reason=f"{source} did not return a workflow list",
        )

    matches = [wf for wf in workflows if selector.matches(wf)]
    candidates = [wf.name or wf.workflow_id for wf in matches]
    if len(matches) == 1:
        chosen = select_workflow(selector, workflows)
        name = chosen.name or chosen.workflow_id if chosen is not None else candidates[0]
        return HopResult(
            number=5,
            title="workflow discovery",
            status=HopStatus.PASS,
            reason=f"selector resolves to one workflow: {name}",
            details={"candidates": candidates},
        )

    if not matches:
        available = [wf.name or wf.workflow_id for wf in workflows]
        return HopResult(
            number=5,
            title="workflow discovery",
            status=HopStatus.FAIL,
            reason=f"selector matched zero workflows (available: {available})",
            details={"candidates": [], "available": available},
        )

    return HopResult(
        number=5,
        title="workflow discovery",
        status=HopStatus.FAIL,
        reason=f"selector matched {len(matches)} workflows: {candidates}",
        details={"candidates": candidates},
    )


async def _list_a2a_skills(backend: Any) -> list[WorkflowCapability] | None:
    card_url = str(getattr(backend, "card_url", "") or "")
    client = _backend_client(backend)
    if not card_url or client is None:
        return None
    try:
        response = await client.get(card_url)
    except Exception:  # noqa: BLE001 — doctor must never throw
        return None
    if getattr(response, "status_code", 0) != 200:
        return None
    body = getattr(response, "body", None)
    if not isinstance(body, dict):
        return None
    skills = body.get("skills")
    if not isinstance(skills, list):
        return None
    # The one skill mapper lives in the A2A backend — the doctor must see
    # exactly the capabilities the backend it diagnoses sees.
    from ravn.adapters.tool_build.a2a import _skill_capability  # noqa: PLC0415

    return [_skill_capability(skill) for skill in skills]


async def _list_ting_workflows(backend: Any) -> list[WorkflowCapability] | None:
    base_url = _backend_base_url(backend)
    client = _backend_client(backend)
    if not base_url or client is None:
        return None
    try:
        response = await client.get(f"{base_url}{_TING_WORKFLOWS_PATH}")
    except Exception:  # noqa: BLE001 — doctor must never throw
        return None
    if getattr(response, "status_code", 0) != 200:
        return None
    body = getattr(response, "body", None)
    if not isinstance(body, list):
        return None
    # The one catalog mapper lives in the Ting backend — the doctor must see
    # exactly the workflows (including metadata) the backend it diagnoses sees.
    from ravn.adapters.tool_build.ting_workflow import _workflow_from_body  # noqa: PLC0415

    return [_workflow_from_body(item) for item in body if isinstance(item, dict)]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _skip(number: int, title: str, reason: str) -> HopResult:
    return HopResult(number=number, title=title, status=HopStatus.SKIP, reason=reason)


def _backend_kind(backend: Any) -> str:
    name = str(getattr(backend, "name", "") or "").lower()
    if name == "a2a" or "A2A" in type(backend).__name__:
        return "a2a"
    if "ting" in name or "TingWorkflow" in type(backend).__name__:
        return "ting"
    return "forge"


def _backend_base_url(backend: Any) -> str:
    base = str(getattr(backend, "base_url", "") or "")
    if base:
        return base
    # The A2A backend is addressed by its agent-card URL, not a base URL.
    return str(getattr(backend, "card_url", "") or "")


def _backend_client(backend: Any) -> Any | None:
    return getattr(backend, "client", None)
