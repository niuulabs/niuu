"""Port for Ravn's authenticated reads from the target platform API."""

from __future__ import annotations

from typing import Any, Protocol


class PlatformRuntimePort(Protocol):
    """Forge session and resident-runtime reads needed by the Ravn product API."""

    async def list_forge_sessions(
        self,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Return Forge sessions visible to the caller."""
        raise NotImplementedError

    async def get_forge_session(
        self,
        session_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any] | None:
        """Return one caller-visible Forge session."""
        raise NotImplementedError

    async def list_resident_runtimes(
        self,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Return durable residents visible to the caller."""
        raise NotImplementedError

    async def get_resident_runtime(
        self,
        runtime_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any] | None:
        """Return one caller-visible durable resident."""
        raise NotImplementedError

    async def list_resident_profiles(
        self,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Return resident profiles enabled on this target."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release transport resources."""
        raise NotImplementedError
