from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from niuu.domain.models import IntegrationConnection, IntegrationType
from volundr.domain.services.telegram_ingress import (
    TelegramIngressService,
    build_test_update,
)


@dataclass
class _FakeIntegrationRepo:
    connections: list[IntegrationConnection]

    async def list_connections_global(
        self,
        integration_type: IntegrationType | None = None,
        *,
        slug: str | None = None,
        enabled_only: bool = False,
    ) -> list[IntegrationConnection]:
        results = list(self.connections)
        if integration_type is not None:
            results = [c for c in results if c.integration_type == integration_type]
        if slug is not None:
            results = [c for c in results if c.slug == slug]
        if enabled_only:
            results = [c for c in results if c.enabled]
        return results


class _FakeCredentialStore:
    def __init__(self, values: dict[tuple[str, str, str], dict[str, str]]) -> None:
        self._values = values

    async def get_value(self, owner_type: str, owner_id: str, name: str):
        return self._values.get((owner_type, owner_id, name))


class _FakeCommunicationIngress:
    def __init__(self) -> None:
        self.messages = []

    async def handle_inbound_message(self, message) -> None:
        self.messages.append(message)


class _FakeCursorRepo:
    def __init__(self, values: dict[tuple[str, str], str] | None = None) -> None:
        self.values = values or {}
        self.upserts: list[tuple[str, str, str]] = []

    async def get_cursor(self, platform: str, consumer_key: str) -> str | None:
        return self.values.get((platform, consumer_key))

    async def upsert_cursor(self, platform: str, consumer_key: str, cursor: str) -> None:
        self.values[(platform, consumer_key)] = cursor
        self.upserts.append((platform, consumer_key, cursor))


def _connection(
    *,
    owner_id: str,
    credential_name: str,
    enabled: bool = True,
    slug: str = "telegram",
) -> IntegrationConnection:
    return IntegrationConnection(
        id=str(uuid4()),
        owner_id=owner_id,
        integration_type=IntegrationType.MESSAGING,
        adapter="tyr.adapters.telegram_notification.TelegramNotificationAdapter",
        credential_name=credential_name,
        config={},
        enabled=enabled,
        slug=slug,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


async def test_load_bindings_deduplicates_shared_bot_tokens():
    ingress = _FakeCommunicationIngress()
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo(
            [
                _connection(owner_id="u1", credential_name="telegram-a"),
                _connection(owner_id="u2", credential_name="telegram-b"),
                _connection(owner_id="u3", credential_name="telegram-c"),
            ]
        ),
        credential_store=_FakeCredentialStore(
            {
                ("user", "u1", "telegram-a"): {"bot_token": "token-1"},
                ("user", "u2", "telegram-b"): {"bot_token": "token-1"},
                ("user", "u3", "telegram-c"): {"bot_token": "token-2"},
            }
        ),
        communication_ingress=ingress,
    )

    bindings = await service._load_bindings()

    assert len(bindings) == 2
    by_token = {binding.bot_token: binding for binding in bindings}
    assert by_token["token-1"].owner_ids == ("u1", "u2")
    assert by_token["token-2"].owner_ids == ("u3",)


async def test_handle_update_normalizes_telegram_message():
    ingress = _FakeCommunicationIngress()
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo([]),
        credential_store=_FakeCredentialStore({}),
        communication_ingress=ingress,
    )

    update = build_test_update(
        update_id=42,
        chat_id=-1003841286227,
        message_thread_id=7,
        text="@coder hello",
        sender_id=7637020486,
        sender_name="Jozef",
        sender_username="Jozef_85",
    )

    await service._handle_update(update)

    assert len(ingress.messages) == 1
    message = ingress.messages[0]
    assert message.platform.value == "telegram"
    assert message.conversation_id == "-1003841286227"
    assert message.thread_id == "7"
    assert message.sender_external_id == "7637020486"
    assert message.sender_display_name == "Jozef_85"
    assert message.text == "@coder hello"


async def test_handle_update_ignores_bot_messages():
    ingress = _FakeCommunicationIngress()
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo([]),
        credential_store=_FakeCredentialStore({}),
        communication_ingress=ingress,
    )

    update = build_test_update(
        update_id=43,
        chat_id=-1003841286227,
        text="status",
        is_bot=True,
    )

    await service._handle_update(update)

    assert ingress.messages == []


