"""REST routes for registered hosts: admin management, and the two routes a joining host calls.

Admin routes need ``volundr:admin``. ``join.sh`` and ``join`` are authenticated by the
host's pairing code: a scoped workload JWT (``compute_host_join``) whose hash must be a
usable code of this pool, so no route here is open without a credential.
"""

from __future__ import annotations

import hashlib
import shlex
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from niuu.domain.services.token_scope import COMPUTE_HOST_JOIN_SCOPE, require_scope
from volundr.adapters.inbound.auth import extract_principal, require_role
from volundr.adapters.inbound.host_join_script import render_join_script
from volundr.domain.compute import ComputeSlot
from volundr.domain.compute_hosts import RegisteredHost
from volundr.domain.models import Principal
from volundr.domain.services.compute_hosts import (
    JOIN_SCRIPT_FILENAME,
    ComputeHostAccessError,
    ComputeHostError,
    ComputeHostService,
    ComputeHostStateError,
    PairingCodeRejectedError,
    key_fingerprint,
)
from volundr.domain.services.compute_pool import ComputePoolService

PREFIX = "/api/v1/forge/compute/hosts"


class PairingCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution: str = Field(min_length=1)
    connector: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    slots: int | None = Field(default=None, ge=1)


class HostUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tags: list[str] | None = None
    slots: int | None = Field(default=None, ge=1)
    name: str | None = None


class DrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remove: bool = False


class JoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    public_key: str = Field(min_length=1)
    address: str = ""
    facts: dict[str, JsonValue] = Field(default_factory=dict)


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "A host pairing code is required")
    return token.strip()


def _http(exc: ComputeHostError | LookupError) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, PairingCodeRejectedError):
        return HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))
    if isinstance(exc, ComputeHostAccessError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, ComputeHostStateError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def create_host_join_router(service: ComputeHostService) -> APIRouter:
    """The two routes a joining host calls: on Forge's API, or alone on the join listener."""
    router = APIRouter(prefix=PREFIX, tags=["Compute hosts"])

    @router.get(
        "/join.sh",
        response_class=Response,
        dependencies=[Depends(require_scope(COMPUTE_HOST_JOIN_SCOPE))],
    )
    async def join_script(request: Request) -> Response:
        code = _bearer(request)
        try:
            await service.usable_code(code)
        except ComputeHostError as exc:
            raise _http(exc) from exc
        script = render_join_script(
            forge_url=service.join_base_url, code=code, pin=service.join_pin
        )
        return Response(
            content=script,
            media_type="text/x-shellscript",
            headers={"Cache-Control": "no-store"},
        )

    @router.post(
        "/join",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_scope(COMPUTE_HOST_JOIN_SCOPE))],
    )
    async def join(body: JoinRequest, request: Request) -> dict[str, Any]:
        try:
            joined = await service.join(
                _bearer(request),
                name=body.name,
                public_key=body.public_key,
                address=body.address,
                facts=body.facts,
            )
        except ComputeHostError as exc:
            raise _http(exc) from exc
        return {
            "host_id": str(joined.host.id),
            "name": joined.host.name,
            "pool_id": joined.host.pool_id,
            "ssh_user": joined.ssh_user,
            "forge_public_key": joined.forge_public_key,
            "authorized_key_options": joined.authorized_key_options,
            "setup": {
                "files": [dict(item) for item in joined.setup_files],
                "commands": [list(command) for command in joined.setup_commands],
            },
        }

    return router


