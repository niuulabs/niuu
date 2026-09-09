"""Configuration contracts shared across Niuu services."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DatabaseConfig(BaseModel):
    """PostgreSQL database configuration shared by hosted services."""

    host: str = Field(default="localhost")
    port: int = Field(default=5432)
    user: str = Field(default="volundr")
    password: str = Field(default="volundr")
    name: str = Field(default="volundr")
    min_pool_size: int = Field(default=5)
    max_pool_size: int = Field(default=20)

    @property
    def database(self) -> str:
        """Alias for name to maintain compatibility."""
        return self.name

    @property
    def dsn(self) -> str:
        """Return PostgreSQL connection string."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class SessionDefinitionConfig(BaseModel):
    """Configuration for a single session definition (e.g. skuldClaude, skuldCodex).

    Session definitions describe available AI backend configurations.
    Each definition has a unique key, display metadata, and a ``defaults``
    dict that gets merged into Helm values when a session is created with
    this definition.
    """

    enabled: bool = True
    display_name: str = ""
    description: str = ""
    labels: list[str] = Field(default_factory=list)
    default_model: str = ""
    compatible_providers: list[str] = Field(
        default_factory=list,
        description=(
            "Model providers this runtime accepts (e.g. ['anthropic'], "
            "['openai']). An empty list means the runtime is provider-neutral "
            "and accepts any model."
        ),
    )
    defaults: dict[str, Any] = Field(default_factory=dict)


def default_session_definitions() -> dict[str, SessionDefinitionConfig]:
    """Built-in session definitions so the wizard works without Helm config.

    These carry only broker-level config (cliType, transportAdapter).
    Helm values merge on top when running in Kubernetes.
    """
    return {
        "skuldClaude": SessionDefinitionConfig(
            enabled=True,
            display_name="Claude Code",
            description="Anthropic Claude — full IDE with terminal, tools, and MCP",
            labels=["session", "claude"],
            default_model="claude-opus-4-8",
            compatible_providers=["anthropic"],
            defaults={
                "broker": {
                    "cliType": "claude",
                    "transport": "sdk",
                    "transportAdapter": "skuld.transports.sdk.SDKTransport",
                    "agentTeams": False,
                },
            },
        ),
        "skuldClaudeInteractive": SessionDefinitionConfig(
            enabled=True,
            display_name="Claude Code Interactive",
            description=(
                "Anthropic Claude Code through a tmux-backed interactive terminal "
                "for subscription sessions, slash commands, and terminal controls"
            ),
            labels=["session", "claude", "interactive"],
            default_model="claude-sonnet-4-6",
            compatible_providers=["anthropic"],
            defaults={
                "broker": {
                    "cliType": "claude",
                    "transport": "tmux-interactive",
                    "transportAdapter": (
                        "skuld.transports.tmux_interactive.TmuxInteractiveTransport"
                    ),
                    "skipPermissions": True,
                    "agentTeams": True,
                },
            },
        ),
        "skuldCodex": SessionDefinitionConfig(
            enabled=True,
            display_name="OpenAI Codex",
            description="OpenAI Codex — WebSocket protocol with streaming and tools",
            labels=["session", "codex"],
            default_model="",
            compatible_providers=["openai"],
            defaults={
                "broker": {
                    "cliType": "codex-ws",
                    "transportAdapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
                    "agentTeams": False,
                },
            },
        ),
        "skuldCodexExec": SessionDefinitionConfig(
            enabled=True,
            display_name="OpenAI Codex (Batch)",
            description=(
                "OpenAI Codex — app-server transport tuned for autonomous workflow execution"
            ),
            labels=["session", "codex", "batch"],
            default_model="",
            compatible_providers=["openai"],
            defaults={
                "broker": {
                    "cliType": "codex-ws",
                    "transportAdapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
                    "agentTeams": False,
                },
            },
        ),
        "skuldGrok": SessionDefinitionConfig(
            enabled=True,
            display_name="xAI Grok Build",
            description=(
                "xAI Grok Build — Agent Client Protocol (ACP) over stdio (Scaldy pipeline)"
            ),
            labels=["session", "grok"],
            default_model="grok-build",
            compatible_providers=["xai"],
            defaults={
                "broker": {
                    "cliType": "grok",
                    "transportAdapter": "skuld.transports.grok.GrokACPTransport",
                    "agentTeams": False,
                },
            },
        ),
        "skuldOpenCode": SessionDefinitionConfig(
            enabled=True,
            display_name="OpenCode",
            description="Model-neutral AI coding agent — Claude, OpenAI, Gemini, local",
            labels=["session", "opencode"],
            default_model="",
            compatible_providers=[],
            defaults={
                "broker": {
                    "cliType": "opencode",
                    "transportAdapter": "skuld.transports.opencode.OpenCodeHttpTransport",
                    "agentTeams": False,
                },
            },
        ),
        "skuldDeepSeekHarness": SessionDefinitionConfig(
            enabled=True,
            display_name="DeepSeek Harness",
            description=(
                "DeepSeek Harness (dsh) — SDK JSON-RPC stdio protocol with "
                "streaming, tools, and durable session events"
            ),
            labels=["session", "dsh"],
            default_model="deepseek-v4-flash",
            compatible_providers=["deepseek"],
            defaults={
                "broker": {
                    "cliType": "dsh",
                    "transportAdapter": "skuld.transports.dsh.DshJsonRpcTransport",
                    "agentTeams": False,
                },
            },
        ),
        "skuldClaudeRemote": SessionDefinitionConfig(
            enabled=True,
            display_name="Claude Remote Control",
            description=(
                "Claude Code in Remote Control mode — pair with the Claude app or "
                "claude.ai/code; the native app drives the session"
            ),
            labels=["session", "claude", "remote-control"],
            default_model="",
            compatible_providers=["anthropic"],
            defaults={
                "broker": {
                    "cliType": "claude",
                    "transportAdapter": "skuld.transports.remote_control.RemoteControlTransport",
                    "agentTeams": False,
                },
            },
        ),
    }