async def test_handle_update_accepts_caption_from_topic_message():
    ingress = _FakeCommunicationIngress()
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo([]),
        credential_store=_FakeCredentialStore({}),
        communication_ingress=ingress,
    )

    update = build_test_update(
        update_id=44,
        chat_id=-1003841286227,
        message_thread_id=9,
        text="",
        sender_name="Jozef",
    )
    update.message.text = None
    update.message.caption = "please look"

    await service._handle_update(update)

    assert len(ingress.messages) == 1
    assert ingress.messages[0].text == "please look"


async def test_load_offset_reads_persisted_cursor():
    ingress = _FakeCommunicationIngress()
    cursor_repo = _FakeCursorRepo({("telegram", "consumer-1"): "77"})
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo([]),
        credential_store=_FakeCredentialStore({}),
        communication_ingress=ingress,
        cursor_repository=cursor_repo,
    )

    binding = type("Binding", (), {"bot_token": "token-1"})()

    # Reuse the real key derivation so the assertion stays aligned with the service.
    from volundr.domain.services.telegram_ingress import _consumer_key

    cursor_repo.values = {("telegram", _consumer_key("token-1")): "77"}

    offset = await service._load_offset(binding)

    assert offset == 77


async def test_store_offset_persists_cursor():
    ingress = _FakeCommunicationIngress()
    cursor_repo = _FakeCursorRepo()
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo([]),
        credential_store=_FakeCredentialStore({}),
        communication_ingress=ingress,
        cursor_repository=cursor_repo,
    )

    binding = type("Binding", (), {"bot_token": "token-1"})()

    await service._store_offset(binding, 123)

    assert len(cursor_repo.upserts) == 1
    platform, _, cursor = cursor_repo.upserts[0]
    assert platform == "telegram"
    assert cursor == "123"


async def test_start_skips_when_telegram_dependency_is_unavailable():
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo([]),
        credential_store=_FakeCredentialStore({}),
        communication_ingress=_FakeCommunicationIngress(),
    )

    with patch("volundr.domain.services.telegram_ingress.HAS_TELEGRAM", False):
        await service.start()

    assert service._manager_task is None


async def test_start_and_stop_manage_manager_and_bot_tasks():
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo([]),
        credential_store=_FakeCredentialStore({}),
        communication_ingress=_FakeCommunicationIngress(),
    )

    manager_task = asyncio.create_task(asyncio.Event().wait())
    bot_task = asyncio.create_task(asyncio.Event().wait())
    service._manager_task = manager_task
    service._bot_tasks["token-1"] = bot_task

    await service.start()
    await service.stop()

    assert service._manager_task is None
    assert service._bot_tasks == {}
    assert manager_task.cancelled()
    assert bot_task.cancelled()


async def test_load_bindings_skips_failed_missing_and_blank_credentials():
    ingress = _FakeCommunicationIngress()
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo(
            [
                _connection(owner_id="u1", credential_name="ok"),
                _connection(owner_id="u2", credential_name="missing"),
                _connection(owner_id="u3", credential_name="blank"),
                _connection(owner_id="u4", credential_name="boom"),
            ]
        ),
        credential_store=_FakeCredentialStore(
            {
                ("user", "u1", "ok"): {"bot_token": "token-1"},
                ("user", "u2", "missing"): {},
                ("user", "u3", "blank"): {"bot_token": "   "},
            }
        ),
        communication_ingress=ingress,
    )

    async def raising_get_value(owner_type: str, owner_id: str, name: str):
        if owner_id == "u4":
            raise RuntimeError("boom")
        return await _FakeCredentialStore.get_value(
            service._credential_store,
            owner_type,
            owner_id,
            name,
        )

    service._credential_store.get_value = raising_get_value  # type: ignore[method-assign]

    bindings = await service._load_bindings()

    assert [(binding.bot_token, binding.owner_ids) for binding in bindings] == [
        ("token-1", ("u1",))
    ]


async def test_run_manager_starts_new_pollers_and_reaps_stale_tokens():
    ingress = _FakeCommunicationIngress()
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo([]),
        credential_store=_FakeCredentialStore({}),
        communication_ingress=ingress,
        refresh_interval_s=0.01,
    )

    first = [SimpleNamespace(bot_token="token-1")]
    second: list[SimpleNamespace] = []
    service._load_bindings = AsyncMock(side_effect=[first, second])  # type: ignore[method-assign]

    created = []
    stale_task = asyncio.create_task(asyncio.Event().wait())
    service._bot_tasks["stale-token"] = stale_task

    async def fake_run_bot(binding):
        created.append(binding.bot_token)
        service._stop_event.set()

    service._run_bot = fake_run_bot  # type: ignore[method-assign]

    await service._run_manager()

    assert created == ["token-1"]
    assert "token-1" in service._bot_tasks
    assert "stale-token" not in service._bot_tasks
    assert stale_task.cancelled()

    await service.stop()


