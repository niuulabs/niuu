"""RavnProfile — complete deployment identity for a Ravn node.

A ``RavnProfile`` is the answer to "who is this specific Ravn?".  It is
serialisable to YAML so it can be stored in Mímir, passed to Ting for spawn
decisions, and announced over mDNS/Sleipnir.

Design rules — what belongs here vs. PersonaConfig:
  * ``PersonaConfig`` owns cognitive/behavioural settings: system prompt
    template, tool access lists, permission mode, LLM alias, iteration budget.
  * ``RavnProfile`` owns deployment identity and infrastructure wiring: name,
    location, which persona to load, specialisations, per-agent channel/MCP/
    Mímir selection, cascade mode, trigger references, and checkpoint policy.

Never duplicate a field that PersonaConfig already owns.  The profile
references a persona by name; the persona drives what the agent can do.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MimirMountRef:
    """A reference to a named Mímir instance with a deployment-specific role.

    ``name`` must match a key in ``Settings.mimir.instances``.
    ``role`` hints to the composite adapter how to weight reads/writes.
    ``priority`` sets the read-order when multiple mounts share the same role.
    """

    name: str
    role: str = "primary"  # "primary" | "archive" | "read-only"
    priority: int = 10


@dataclass
class RavnProfile:
    """Complete declarative definition of a Ravn node.

    One of these per deployed Ravn — whether on Valaskjalf, a Pi, or a phone.
    The profile is serialisable to YAML and can be stored in Mímir, passed to
    Ting for spawn decisions, or used by the TUI to describe the Flokk.

    Fields are grouped by concern:

    Identity:
        name, rune, location, deployment

    Role reference (cognitive settings live in PersonaConfig, not here):
        persona, system_prompt_extra, specialisations

    LLM deployment:
        fallback_model  — offline/Pi fallback; primary model is PersonaConfig's job

    Per-agent infrastructure (filtered from global Settings):
        mcp_servers, gateway_channels, sleipnir_topics, output_mode

    Knowledge wiring:
        mimir_mounts, mimir_write_routing

    Autonomy:
        cascade_mode, trigger_names

    Operational:
        checkpoint_enabled, checkpoint_strategy
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    name: str
    rune: str = "ᚱ"
    location: str = ""  # "gimle", "sindri", "iphone", …
    deployment: str = "ephemeral"  # "k8s" | "systemd" | "pi" | "mobile" | "ephemeral"

    # ------------------------------------------------------------------
    # Role reference — points to a PersonaConfig by name
    # ------------------------------------------------------------------

    persona: str = "autonomous-agent"
    system_prompt_extra: str = ""  # injected after persona template, before context
    specialisations: list[str] = field(default_factory=list)  # ["infrastructure", "coding"]

    # ------------------------------------------------------------------
    # LLM deployment concern
    # (primary model + thinking/tokens are PersonaConfig's responsibility)
    # ------------------------------------------------------------------

    fallback_model: str = ""  # local model for Pi/offline mode

    # ------------------------------------------------------------------
    # Per-agent infrastructure — subset of what global Settings exposes
    # ------------------------------------------------------------------

    mcp_servers: list[str] = field(default_factory=list)  # named MCP server refs
    gateway_channels: list[str] = field(default_factory=list)  # ["telegram", "skuld"]
    sleipnir_topics: list[str] = field(default_factory=list)  # event routing-key patterns
    output_mode: str = "ambient"  # "silent" | "ambient" | "surface"

    # ------------------------------------------------------------------
    # Knowledge wiring
    # ------------------------------------------------------------------

    mimir_mounts: list[MimirMountRef] = field(default_factory=list)
    mimir_write_routing: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Autonomy
    # ------------------------------------------------------------------

    cascade_mode: str = "local"  # "local" | "networked" | "ephemeral"
    trigger_names: list[str] = field(default_factory=list)  # refs to InitiativeConfig.triggers

    # ------------------------------------------------------------------
    # Operational
    # ------------------------------------------------------------------

    checkpoint_enabled: bool = False
    # "on_milestone" | "on_every_n_tools" | "on_destructive"
    checkpoint_strategy: str = "on_milestone"

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a plain dict suitable for JSON/YAML serialisation."""
        return {
            "name": self.name,
            "rune": self.rune,
            "location": self.location,
            "deployment": self.deployment,
            "persona": self.persona,
            "system_prompt_extra": self.system_prompt_extra,
            "specialisations": list(self.specialisations),
            "fallback_model": self.fallback_model,
            "mcp_servers": list(self.mcp_servers),
            "gateway_channels": list(self.gateway_channels),
            "sleipnir_topics": list(self.sleipnir_topics),
            "output_mode": self.output_mode,
            "mimir_mounts": [
                {"name": m.name, "role": m.role, "priority": m.priority} for m in self.mimir_mounts
            ],
            "mimir_write_routing": dict(self.mimir_write_routing),
            "cascade_mode": self.cascade_mode,
            "trigger_names": list(self.trigger_names),
            "checkpoint_enabled": self.checkpoint_enabled,
            "checkpoint_strategy": self.checkpoint_strategy,
        }
