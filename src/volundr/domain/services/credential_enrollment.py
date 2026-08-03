"""User-scoped interactive credential enrollment orchestration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from niuu.domain.models import SecretType
from volundr.domain.models import (
    CredentialEnrollment,
    CredentialEnrollmentPoll,
    CredentialEnrollmentState,
    IntegrationConnection,
    Principal,
)
from volundr.domain.ports import (
    CredentialEnrollmentRepository,
    CredentialEnrollmentRunnerPort,
    CredentialStorePort,
    IntegrationRepository,
)
from volundr.domain.services.integration_registry import IntegrationRegistry

DEFAULT_CREDENTIAL_ENROLLMENT_TTL_SECONDS = 900
CREDENTIAL_ENROLLMENT_RECONCILE_INTERVAL_SECONDS = 30

logger = logging.getLogger(__name__)


class CredentialEnrollmentError(ValueError):
    """Raised for a safe, user-actionable enrollment failure."""


class CredentialEnrollmentService:
    """Coordinate catalog, connection, runner, and OpenBao state per user."""

    def __init__(
        self,
        *,
        repository: CredentialEnrollmentRepository,
        runner: CredentialEnrollmentRunnerPort,
        integration_repository: IntegrationRepository,
        integration_registry: IntegrationRegistry,
        credential_store: CredentialStorePort,
        ttl_seconds: int = DEFAULT_CREDENTIAL_ENROLLMENT_TTL_SECONDS,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._integration_repository = integration_repository
        self._integration_registry = integration_registry
        self._credential_store = credential_store
        self._ttl_seconds = int(ttl_seconds)

    async def start(
        self,
        *,
        principal: Principal,
        slug: str,
        credential_name: str = "",
        connection_id: str = "",
    ) -> CredentialEnrollment:
        definition = self._integration_registry.get_definition(slug)
        spec = definition.credential_enrollment if definition is not None else None
        if definition is None or spec is None:
            raise CredentialEnrollmentError("Integration does not support interactive enrollment")
        if not self._runner.supports_enrollment(spec.method):
            raise CredentialEnrollmentError("Interactive enrollment is unavailable on this runtime")

        connection = await self._resolve_connection(
            principal=principal,
            slug=slug,
            connection_id=connection_id,
            credential_name=credential_name or spec.default_credential_name,
        )
        active = await self._repository.find_active(connection.id)
        if active is not None:
            return active

        now = datetime.now(UTC)
        enrollment = CredentialEnrollment(
            id=uuid4(),
            connection_id=connection.id,
            owner_id=principal.user_id,
            tenant_id=principal.tenant_id,
            provider_slug=slug,
            credential_name=connection.credential_name,
            method=spec.method,
            state=CredentialEnrollmentState.PENDING,
            runner_ref={},
            verification_uri="",
            user_code="",
            expires_at=now + timedelta(seconds=self._ttl_seconds),
            error_code="",
            created_at=now,
            updated_at=now,
        )
        await self._mark_credential_state(connection, state="enrolling")
        await self._repository.save(enrollment)
        try:
            started = await self._runner.start_enrollment(enrollment)
        except Exception as exc:
            failed = self._with_state(
                enrollment,
                CredentialEnrollmentState.FAILED,
                error_code="runner_start_failed",
            )
            await self._repository.save(failed)
            await self._mark_credential_state(
                connection,
                state="auth_required",
                error_code="enrollment_failed",
            )
            raise CredentialEnrollmentError("Could not start provider login") from exc
        return await self._repository.save(started)

    async def get(self, enrollment_id: UUID, principal: Principal) -> CredentialEnrollment:
        enrollment = await self._repository.get(enrollment_id)
        if enrollment is None or enrollment.owner_id != principal.user_id:
            raise CredentialEnrollmentError("Credential enrollment not found")
        if enrollment.state not in {
            CredentialEnrollmentState.PENDING,
            CredentialEnrollmentState.AWAITING_USER,
        }:
            return enrollment

        now = datetime.now(UTC)
        connection = await self._integration_repository.get_connection(enrollment.connection_id)
        if connection is None or connection.owner_id != principal.user_id:
            raise CredentialEnrollmentError("Credential enrollment connection not found")
        if now >= enrollment.expires_at:
            expired = self._with_state(enrollment, CredentialEnrollmentState.EXPIRED)
            await self._runner.cancel_enrollment(enrollment)
            await self._mark_credential_state(
                connection,
                state="auth_required",
                error_code="enrollment_expired",
            )
            return await self._repository.save(expired)

        try:
            poll = await self._runner.poll_enrollment(enrollment)
        except Exception:
            failed = self._with_state(
                enrollment,
                CredentialEnrollmentState.FAILED,
                error_code="runner_poll_failed",
            )
            await self._runner.cancel_enrollment(enrollment)
            await self._mark_credential_state(
                connection,
                state="auth_required",
                error_code="enrollment_failed",
            )
            return await self._repository.save(failed)
        if poll.state == CredentialEnrollmentState.COMPLETE:
            definition = self._integration_registry.get_definition(enrollment.provider_slug)
            spec = definition.credential_enrollment if definition is not None else None
            if spec is None or not poll.credential_data.get(spec.credential_field):
                poll = CredentialEnrollmentPoll(
                    state=CredentialEnrollmentState.FAILED,
                    error_code="credential_missing",
                )
            else:
                stored = await self._credential_store.get(
                    "user",
                    enrollment.owner_id,
                    enrollment.credential_name,
                )
                existing_values = await self._credential_store.get_value(
                    "user",
                    enrollment.owner_id,
                    enrollment.credential_name,
                )
                updated_values = dict(existing_values or {})
                updated_values.update(poll.credential_data)
                metadata = dict(stored.metadata) if stored is not None else {}
                metadata.update(
                    {
                        "source": "credential_enrollment",
                        "integration": enrollment.provider_slug,
                        "auth_type": "device_code",
                        "auth_state": "active",
                        "auth_state_updated_at": now.isoformat(),
                    }
                )
                metadata.pop("auth_error_code", None)
                await self._credential_store.store(
                    "user",
                    enrollment.owner_id,
                    enrollment.credential_name,
                    stored.secret_type if stored is not None else SecretType.OAUTH_TOKEN,
                    updated_values,
                    metadata,
                )
                completed = self._with_state(enrollment, CredentialEnrollmentState.COMPLETE)
                await self._runner.cancel_enrollment(enrollment)
                return await self._repository.save(completed)

        if poll.state in {
            CredentialEnrollmentState.FAILED,
            CredentialEnrollmentState.EXPIRED,
            CredentialEnrollmentState.CANCELLED,
        }:
            terminal = self._with_state(
                enrollment,
                poll.state,
                error_code=poll.error_code,
            )
            await self._runner.cancel_enrollment(enrollment)
            await self._mark_credential_state(
                connection,
                state="auth_required",
                error_code=poll.error_code or "enrollment_failed",
            )
            return await self._repository.save(terminal)

        return enrollment

    async def cancel(self, enrollment_id: UUID, principal: Principal) -> CredentialEnrollment:
        enrollment = await self._repository.get(enrollment_id)
        if enrollment is None or enrollment.owner_id != principal.user_id:
            raise CredentialEnrollmentError("Credential enrollment not found")
        if enrollment.state in {
            CredentialEnrollmentState.PENDING,
            CredentialEnrollmentState.AWAITING_USER,
        }:
            await self._runner.cancel_enrollment(enrollment)
            enrollment = self._with_state(enrollment, CredentialEnrollmentState.CANCELLED)
            await self._repository.save(enrollment)
            connection = await self._integration_repository.get_connection(enrollment.connection_id)
            if connection is not None and connection.owner_id == principal.user_id:
                await self._mark_credential_state(
                    connection,
                    state="auth_required",
                    error_code="enrollment_cancelled",
                )
        return enrollment

    async def expire_stale(self, now: datetime | None = None) -> int:
        """Destroy expired enrollment runtimes even when the initiating UI is gone."""
        expired_count = 0
        for enrollment in await self._repository.list_expired_active(now or datetime.now(UTC)):
            try:
                await self._runner.cancel_enrollment(enrollment)
            except Exception:
                # Leave it active so this or another replica retries cleanup on the
                # next reconciliation pass. The enrollment sandbox is workspace-free
                # and never receives an existing credential.
                logger.exception(
                    "Credential enrollment cleanup failed for %s",
                    enrollment.id,
                )
                continue

            connection = await self._integration_repository.get_connection(enrollment.connection_id)
            if connection is not None and connection.owner_id == enrollment.owner_id:
                await self._mark_credential_state(
                    connection,
                    state="auth_required",
                    error_code="enrollment_expired",
                )
            await self._repository.save(
                self._with_state(enrollment, CredentialEnrollmentState.EXPIRED)
            )
            expired_count += 1
        return expired_count

    async def _resolve_connection(
        self,
        *,
        principal: Principal,
        slug: str,
        connection_id: str,
        credential_name: str,
    ) -> IntegrationConnection:
        if connection_id:
            connection = await self._integration_repository.get_connection(connection_id)
            if (
                connection is None
                or connection.owner_id != principal.user_id
                or connection.slug != slug
            ):
                raise CredentialEnrollmentError("Integration connection not found")
            return connection

        existing = await self._integration_repository.list_connections(principal.user_id)
        matching = next((item for item in existing if item.slug == slug), None)
        if matching is not None:
            return matching

        definition = self._integration_registry.get_definition(slug)
        if definition is None:
            raise CredentialEnrollmentError("Integration definition not found")
        now = datetime.now(UTC)
        connection = IntegrationConnection(
            id=str(uuid4()),
            owner_id=principal.user_id,
            integration_type=definition.integration_type,
            adapter=definition.adapter,
            credential_name=credential_name,
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug=slug,
        )
        return await self._integration_repository.save_connection(connection)

    async def _mark_credential_state(
        self,
        connection: IntegrationConnection,
        *,
        state: str,
        error_code: str = "",
    ) -> None:
        stored = await self._credential_store.get(
            "user",
            connection.owner_id,
            connection.credential_name,
        )
        values = await self._credential_store.get_value(
            "user",
            connection.owner_id,
            connection.credential_name,
        )
        metadata = dict(stored.metadata) if stored is not None else {}
        metadata.update(
            {
                "source": "credential_enrollment",
                "integration": connection.slug,
                "auth_type": "device_code",
                "auth_state": state,
                "auth_state_updated_at": datetime.now(UTC).isoformat(),
            }
        )
        if error_code:
            metadata["auth_error_code"] = error_code
        else:
            metadata.pop("auth_error_code", None)
        await self._credential_store.store(
            "user",
            connection.owner_id,
            connection.credential_name,
            stored.secret_type if stored is not None else SecretType.OAUTH_TOKEN,
            values or {},
            metadata,
        )

    @staticmethod
    def _with_state(
        enrollment: CredentialEnrollment,
        state: CredentialEnrollmentState,
        *,
        error_code: str = "",
    ) -> CredentialEnrollment:
        terminal = state in {
            CredentialEnrollmentState.COMPLETE,
            CredentialEnrollmentState.FAILED,
            CredentialEnrollmentState.EXPIRED,
            CredentialEnrollmentState.CANCELLED,
        }
        return replace(
            enrollment,
            state=state,
            error_code=error_code,
            runner_ref={} if terminal else enrollment.runner_ref,
            verification_uri="" if terminal else enrollment.verification_uri,
            user_code="" if terminal else enrollment.user_code,
            updated_at=datetime.now(UTC),
        )


async def reconcile_credential_enrollments_loop(
    service: CredentialEnrollmentService,
    *,
    interval_seconds: float = CREDENTIAL_ENROLLMENT_RECONCILE_INTERVAL_SECONDS,
) -> None:
    """Destroy expired interactive-login sandboxes independently of the UI."""
    logger.info(
        "Credential enrollment reconciliation started, interval=%.1fs",
        interval_seconds,
    )
    while True:
        try:
            count = await service.expire_stale()
            if count:
                logger.info("Expired %d stale credential enrollment(s)", count)
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("Credential enrollment reconciliation task cancelled")
            break
        except Exception:
            logger.exception("Credential enrollment reconciliation iteration failed")
            await asyncio.sleep(interval_seconds)