def create_compute_hosts_router(service: ComputeHostService, pool: ComputePoolService) -> APIRouter:
    """Admin management; the host-facing routes are ``create_host_join_router``."""
    router = APIRouter(prefix=PREFIX, tags=["Compute hosts"])
    admin = Depends(require_role("volundr:admin"))

    async def view(host: RegisteredHost) -> dict[str, Any]:
        sessions = await service.live_sessions(host)
        execution = service.executions.get(host.execution)
        connector = service.connectors.get(host.connector)
        connection = (await connector.status(host)).model_dump(mode="json") if connector else None
        suggestion = service.suggestion(host)
        used = {binding.slot for binding in sessions}
        return {
            "id": str(host.id),
            "name": host.name,
            "tags": list(host.tags),
            "state": host.display_state().value,
            "health": host.health.value,
            "service": host.service.value,
            "remove_when_empty": host.service.value == "removing",
            "execution": host.execution,
            "runtime": execution.runtime.value if execution and execution.runtime else None,
            "connector": host.connector,
            "connection": (
                {"kind": connector.kind, **connection} if connector and connection else None
            ),
            "slots": host.slots,
            "sessions": [_session(binding) for binding in sessions],
            "slots_free": (
                max(0, host.slots - len(used)) if host.slots is not None and host.accepting else 0
            ),
            "facts": host.facts,
            "suggested_slots": (
                {"slots": suggestion[0], "lines": list(suggestion[1])} if suggestion else None
            ),
            "findings": list(host.findings),
            "host_key_fingerprint": key_fingerprint(host.host_public_key),
            "registered_at": host.registered_at.isoformat(),
            "registered_by": host.registered_by,
            "last_checked_at": host.last_checked_at.isoformat() if host.last_checked_at else None,
        }

    def pairing_view(pairing) -> dict[str, Any]:
        return {
            "id": str(pairing.id),
            "execution": pairing.execution,
            "connector": pairing.connector,
            "tags": list(pairing.tags),
            "slots": pairing.slots,
            "expires_at": pairing.expires_at.isoformat(),
            "created_by": pairing.created_by,
            "consumed_by_host_id": (
                str(pairing.consumed_by_host_id) if pairing.consumed_by_host_id else None
            ),
        }

    @router.get("", dependencies=[admin])
    async def list_hosts() -> dict[str, Any]:
        hosts = await service.hosts()
        views = [await view(host) for host in hosts]
        policy = await pool.policy()
        free_by_tags: dict[tuple[str, ...], int] = {}
        for host, shown in zip(hosts, views, strict=True):
            free_by_tags[host.tags] = free_by_tags.get(host.tags, 0) + shown["slots_free"]
        by_state: dict[str, int] = {}
        for shown in views:
            by_state[shown["state"]] = by_state.get(shown["state"], 0) + 1
        slots_free = sum(shown["slots_free"] for shown in views)
        return {
            "pool_id": service.pool_id,
            "hosts": views,
            "summary": {
                "hosts": len(hosts),
                "by_state": by_state,
                "slots_total": sum(host.slots or 0 for host in hosts),
                "slots_free": slots_free,
                "free_by_tag_set": [
                    {"tags": list(tags), "free": free}
                    for tags, free in sorted(free_by_tags.items())
                ],
                "warm_targets": [
                    {
                        "tags": [],
                        "match": "all",
                        "min": policy.warm_min,
                        "free": slots_free,
                        "met": slots_free >= policy.warm_min,
                    }
                ],
                "capacity_shortfall": pool.capacity_shortfall,
                "last_error": service.last_error,
            },
            "connectors": [
                {"binding_id": binding_id, "kind": connector.kind, "label": connector.label}
                for binding_id, connector in service.connectors.items()
            ],
            "runtimes": [
                {
                    "execution": execution.execution,
                    "runtime": execution.runtime.value if execution.runtime else None,
                    "label": execution.label,
                    "exclusive_only": execution.exclusive_only,
                    "platforms": sorted(execution.platforms),
                    "allowed": execution.refusal is None,
                    "refusal": execution.refusal,
                }
                for execution in service.executions.values()
            ],
            "pairing_codes": [pairing_view(p) for p in await service.pairing_codes()],
        }

    @router.get("/tag-sets")
    async def tag_sets(_: Principal = Depends(extract_principal)) -> dict[str, Any]:
        """Tag sets of hosts that can take sessions, for Guild and Ting targeting."""
        sets = await pool.provider.tag_sets()
        return {"tag_sets": [list(tags) for tags in sets]}

    @router.post("/pairing-codes", status_code=status.HTTP_201_CREATED)
    async def mint(
        body: PairingCodeRequest, principal: Principal = Depends(extract_principal)
    ) -> dict[str, Any]:
        try:
            minted = await service.mint(
                principal,
                execution=body.execution,
                connector=body.connector,
                tags=body.tags,
                slots=body.slots,
            )
        except ComputeHostError as exc:
            raise _http(exc) from exc
        # The same bytes join.sh serves for this code, so the digest checks either download.
        script = render_join_script(
            forge_url=service.join_base_url, code=minted.code, pin=service.join_pin
        )
        digest = hashlib.sha256(script.encode()).hexdigest()
        return {
            **pairing_view(minted.pairing),
            "code": minted.code,
            "command": minted.command,
            "command_wget": minted.command_wget,
            "join_url": service.join_base_url,
            "pin": service.join_pin,
            "script": {
                "filename": JOIN_SCRIPT_FILENAME,
                "sha256": digest,
                "content": script,
                "commands": [
                    minted.command_download,
                    f"echo {shlex.quote(f'{digest}  {JOIN_SCRIPT_FILENAME}')} | sha256sum -c -",
                    f"less {JOIN_SCRIPT_FILENAME}",
                    f"sudo sh {JOIN_SCRIPT_FILENAME}",
                ],
            },
        }

    @router.delete("/pairing-codes/{code_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def revoke(code_id: UUID, principal: Principal = Depends(extract_principal)) -> Response:
        try:
            await service.revoke(principal, code_id)
        except ComputeHostError as exc:
            raise _http(exc) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.patch("/{host_id}")
    async def update(
        host_id: UUID, body: HostUpdateRequest, principal: Principal = Depends(extract_principal)
    ) -> dict[str, Any]:
        try:
            host = await service.update(
                principal, host_id, tags=body.tags, slots=body.slots, name=body.name
            )
        except (ComputeHostError, LookupError) as exc:
            raise _http(exc) from exc
        return await view(host)

    @router.post("/{host_id}/drain")
    async def drain(
        host_id: UUID, body: DrainRequest, principal: Principal = Depends(extract_principal)
    ) -> dict[str, Any]:
        try:
            host = await service.drain(principal, host_id, remove=body.remove)
        except (ComputeHostError, LookupError) as exc:
            raise _http(exc) from exc
        return await view(host)

    @router.post("/{host_id}/resume")
    async def resume(
        host_id: UUID, principal: Principal = Depends(extract_principal)
    ) -> dict[str, Any]:
        try:
            host = await service.resume(principal, host_id)
        except (ComputeHostError, LookupError) as exc:
            raise _http(exc) from exc
        return await view(host)

    @router.post("/{host_id}/reprepare")
    async def reprepare(
        host_id: UUID, principal: Principal = Depends(extract_principal)
    ) -> dict[str, Any]:
        try:
            host = await service.reprepare(principal, host_id)
        except (ComputeHostError, LookupError) as exc:
            raise _http(exc) from exc
        return await view(host)

    @router.post("/{host_id}/check")
    async def check(
        host_id: UUID, principal: Principal = Depends(extract_principal)
    ) -> dict[str, Any]:
        try:
            host = await service.check_now(principal, host_id)
        except (ComputeHostError, LookupError) as exc:
            raise _http(exc) from exc
        return await view(host)

    @router.delete("/{host_id}")
    async def remove(
        host_id: UUID, principal: Principal = Depends(extract_principal)
    ) -> dict[str, Any]:
        try:
            cleaned = await service.remove(principal, host_id)
        except (ComputeHostError, LookupError) as exc:
            raise _http(exc) from exc
        return {
            "removed": True,
            "host_cleanup_command": None if cleaned else "sudo niuu-host-leave",
        }

    return router


def _session(binding: ComputeSlot) -> dict[str, Any]:
    return {
        "slot": binding.slot,
        "session_id": str(binding.session_id),
        "state": binding.state.value,
    }
