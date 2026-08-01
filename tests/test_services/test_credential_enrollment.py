"""Tests for user-scoped interactive credential enrollment."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from volundr.adapters.outbound.memory_integrations import InMemoryIntegrationRepository
from volundr.domain.models import (
    CredentialEnrollmentPoll,
    CredentialEnrollmentState,
    Principal,
)
from volundr.domain.services.credential_enrollment import (
    CredentialEnrollmentError,
    CredentialEnrollmentService,
)
from volundr.domain.services.integration_registry import (
    IntegrationRegistry,
    definitions_from_config,
)


class _EnrollmentRepository:
    def __init__(self) -> None:
        self.items = {}

    async def save(self, enrollment):
        self.items[enrollment.id] = enrollment
        return enrollment

    async def get(self, enrollment_id):
        return self.items.get(enrollment_id)

    async def find_active(self, connection_id):
        return next(
            (
                item
                for item in self.items.values()
                if item.connection_id == connection_id
                and item.state
                in {CredentialEnrollmentState.PENDING, CredentialEnrollmentState.AWAITING_USER}
            ),
            None,
        )

    async def list_expired_active(self, now):
        return [
            item
            for item in self.items.values()
            if item.state
            in {CredentialEnrollmentState.PENDING, CredentialEnrollmentState.AWAITING_USER}
            and item.expires_at <= now
        ]


class _CredentialStore:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str, str], dict] = {}

    async def get(self, owner_type, owner_id, name):
        item = self.items.get((owner_type, owner_id, name))
        if item is None:
            return None
        return SimpleNamespace(secret_type=item["secret_type"], metadata=item["metadata"])

    async def get_value(self, owner_type, owner_id, name):
        item = self.items.get((owner_type, owner_id, name))
        return dict(item["data"]) if item is not None else None

    async def store(self, owner_type, owner_id, name, secret_type, data, metadata=None):
        self.items[(owner_type, owner_id, name)] = {
            "secret_type": secret_type,
            "data": dict(data),
            "metadata": dict(metadata or {}),
        }
        return await self.get(owner_type, owner_id, name)


class _Runner:
    def __init__(self) -> None:
        self.poll_result = CredentialEnrollmentPoll(state=CredentialEnrollmentState.AWAITING_USER)
        self.cancelled = []

    def supports_enrollment(self, method: str) -> bool:
        return method == "codex_device"

    async def start_enrollment(self, enrollment):
        return replace(
            enrollment,
            state=CredentialEnrollmentState.AWAITING_USER,
            runner_ref={"sandbox_name": f"enroll-{enrollment.id}"},
            verification_uri="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
        )

    async def poll_enrollment(self, _enrollment):
        return self.poll_result

    async def cancel_enrollment(self, enrollment):
        self.cancelled.append(enrollment.id)


def _principal(user_id: str) -> Principal:
    return Principal(
        user_id=user_id,
        email=f"{user_id}@example.test",
        tenant_id="tenant-1",
        roles=["volundr:developer"],
    )


def _service():
    enrollment_repository = _EnrollmentRepository()
    integration_repository = InMemoryIntegrationRepository()
    credential_store = _CredentialStore()
    runner = _Runner()
    registry = IntegrationRegistry(
        definitions_from_config(
            [
                {
                    "slug": "codex",
                    "name": "Codex",
                    "integration_type": "ai_provider",
                    "auth_type": "device_code",
                    "credential_enrollment": {
                        "method": "codex_device",
                        "credential_field": "auth.json",
                        "default_credential_name": "codex-credentials",
                    },
                }
            ]
        )
    )
    service = CredentialEnrollmentService(
        repository=enrollment_repository,
        runner=runner,
        integration_repository=integration_repository,
        integration_registry=registry,
        credential_store=credential_store,
    )
    return service, enrollment_repository, integration_repository, credential_store, runner


async def test_same_credential_name_is_isolated_for_each_user() -> None:
    service, _, integration_repository, credential_store, _ = _service()

    first = await service.start(principal=_principal("user-1"), slug="codex")
    second = await service.start(principal=_principal("user-2"), slug="codex")

    assert first.connection_id != second.connection_id
    assert first.credential_name == second.credential_name == "codex-credentials"
    assert len(await integration_repository.list_connections("user-1")) == 1
    assert len(await integration_repository.list_connections("user-2")) == 1
    assert ("user", "user-1", "codex-credentials") in credential_store.items
    assert ("user", "user-2", "codex-credentials") in credential_store.items


async def test_completed_login_persists_only_to_enrollment_owner() -> None:
    service, _, _, credential_store, runner = _service()
    principal = _principal("user-1")
    enrollment = await service.start(principal=principal, slug="codex")
    credential_store.items[("user", "user-1", "codex-credentials")]["data"] = {
        "config.toml": 'model = "gpt-5"'
    }
    runner.poll_result = CredentialEnrollmentPoll(
        state=CredentialEnrollmentState.COMPLETE,
        credential_data={"auth.json": '{"tokens":{"access_token":"a"}}'},
    )

    completed = await service.get(enrollment.id, principal)

    assert completed.state == CredentialEnrollmentState.COMPLETE
    assert completed.runner_ref == {}
    assert completed.verification_uri == ""
    assert completed.user_code == ""
    stored = credential_store.items[("user", "user-1", "codex-credentials")]
    assert stored["data"] == {
        "auth.json": '{"tokens":{"access_token":"a"}}',
        "config.toml": 'model = "gpt-5"',
    }
    assert stored["metadata"]["auth_state"] == "active"
    assert runner.cancelled == [enrollment.id]


async def test_other_user_cannot_read_or_complete_enrollment() -> None:
    service, _, _, credential_store, runner = _service()
    enrollment = await service.start(principal=_principal("user-1"), slug="codex")
    runner.poll_result = CredentialEnrollmentPoll(
        state=CredentialEnrollmentState.COMPLETE,
        credential_data={"auth.json": "secret"},
    )

    with pytest.raises(CredentialEnrollmentError, match="not found"):
        await service.get(enrollment.id, _principal("user-2"))

    assert ("user", "user-2", "codex-credentials") not in credential_store.items


async def test_start_is_idempotent_while_login_is_active() -> None:
    service, _, _, _, _ = _service()
    principal = _principal("user-1")

    first = await service.start(principal=principal, slug="codex")
    second = await service.start(principal=principal, slug="codex")

    assert second.id == first.id


async def test_expired_login_is_reaped_without_a_ui_poll() -> None:
    service, enrollment_repository, _, credential_store, runner = _service()
    principal = _principal("user-1")
    enrollment = await service.start(principal=principal, slug="codex")
    expired = replace(enrollment, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    await enrollment_repository.save(expired)

    count = await service.expire_stale()

    stored = await enrollment_repository.get(enrollment.id)
    assert count == 1
    assert stored.state == CredentialEnrollmentState.EXPIRED
    assert stored.runner_ref == {}
    assert stored.user_code == ""
    assert runner.cancelled == [enrollment.id]
    assert credential_store.items[("user", "user-1", "codex-credentials")]["metadata"] == {
        "source": "credential_enrollment",
        "integration": "codex",
        "auth_type": "device_code",
        "auth_state": "auth_required",
        "auth_state_updated_at": credential_store.items[("user", "user-1", "codex-credentials")][
            "metadata"
        ]["auth_state_updated_at"],
        "auth_error_code": "enrollment_expired",
    }


async def test_cancelled_login_leaves_connection_reconnectable() -> None:
    service, _, _, credential_store, runner = _service()
    principal = _principal("user-1")
    enrollment = await service.start(principal=principal, slug="codex")

    cancelled = await service.cancel(enrollment.id, principal)

    assert cancelled.state == CredentialEnrollmentState.CANCELLED
    assert cancelled.user_code == ""
    assert runner.cancelled == [enrollment.id]
    metadata = credential_store.items[("user", "user-1", "codex-credentials")]["metadata"]
    assert metadata["auth_state"] == "auth_required"
    assert metadata["auth_error_code"] == "enrollment_cancelled"
