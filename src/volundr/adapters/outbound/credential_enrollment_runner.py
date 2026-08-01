"""Configured trusted runtimes for interactive credential enrollment."""

from __future__ import annotations

from volundr.domain.models import CredentialEnrollment, CredentialEnrollmentPoll
from volundr.domain.ports import CredentialEnrollmentRunnerPort


class UnsupportedCredentialEnrollmentRunner(CredentialEnrollmentRunnerPort):
    """Local/default adapter that deliberately does not launch a login runtime."""

    def __init__(self, **_extra: object) -> None:
        pass

    def supports_enrollment(self, method: str) -> bool:
        del method
        return False

    async def start_enrollment(self, enrollment: CredentialEnrollment) -> CredentialEnrollment:
        del enrollment
        raise ValueError("interactive credential enrollment is not configured")

    async def poll_enrollment(
        self,
        enrollment: CredentialEnrollment,
    ) -> CredentialEnrollmentPoll:
        del enrollment
        raise ValueError("interactive credential enrollment is not configured")

    async def cancel_enrollment(self, enrollment: CredentialEnrollment) -> None:
        del enrollment


class OpenShellCredentialEnrollmentRunner(CredentialEnrollmentRunnerPort):
    """Use a dedicated OpenShell adapter instance solely as the trusted login runner."""

    def __init__(self, **kwargs: object) -> None:
        from volundr.adapters.outbound.openshell_gateway import OpenShellGatewayPodManager

        self._runner = OpenShellGatewayPodManager(**kwargs)

    def supports_enrollment(self, method: str) -> bool:
        return self._runner.supports_enrollment(method)

    async def start_enrollment(self, enrollment: CredentialEnrollment) -> CredentialEnrollment:
        return await self._runner.start_enrollment(enrollment)

    async def poll_enrollment(
        self,
        enrollment: CredentialEnrollment,
    ) -> CredentialEnrollmentPoll:
        return await self._runner.poll_enrollment(enrollment)

    async def cancel_enrollment(self, enrollment: CredentialEnrollment) -> None:
        await self._runner.cancel_enrollment(enrollment)
