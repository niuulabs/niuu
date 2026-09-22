"""Resolve exact catalog selections into durable compute execution plans."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from volundr.domain.execution_catalog import (
    AccessConfiguration,
    CatalogRef,
    ExecutionCatalogIntegrityError,
    ExecutionCatalogNotFoundError,
    ExecutionCatalogSnapshot,
    ExecutionPlanMismatchError,
    ExecutionProfile,
    HostRecipe,
    MachineProfile,
    ResolvedExecutionPlan,
    RuntimeProfile,
)
from volundr.ports.execution_catalog import ExecutionCatalogPort


def canonical_digest(value: Any) -> str:
    """Return a stable SHA256 digest for immutable catalog data."""
    normalized = _canonical_value(value)
    encoded = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"))
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


class ExecutionPlanResolver:
    """Resolve only exact approved compositions; never infer a latest revision."""

    def __init__(self, catalog: ExecutionCatalogPort) -> None:
        self._catalog = catalog

    def resolve(self, selection: str | CatalogRef | None = None) -> ResolvedExecutionPlan:
        snapshot = self._catalog.load()
        reference = self._selection(selection, snapshot)
        return self._resolve(snapshot, reference)

    def resolve_pinned(
        self,
        selection: str | CatalogRef,
        expected_plan_digest: str,
    ) -> ResolvedExecutionPlan:
        """Reload exact data and fail if config or artifact content changed."""
        try:
            plan = self.resolve(selection)
        except (ExecutionCatalogNotFoundError, ExecutionCatalogIntegrityError) as exc:
            raise ExecutionPlanMismatchError(
                "Pinned execution revision is unavailable or has changed"
            ) from exc
        if plan.plan_digest != expected_plan_digest:
            raise ExecutionPlanMismatchError(
                f"Execution plan {plan.execution} changed: expected digest "
                f"{expected_plan_digest}, loaded {plan.plan_digest}"
            )
        return plan

    def resolve_legacy_compute_profile(self, name: str) -> ResolvedExecutionPlan:
        """Translate a legacy compute profile only through an explicit mapping."""
        snapshot = self._catalog.load()
        reference = snapshot.legacy_compute_profiles.get(name)
        if reference is None:
            raise ExecutionCatalogNotFoundError(
                f"Legacy compute profile has no execution catalog mapping: {name!r}"
            )
        return self._resolve(snapshot, reference)

    def plans(self) -> tuple[ResolvedExecutionPlan, ...]:
        """Resolve every explicitly versioned execution in a single snapshot."""
        return self.resolve_all()[1]

    def resolve_all(
        self,
    ) -> tuple[ResolvedExecutionPlan, tuple[ResolvedExecutionPlan, ...]]:
        """Resolve the default and every plan from exactly one catalog read."""
        snapshot = self._catalog.load()
        plans = tuple(
            self._resolve(snapshot, execution.ref)
            for execution in sorted(snapshot.executions, key=lambda item: (item.id, item.revision))
        )
        default = next(plan for plan in plans if plan.execution == snapshot.default_execution)
        return default, plans

    @staticmethod
    def _selection(
        selection: str | CatalogRef | None,
        snapshot: ExecutionCatalogSnapshot,
    ) -> CatalogRef:
        if selection is None:
            return snapshot.default_execution
        if isinstance(selection, CatalogRef):
            return selection
        try:
            return CatalogRef.parse(selection)
        except ValueError as exc:
            raise ExecutionCatalogNotFoundError(str(exc)) from exc

    def _resolve(
        self,
        snapshot: ExecutionCatalogSnapshot,
        reference: CatalogRef,
    ) -> ResolvedExecutionPlan:
        execution = self._require(snapshot.executions, reference, "execution")
        machine = self._require(snapshot.machines, execution.machine, "machine")
        host_recipe = self._require(
            snapshot.host_recipes,
            execution.host_recipe,
            "host recipe",
        )
        access = self._require(
            snapshot.access_configurations,
            execution.access,
            "access configuration",
        )
        runtime = self._require(snapshot.runtimes, execution.runtime, "runtime")
        return self._build_plan(execution, machine, host_recipe, access, runtime)

    @staticmethod
    def _require(entries: tuple, reference: CatalogRef, kind: str):
        for entry in entries:
            if entry.id == reference.id and entry.revision == reference.revision:
                return entry
        raise ExecutionCatalogNotFoundError(f"Unknown {kind} catalog reference: {reference}")

    @staticmethod
    def _build_plan(
        execution: ExecutionProfile,
        machine: MachineProfile,
        host_recipe: HostRecipe,
        access: AccessConfiguration,
        runtime: RuntimeProfile,
    ) -> ResolvedExecutionPlan:
        host_requirements = dict(machine.host_requirements)
        for name, value in runtime.host_requirements.items():
            current = host_requirements.get(name)
            if current is not None and current != value:
                raise ExecutionCatalogIntegrityError(
                    f"Machine and runtime require conflicting host value for {name!r}"
                )
            host_requirements[name] = value
        host_digest = canonical_digest(
            {
                "machine": machine,
                "host_recipe": _portable_recipe(host_recipe),
                "access": access,
                "host_requirements": host_requirements,
            }
        )
        session_digest = canonical_digest(
            {
                "runtime": runtime,
                "contributor_backend": runtime.contributor_backend,
                "storage_mode": runtime.storage_mode,
                "capabilities": runtime.capabilities,
            }
        )
        digest_payload = {
            "plan_version": 1,
            "execution": execution.ref,
            "pool_id": execution.pool_id,
            "provider_binding": execution.provider_binding,
            "provider_profile": machine.provider_profile,
            "machine": machine,
            "host_recipe": _portable_recipe(host_recipe),
            "access": access,
            "runtime": runtime,
            "host_recipe_digest": host_recipe.digest,
            "host_compatibility_digest": host_digest,
            "session_execution_digest": session_digest,
        }
        return ResolvedExecutionPlan(
            plan_version=1,
            execution=execution.ref,
            plan_digest=canonical_digest(digest_payload),
            pool_id=execution.pool_id,
            provider_binding=execution.provider_binding,
            provider_profile=machine.provider_profile,
            machine=machine,
            host_recipe=host_recipe,
            access=access,
            runtime=runtime,
            host_recipe_digest=host_recipe.digest,
            host_compatibility_digest=host_digest,
            session_execution_digest=session_digest,
        )


def _portable_recipe(recipe: HostRecipe) -> dict[str, Any]:
    """Exclude installation-specific absolute paths from durable identities."""
    payload = recipe.model_dump(mode="python")
    for artifact in payload["artifacts"]:
        artifact.pop("resolved_path")
    return payload
