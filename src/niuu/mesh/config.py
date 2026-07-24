"""Shared configuration for Niuu NATS mesh transports."""

from __future__ import annotations

from typing import Annotated

from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class MeshNatsExtraSubscriptionConfig(BaseModel):
    """Additional JetStream filter subject for an existing stream."""

    subject: str = Field(default="")
    stream_name: str = Field(default="")
    event_types: list[str] = Field(default_factory=list)


class MeshNatsCoreSubscriptionConfig(BaseModel):
    """Additional core NATS filter subject for live control messages."""

    subject: str = Field(default="")


class MeshNatsConfig(BaseSettings):
    """NATS JetStream settings shared by mesh participants."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore", case_sensitive=True)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del cls, settings_cls, dotenv_settings
        return env_settings, init_settings, file_secret_settings

    servers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["nats://localhost:4222"],
        validation_alias=AliasChoices("servers", "NATS_URL"),
    )
    stream_name: str = Field(default="ravn_environment")
    jetstream_domain: str = Field(default="")
    subject_prefix: str = Field(default="ravn.environment")
    consumer_group: str = Field(default="")
    publish_timeout_s: float = Field(default=10.0)
    replay_from_sequence: int | None = Field(default=None)
    retention: str = Field(default="limits")
    max_age_seconds: int = Field(default=7 * 24 * 3600)
    max_bytes: int = Field(default=1024 * 1024 * 1024)
    ring_buffer_depth: int = Field(default=1000)
    connect_timeout_s: float = Field(default=10.0)
    max_reconnect_attempts: int = Field(default=60)
    ensure_stream: bool = Field(default=True)
    tls_ca_file: str = Field(default="")
    tls_ca_pem: str = Field(default="")
    tls_cert_file: str = Field(default="")
    tls_key_file: str = Field(default="")
    tls_hostname: str = Field(default="")
    tls_handshake_first: bool = Field(default=False)
    tls_legacy_ca: bool = Field(default=False)
    tls_insecure_skip_verify: bool = Field(default=False)
    user: str = Field(default="")
    user_env: str = Field(default="")
    password_env: str = Field(default="")
    token_env: str = Field(default="")
    nkeys_seed_file: str = Field(default="")
    nkeys_seed_env: str = Field(default="")
    extra_subscriptions: list[MeshNatsExtraSubscriptionConfig] = Field(default_factory=list)
    core_subscriptions: list[MeshNatsCoreSubscriptionConfig] = Field(default_factory=list)

    @field_validator("servers", mode="before")
    @classmethod
    def _parse_servers(cls, value: object) -> object:
        del cls
        if not isinstance(value, str):
            return value
        return [entry.strip() for entry in value.split(",") if entry.strip()]


__all__ = [
    "MeshNatsConfig",
    "MeshNatsCoreSubscriptionConfig",
    "MeshNatsExtraSubscriptionConfig",
]
