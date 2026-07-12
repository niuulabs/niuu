"""HTTP adapter for Ravn reads from its target Volundr platform API."""

from __future__ import annotations

import re
from typing import Any

import httpx

_DEFAULT_PLATFORM_API_URL = "http://localhost:8080"
_DEFAULT_TIMEOUT_SECONDS = 5.0


def _path_segment(value: str) -> str:
    """Validate an engine-provided identifier as exactly one URL path segment."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError(f"Invalid platform resource identifier: {value!r}")
    return value


class HttpPlatformRuntimeAdapter:
    """One pooled authenticated client for Forge and resident runtime reads."""

    def __init__(
        self,
        *,
        base_url: str = "",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=(base_url or _DEFAULT_PLATFORM_API_URL).rstrip("/"),
            timeout=timeout_seconds,
        )

    async def list_forge_sessions(
        self,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        return await self._get_list("/api/v1/forge/sessions", auth_headers, auth_params)

    async def get_forge_session(
        self,
        session_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any] | None:
        return await self._get_item(
            f"/api/v1/forge/sessions/{_path_segment(session_id)}",
            auth_headers,
            auth_params,
        )

    async def stop_forge_session(
        self,
        session_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any] | None:
        response = await self._client.post(
            f"/api/v1/forge/sessions/{_path_segment(session_id)}/stop",
            headers=auth_headers,
            params=auth_params,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Platform Forge stop returned an unexpected payload")
        return payload

    async def list_resident_runtimes(
        self,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        return await self._get_list(
            "/api/v1/forge/resident-runtimes",
            auth_headers,
            auth_params,
        )

    async def get_resident_runtime(
        self,
        runtime_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any] | None:
        return await self._get_item(
            f"/api/v1/forge/resident-runtimes/{_path_segment(runtime_id)}",
            auth_headers,
            auth_params,
        )

    async def list_resident_profiles(
        self,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        return await self._get_list(
            "/api/v1/forge/resident-profiles",
            auth_headers,
            auth_params,
        )

    async def create_resident_runtime(
        self,
        body: dict[str, Any],
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any]:
        return await self._post_item(
            "/api/v1/forge/resident-runtimes",
            body,
            auth_headers,
            auth_params,
        )

    async def control_resident_runtime(
        self,
        runtime_id: str,
        action: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any]:
        if action not in {"restart", "suspend", "resume"}:
            raise ValueError(f"Unsupported resident lifecycle action: {action}")
        return await self._post_item(
            f"/api/v1/forge/resident-runtimes/{_path_segment(runtime_id)}/{action}",
            None,
            auth_headers,
            auth_params,
        )

    async def delete_resident_runtime(
        self,
        runtime_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> None:
        response = await self._client.delete(
            f"/api/v1/forge/resident-runtimes/{_path_segment(runtime_id)}",
            headers=auth_headers,
            params=auth_params,
        )
        response.raise_for_status()

    async def get_resident_logs(
        self,
        runtime_id: str,
        *,
        lines: int,
        sources: tuple[str, ...],
        min_level: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any]:
        params: list[tuple[str, str]] = [
            *[(key, value) for key, value in auth_params.items()],
            ("lines", str(lines)),
            *[("source", source) for source in sources],
        ]
        if min_level:
            params.append(("min_level", min_level))
        response = await self._client.get(
            f"/api/v1/forge/resident-runtimes/{_path_segment(runtime_id)}/logs",
            headers=auth_headers,
            params=params,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Platform resident logs returned an unexpected payload")
        return payload

    async def list_resident_sessions(
        self,
        runtime_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        return await self._get_list(
            f"/api/v1/forge/resident-runtimes/{_path_segment(runtime_id)}/sessions",
            auth_headers,
            auth_params,
        )

    async def create_resident_session(
        self,
        runtime_id: str,
        body: dict[str, Any],
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any]:
        return await self._post_item(
            f"/api/v1/forge/resident-runtimes/{_path_segment(runtime_id)}/sessions",
            body,
            auth_headers,
            auth_params,
        )

    async def delete_resident_session(
        self,
        runtime_id: str,
        session_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> None:
        response = await self._client.delete(
            "/api/v1/forge/resident-runtimes/"
            f"{_path_segment(runtime_id)}/sessions/{_path_segment(session_id)}",
            headers=auth_headers,
            params=auth_params,
        )
        response.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_list(
        self,
        path: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        payload = await self._get_json(path, auth_headers, auth_params)
        if not isinstance(payload, list):
            raise RuntimeError(
                f"Platform API {path} returned unexpected payload: {type(payload).__name__}"
            )
        return [item for item in payload if isinstance(item, dict)]

    async def _get_item(
        self,
        path: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any] | None:
        response = await self._client.get(path, headers=auth_headers, params=auth_params)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"Platform API {path} returned unexpected payload: {type(payload).__name__}"
            )
        return payload

    async def _post_item(
        self,
        path: str,
        body: dict[str, Any] | None,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any]:
        response = await self._client.post(
            path,
            json=body,
            headers=auth_headers,
            params=auth_params,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"Platform API {path} returned unexpected payload: {type(payload).__name__}"
            )
        return payload

    async def _get_json(
        self,
        path: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> Any:
        response = await self._client.get(path, headers=auth_headers, params=auth_params)
        response.raise_for_status()
        return response.json()
