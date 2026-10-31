"""Compose the resident's voice channel from its settings and persona.

Everything here is configured-or-fatal: a voice channel that is enabled but
cannot serve (no ``voice:`` section, a voice tool the persona cannot build, no
credential minter, a minter that cannot read its key) stops the resident with
the remedy in the message, rather than starting a channel that answers calls
it cannot complete.
"""

from __future__ import annotations

from typing import Any

from niuu.utils import import_class
from ravn.adapters.channels.voice.gateway import VoiceGateway
from ravn.adapters.channels.voice.prompt import VoicePersona, VoicePromptComposer
from ravn.adapters.channels.voice.room import VoiceRoomCopier
from ravn.adapters.channels.voice.store import VoiceCallStore
from ravn.adapters.channels.voice.tools import VoiceConversationTools, VoiceToolBridge
from ravn.config import Settings, VoiceChannelConfig
from ravn.ports.realtime_voice import RealtimeCredentialMinterPort


def build_voice_gateway(settings: Settings, persona_config: Any | None) -> VoiceGateway:
    """Build the voice channel for the resident's persona."""
    from ravn.cli.commands import _build_tool_mcp_tools

    config: VoiceChannelConfig = settings.gateway.channels.voice
    voice = getattr(persona_config, "voice", None)
    if persona_config is None or voice is None or not voice.enabled:
        name = getattr(persona_config, "name", "<none>")
        raise ValueError(
            f"gateway.channels.voice is enabled but persona {name!r} has no voice section: "
            "add voice.voices to the persona, or disable the voice channel"
        )

    built = {
        tool.name: tool for tool in _build_tool_mcp_tools(settings, persona_config=persona_config)
    }
    missing = [name for name in voice.tools if name not in built]
    if missing:
        raise ValueError(
            f"Persona {persona_config.name!r} voice.tools names {missing}, which the persona "
            "cannot build: add them to allowed_tools or remove them from voice.tools"
        )
    minters = build_minters(config)
    store = VoiceCallStore(config.store_path)
    try:
        return _compose(settings, persona_config, config, built, minters, store)
    except BaseException:
        # Nothing else holds the store yet: close it before the error stops the resident.
        store.close()
        raise


def _compose(
    settings: Settings,
    persona_config: Any,
    config: VoiceChannelConfig,
    built: dict[str, Any],
    minters: dict[str, RealtimeCredentialMinterPort],
    store: VoiceCallStore,
) -> VoiceGateway:
    from ravn.cli.runtime_builders import _build_permission, _resolve_workspace

    voice = persona_config.voice
    bridge = VoiceToolBridge(
        tools=[built[name] for name in voice.tools],
        permission=_build_permission(
            settings,
            _resolve_workspace(settings),
            no_tools=False,
            persona_config=persona_config,
        ),
        conversation=VoiceConversationTools(store, search_limit=config.history_default_limit),
        agent_name=persona_config.name,
        timeout_seconds=config.tool_timeout_seconds,
    )
    persona = VoicePersona(
        name=persona_config.name,
        display_name=config.display_name or persona_config.name.capitalize(),
        identity=persona_config.system_prompt_template,
        style=voice.style,
        voices=dict(voice.voices),
        grok_session=dict(voice.grok),
    )
    composer = VoicePromptComposer(
        persona, models=config.models, time_zone=config.time_zone, tool_names=bridge.names
    )
    return VoiceGateway(
        config,
        persona=persona,
        composer=composer,
        tools=bridge,
        store=store,
        minters=minters,
        room=build_room_copier(config),
    )


def build_minters(config: VoiceChannelConfig) -> dict[str, RealtimeCredentialMinterPort]:
    """One credential minter per configured provider (dynamic adapters)."""
    if not config.minters:
        raise ValueError(
            "gateway.channels.voice is enabled with no minters: configure at least one "
            "provider (xai or openai) with the path its key is mounted at"
        )
    minters: dict[str, RealtimeCredentialMinterPort] = {}
    for provider, minter_config in config.minters.items():
        minter = import_class(minter_config.adapter)(**minter_config.kwargs)
        if not isinstance(minter, RealtimeCredentialMinterPort):
            raise TypeError(
                f"Voice minter {minter_config.adapter!r} must implement "
                "RealtimeCredentialMinterPort"
            )
        minters[provider] = minter
    return minters


def build_room_copier(config: VoiceChannelConfig) -> VoiceRoomCopier | None:
    """The room copy, when the channel is configured with the room's broker."""
    if not config.room_broker_url:
        return None
    return VoiceRoomCopier(
        broker_url=config.room_broker_url,
        participant_id=config.room_participant_id,
        timeout_seconds=config.room_timeout_seconds,
    )