class WorkloadIdentityVerifierConfig(BaseModel):
    """Adapter config for validating workload identity proofs."""

    name: str = Field(
        default="kubernetes",
        description="Stable verifier name referenced by workload identity mappings.",
    )
    adapter: str = Field(
        default="niuu.adapters.workload_identity.jwt.JwtWorkloadIdentityVerifier",
        description="Fully-qualified class path for the verifier adapter.",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


class WorkloadIdentityMappingConfig(BaseModel):
    """Maps a validated workload proof to an application principal."""

    name: str = Field(default="", description="Human-readable workload identity name.")
    verifier: str = Field(
        default="kubernetes",
        description="Verifier name that must accept the presented proof.",
    )
    subject: str = Field(default="", description="Exact subject claim to match.")
    subject_prefix: str = Field(
        default="",
        description="Subject claim prefix to match for dynamic workload identities.",
    )
    issuer: str = Field(default="", description="Optional exact issuer claim to match.")
    claims: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional exact-match claim selectors. Dot notation is supported.",
    )
    owner_id: str = Field(
        default="",
        description="User id used as the exchanged token subject and session owner.",
    )
    tenant_id: str = Field(default="default", description="Tenant/org id for isolation.")
    email: str = Field(default="", description="Optional owner/workload email claim.")
    roles: list[str] = Field(
        default_factory=lambda: ["volundr:developer"],
        description="Roles embedded in the exchanged token.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-secret audit metadata embedded as workload_* claims.",
    )


class WorkloadIdentityConfig(BaseModel):
    """Short-lived workload token exchange configuration."""

    enabled: bool = Field(default=False)
    issuer: str = Field(
        default="",
        description="Issuer used for exchanged workload JWTs and Envoy validation.",
    )
    audiences: list[str] = Field(
        default_factory=lambda: ["volundr-api"],
        description="Audiences accepted by Envoy for exchanged workload JWTs.",
    )
    token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    key_id: str = Field(default="niuu-workload")
    signing_key_pem: str = Field(
        default="",
        description="PEM encoded RSA private key. Prefer signing_key_env for deployments.",
    )
    signing_key_env: str = Field(
        default="NIUU_WORKLOAD_IDENTITY_SIGNING_KEY",
        description="Environment variable containing a PEM encoded RSA private key.",
    )
    verifiers: list[WorkloadIdentityVerifierConfig] = Field(default_factory=list)
    mappings: list[WorkloadIdentityMappingConfig] = Field(default_factory=list)