async def test_run_bot_processes_updates_and_persists_next_offset():
    ingress = _FakeCommunicationIngress()
    cursor_repo = _FakeCursorRepo()
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo([]),
        credential_store=_FakeCredentialStore({}),
        communication_ingress=ingress,
        cursor_repository=cursor_repo,
        poll_timeout_s=5,
    )

    binding = type(
        "Binding",
        (),
        {
            "bot_token": "token-1",
            "owner_ids": ("u1",),
            "connection_ids": ("c1",),
        },
    )()
    update = build_test_update(update_id=7, chat_id=123, text="hello")

    class _FakeBot:
        def __init__(self, token: str) -> None:
            self.token = token
            self.calls = 0

        async def get_updates(self, *, offset, timeout, allowed_updates):
            self.calls += 1
            assert offset is None
            assert timeout == 5
            assert allowed_updates == ["message", "edited_message"]
            service._stop_event.set()
            return [update]

    with patch("volundr.domain.services.telegram_ingress.Bot", _FakeBot):
        await service._run_bot(binding)

    assert len(ingress.messages) == 1
    assert cursor_repo.upserts[-1][2] == "8"


async def test_run_bot_swallow_poller_errors():
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo([]),
        credential_store=_FakeCredentialStore({}),
        communication_ingress=_FakeCommunicationIngress(),
    )
    binding = type(
        "Binding",
        (),
        {
            "bot_token": "token-1",
            "owner_ids": ("u1",),
            "connection_ids": ("c1",),
        },
    )()

    class _FakeBot:
        def __init__(self, token: str) -> None:
            self.token = token

        async def get_updates(self, **kwargs):
            raise RuntimeError("network down")

    with patch("volundr.domain.services.telegram_ingress.Bot", _FakeBot):
        await service._run_bot(binding)


async def test_handle_update_supports_edited_messages_and_chat_id_fallback():
    ingress = _FakeCommunicationIngress()
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo([]),
        credential_store=_FakeCredentialStore({}),
        communication_ingress=ingress,
    )

    sender = SimpleNamespace(
        id=3,
        username=None,
        first_name="Ada",
        last_name="Lovelace",
        is_bot=False,
    )
    edited_message = SimpleNamespace(
        text="edited",
        caption=None,
        message_id=9,
        message_thread_id=None,
        from_=sender,
        chat=None,
        chat_id=456,
    )
    update = SimpleNamespace(update_id=10, edited_message=edited_message)

    await service._handle_update(update)

    assert len(ingress.messages) == 1
    assert ingress.messages[0].conversation_id == "456"
    assert ingress.messages[0].sender_display_name == "Ada Lovelace"


async def test_handle_update_ignores_missing_message_text_and_chat():
    ingress = _FakeCommunicationIngress()
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo([]),
        credential_store=_FakeCredentialStore({}),
        communication_ingress=ingress,
    )

    await service._handle_update(SimpleNamespace(update_id=1))
    await service._handle_update(
        SimpleNamespace(
            update_id=2,
            message=SimpleNamespace(text="   ", caption=None),
        )
    )
    await service._handle_update(
        SimpleNamespace(
            update_id=3,
            effective_message=SimpleNamespace(
                text="hello",
                caption=None,
                from_user=None,
                chat=None,
                chat_id=None,
            ),
        )
    )

    assert ingress.messages == []


async def test_load_offset_handles_missing_and_invalid_cursor_values():
    ingress = _FakeCommunicationIngress()
    cursor_repo = _FakeCursorRepo()
    service = TelegramIngressService(
        integration_repo=_FakeIntegrationRepo([]),
        credential_store=_FakeCredentialStore({}),
        communication_ingress=ingress,
        cursor_repository=cursor_repo,
    )
    binding = type("Binding", (), {"bot_token": "token-1"})()

    assert await service._load_offset(binding) is None

    from volundr.domain.services.telegram_ingress import _consumer_key

    cursor_repo.values = {("telegram", _consumer_key("token-1")): "invalid"}
    assert await service._load_offset(binding) is None
