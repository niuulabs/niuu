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

    async def create_resident_runtime(
        self,
        body: dict[str, Any],
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any]:
        """Deploy one resident through the target control plane."""
        raise NotImplementedError

    async def control_resident_runtime(
        self,
        runtime_id: str,
        action: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any]:
        """Apply one lifecycle action to a target resident."""
        raise NotImplementedError

    async def delete_resident_runtime(
        self,
        runtime_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> None:
        """Delete one resident and its owned backend resources."""
        raise NotImplementedError

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
        """Return normalized backend logs for one resident."""
        raise NotImplementedError

    async def list_resident_sessions(
        self,
        runtime_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Return native sessions owned by one resident engine."""
        raise NotImplementedError

    async def create_resident_session(
        self,
        runtime_id: str,
        body: dict[str, Any],
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any]:
        """Create one native session in a resident engine."""
        raise NotImplementedError

    async def delete_resident_session(
        self,
        runtime_id: str,
        session_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> None:
        """Delete one native resident session."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release transport resources."""
        raise NotImplementedError
