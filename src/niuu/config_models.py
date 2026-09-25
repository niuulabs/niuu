"""Configuration contracts shared across Niuu services."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, model_validator


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
            description=(
                "Claude Code in the full session workspace: chat, terminal, diffs, "
                "files and MCP tools. The usual choice for Claude."
            ),
            labels=["session", "claude"],
            default_model="claude-opus-5",
            compatible_providers=["anthropic", "local"],
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
                "Claude Code as the plain interactive terminal you know from your own "
                "machine: slash commands, permission prompts and agent teams. Pick this "
                "when you want the CLI itself rather than the chat workspace."
            ),
            labels=["session", "claude", "interactive"],
            default_model="claude-opus-5",
            compatible_providers=["anthropic", "local"],
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
            description=(
                "OpenAI's Codex coding agent with streaming chat and tools. "
                "The usual choice for OpenAI."
            ),
            labels=["session", "codex"],
            # Astra is the default Codex model (Damien, 2026-09-05). It was empty, which
            # left the choice entirely to whatever the caller happened to pass — the app
            # always sends one, but a REST/tool launch that omitted it got no model at
            # all.
            default_model="gpt-6-astra",
            compatible_providers=["openai", "local"],
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
                "OpenAI Codex set up for unattended runs. Ting workflows use this one; "
                "for hands-on work pick OpenAI Codex."
            ),
            labels=["session", "codex", "batch"],
            default_model="gpt-6-astra",
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
            description="xAI's Grok Build coding agent, signed in with your Grok account.",
            labels=["session", "grok"],
            default_model="grok-4.7",
            compatible_providers=["xai"],
            defaults={
                "broker": {
                    "cliType": "grok",
                    "transportAdapter": "skuld.transports.grok.GrokACPTransport",
                    "agentTeams": False,
                },
            },
        ),
        "skuldMuse": SessionDefinitionConfig(
            enabled=True,
            display_name="Meta Muse Code",
            description=(
                "Meta's coding agent with streaming chat, tools and resumable sessions. "
                "Steer its work while it runs."
            ),
            labels=["session", "muse"],
            # Muse Spark 1.3 shipped 2026-09-02 and is what `muse` serves by default in
            # Muse Code 1.0.2; the id is exactly what the Meta Model API accepts.
            default_model="muse-spark-1.3",
            compatible_providers=["meta"],
            defaults={
                "broker": {
                    "cliType": "muse",
                    "transportAdapter": "skuld.transports.muse.MuseMSPTransport",
                    "agentTeams": False,
                },
            },
        ),
        "skuldPi": SessionDefinitionConfig(
            enabled=True,
            display_name="PI",
            description="PI coding agent with streaming chat, tools and resumable sessions",
            labels=["session", "pi"],
            default_model="openai-codex/gpt-6-astra",
            compatible_providers=[],
            defaults={
                "broker": {
                    "cliType": "pi",
                    "transportAdapter": "skuld.transports.pi.PiRpcTransport",
                    "agentTeams": False,
                },
            },
        ),
        "skuldOpenCode": SessionDefinitionConfig(
            enabled=True,
            display_name="OpenCode",
            description=(
                "OpenCode, an open-source coding agent that works with any connected "
                "provider or a local model."
            ),
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
            description="DeepSeek's Harness coding agent (dsh) with streaming chat and tools.",
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
                "Claude Code driven from the Claude app or claude.ai/code: the session "
                "runs here, you steer it from there."
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
        description=(
            "Fixed user id used as the exchanged token subject and session owner. "
            "Ignored when owner_id_claim is set."
        ),
    )
    owner_id_claim: str = Field(
        default="",
        description=(
            "When set, derive owner_id per-caller from this claim on the verified "
            "workload proof (dot notation supported) instead of the fixed owner_id "
            "above — e.g. 'sub' to give each distinct ServiceAccount its own "
            "identity, so callers sharing one mapping are not conflated into one "
            "principal. A workload proof missing this claim is rejected, not "
            "silently mapped to a default."
        ),
    )
    owner_id_claim_pattern: str = Field(
        default="",
        description=(
            "Optional regex with one capture group applied to the owner_id_claim "
            "value, e.g. '^system:serviceaccount:[^:]+:resident-(.+)$' to pull the "
            "resident id out of a Kubernetes ServiceAccount subject. Unset uses the "
            "whole claim value verbatim; a claim value that fails to match a "
            "configured pattern is rejected, not passed through unstripped."
        ),
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

    @model_validator(mode="after")
    def _validate_owner_id_claim_pattern(self) -> WorkloadIdentityMappingConfig:
        """A bad pattern here was a 500 at exchange time, not a config-load
        error — every real caller through this mapping would fail the same
        way, discoverable only by trying it. Fail at load time instead."""
        pattern = self.owner_id_claim_pattern.strip()
        if not pattern:
            return self
        if not self.owner_id_claim.strip():
            raise ValueError(
                "owner_id_claim_pattern requires owner_id_claim to also be set — a "
                "pattern with no claim to apply it to can never be reached"
            )
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"owner_id_claim_pattern {pattern!r} does not compile: {exc}") from exc
        if compiled.groups != 1:
            raise ValueError(
                f"owner_id_claim_pattern {pattern!r} must have exactly one capture "
                f"group, found {compiled.groups}"
            )
        return self


class WorkloadIdentityTenantResolverConfig(BaseModel):
    """Dynamic adapter resolving a per-caller tenant_id for a mapping that
    also derives owner_id per-caller (``owner_id_claim`` set).

    Every deployed resident's own tenant_id lives in ONE durable record
    (Völundr's ``resident_runtimes`` table) — a mapping's own static
    ``tenant_id`` is a single fixed guess that is wrong for any tenant other
    than the one it names. When this is configured, a mapping using
    ``owner_id_claim`` derives its tenant from here instead, keyed by the
    same resolved owner_id (see ``niuu.ports.owner_tenant_resolver
    .OwnerTenantResolverPort``); the mapping's own ``tenant_id`` is unused
    for that mapping in that case. Left unset (the default), every mapping
    falls back to its own static ``tenant_id`` — an explicit,
    single-tenant-only mode, not a silent guess.
    """

    adapter: str = Field(
        default="",
        description=(
            "Fully-qualified OwnerTenantResolverPort adapter class path. Empty "
            "means no resolver is configured — every mapping uses its own static "
            "tenant_id."
        ),
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


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
    tenant_resolver: WorkloadIdentityTenantResolverConfig = Field(
        default_factory=WorkloadIdentityTenantResolverConfig
    )
