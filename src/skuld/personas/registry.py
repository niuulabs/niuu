"""Which projector plays a persona on which transport.

The registry is keyed by the engine family and mapped from the transport adapter the node
runs (``transport_adapter``). A transport with no entry cannot play a persona: starting a
session with one on it fails rather than running the persona unprojected.
"""

from __future__ import annotations

from skuld.personas.claude import ClaudeProjector
from skuld.personas.codex import CodexProjector
from skuld.personas.instruction_only import InstructionOnlyProjector
from skuld.personas.projection import PersonaProjectionError, PersonaProjector

PROJECTORS: dict[str, PersonaProjector] = {
    "claude": ClaudeProjector(),
    "codex": CodexProjector(),
    "opencode": InstructionOnlyProjector("opencode", delivery="append"),
    "grok": InstructionOnlyProjector(
        "grok",
        delivery="unverified",
        why=(
            "Grok's ACP has no standard system prompt field; the instructions are sent as "
            "systemPrompt and a Grok build that ignores it plays the persona without them"
        ),
    ),
    "pi": InstructionOnlyProjector("pi", delivery="append"),
    "dsh": InstructionOnlyProjector("dsh", delivery="append"),
    "muse": InstructionOnlyProjector(
        "muse",
        delivery="unsupported",
        why=(
            "Muse MSP cannot apply system instructions; configure the persona's behaviour in "
            "the Muse workspace AGENTS.md or run it on a harness that takes instructions"
        ),
    ),
    "claude-remote-control": InstructionOnlyProjector(
        "claude-remote-control",
        delivery="unsupported",
        why="Claude Remote Control is driven by the native app and takes no instructions",
    ),
    "codex-remote-control": InstructionOnlyProjector(
        "codex-remote-control",
        delivery="unsupported",
        why="Codex Remote Control is driven by the native app and takes no instructions",
    ),
}

ENGINE_BY_ADAPTER: dict[str, str] = {
    "skuld.transports.sdk.SDKTransport": "claude",
    "skuld.transports.subprocess.SubprocessTransport": "claude",
    "skuld.transports.persistent_subprocess.PersistentSubprocessTransport": "claude",
    "skuld.transports.sdk_websocket.SdkWebSocketTransport": "claude",
    "skuld.transports.tmux_interactive.TmuxInteractiveTransport": "claude",
    "skuld.transports.codex_ws.CodexWebSocketTransport": "codex",
    "skuld.transports.codex.CodexSubprocessTransport": "codex",
    "skuld.transports.opencode.OpenCodeHttpTransport": "opencode",
    "skuld.transports.grok.GrokACPTransport": "grok",
    "skuld.transports.pi.PiRpcTransport": "pi",
    "skuld.transports.dsh.DshJsonRpcTransport": "dsh",
    "skuld.transports.muse.MuseMSPTransport": "muse",
    "skuld.transports.remote_control.RemoteControlTransport": "claude-remote-control",
    "skuld.transports.remote_control.CodexRemoteControlTransport": "codex-remote-control",
}


def projector_for(adapter_path: str) -> PersonaProjector:
    """The projector for a transport adapter class path, or raise naming the known ones."""
    engine = ENGINE_BY_ADAPTER.get(adapter_path)
    if engine is None:
        raise PersonaProjectionError(
            f"No persona projector for transport {adapter_path!r}, so a persona cannot be "
            f"played on it. Use one of: {', '.join(sorted(ENGINE_BY_ADAPTER))}; or register a "
            "projector for it in skuld.personas.registry."
        )
    return PROJECTORS[engine]
