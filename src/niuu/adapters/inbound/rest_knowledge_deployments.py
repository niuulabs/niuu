"""Discover and route knowledge deployments through visible Guild Mimir services.

Each service owns its Flux/local targets and credentials. Guild forwards the
caller's identity, never accepts a deployment URL or Kubernetes credentials.
"""

import asyncio
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict

from niuu.adapters.identity_headers import parse_roles_header
from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.remote_urls import build_remote_url, forward_identity_headers
from niuu.domain.models import InstanceKind, Principal, RegisteredInstance
from niuu.domain.services.instances import InstanceService


class _Target(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    cluster: str
    namespace: str
    backends: list[str]
    releases: list[dict]


def _require_admin(request: Request) -> None:
    roles = parse_roles_header(request.headers.get("x-auth-roles", ""))
    if not request.headers.get("x-auth-user-id") or not {"admin", "volundr:admin"}.intersection(
        roles
    ):
        raise HTTPException(403, "Instance deployment requires an authenticated administrator")


def _deployment_url(instance: RegisteredInstance, path: str) -> str:
    base_path = urlsplit(instance.base_url).path.rstrip("/")
    prefix = "/api/v1/mimir"
    if base_path.endswith("/api/v1/mimir"):
        prefix = ""
    elif base_path.endswith("/api/v1"):
        prefix = "/mimir"
    return build_remote_url(instance.base_url, prefix, path)


def create_knowledge_deployments_router(
    service: InstanceService, *, timeout_seconds: float = 30.0
) -> APIRouter:
    router = APIRouter(prefix="/knowledge", dependencies=[Depends(_require_admin)])

    async def call(instance, request, method, path, *, body=None, params=None):
        try:
            url = _deployment_url(instance, path)
            async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
                response = await client.request(
                    method, url, headers=forward_identity_headers(request), json=body, params=params
                )
        except (httpx.RequestError, ValueError) as exc:
            raise HTTPException(502, f"{instance.name}: {exc}") from exc
        if response.status_code >= 300:
            try:
                message = response.json().get("detail", response.reason_phrase)
            except (ValueError, AttributeError):
                message = response.reason_phrase
            raise HTTPException(response.status_code, f"{instance.name}: {message}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise HTTPException(502, f"{instance.name}: invalid deployment response") from exc
        if not isinstance(payload, dict):
            raise HTTPException(502, f"{instance.name}: invalid deployment response")
        return payload

    async def resolve(principal, target):
        source_id, separator, local_target = target.partition(":")
        if not source_id or not separator:
            raise HTTPException(422, "Select a deployment target from Guild")
        instance = await service.get_visible(principal, source_id)
        if instance is None or not instance.enabled or instance.kind != InstanceKind.MIMIR:
            raise HTTPException(404, "Deployment service is not available in your Guild")
        return instance, local_target

    @router.get("/deployments")
    async def deployments(request: Request, principal: Principal = Depends(extract_principal)):
        instances = await service.list_visible(
            principal, kind=InstanceKind.MIMIR, enabled_only=True
        )

        async def discover(instance):
            try:
                status = await call(instance, request, "GET", "/deployments")
                targets = status.get("targets", [{**status, "id": status.get("target", "")}])
                result = []
                for target in targets:
                    target = _Target.model_validate(target).model_dump()
                    target_id = f"{instance.id}:{target['id']}"
                    result.append(
                        {
                            **target,
                            "id": target_id,
                            "source_id": instance.id,
                            "source_name": instance.name,
                            "releases": [
                                {**release, "target": target_id} for release in target["releases"]
                            ],
                        }
                    )
                return result
            except (HTTPException, KeyError, TypeError, ValueError) as exc:
                return [
                    {
                        "id": f"{instance.id}:",
                        "source_id": instance.id,
                        "source_name": instance.name,
                        "cluster": instance.name,
                        "namespace": "",
                        "backends": [],
                        "releases": [],
                        "error": str(exc.detail)
                        if isinstance(exc, HTTPException)
                        else "Invalid deployment target response",
                    }
                ]

        groups = await asyncio.gather(*(discover(instance) for instance in instances))
        targets = [target for group in groups for target in group]
        return {
            "cluster": "",
            "namespace": "",
            "backends": [],
            "targets": targets,
            "releases": [release for target in targets for release in target["releases"]],
        }

    @router.post("/deployments", status_code=202)
    async def deploy(
        request: Request, body: dict, principal: Principal = Depends(extract_principal)
    ):
        target = body.get("target")
        if not isinstance(target, str):
            raise HTTPException(422, "Select a deployment target from Guild")
        instance, local_target = await resolve(principal, target)
        return await call(
            instance, request, "POST", "/deployments", body={**body, "target": local_target}
        )

    @router.get("/deployments/{name}")
    async def inspect(
        request: Request, name: str, target: str, principal: Principal = Depends(extract_principal)
    ):
        instance, local_target = await resolve(principal, target)
        return await call(
            instance,
            request,
            "GET",
            f"/deployments/{quote(name, safe='')}",
            params={"target": local_target},
        )

    @router.post("/deployments/{name}/{action}")
    async def control(
        request: Request,
        action: str,
        target: str,
        name: str = Path(pattern=r"^[a-z][a-z0-9-]{0,39}$"),
        principal: Principal = Depends(extract_principal),
    ):
        if action not in {"start", "stop", "delete", "update"}:
            raise HTTPException(422, "Unsupported deployment action")
        instance, local_target = await resolve(principal, target)
        return await call(
            instance,
            request,
            "POST",
            f"/deployments/{quote(name, safe='')}/{action}",
            params={"target": local_target},
        )

    return router
