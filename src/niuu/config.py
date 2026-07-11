"""Shared configuration models for git providers.

These classes are used by both the niuu plugin (to create its own git
provider registry) and by Volundr (which embeds them in its Settings).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from niuu.domain.models import InstanceKind, InstanceVisibility


@dataclass(frozen=True)
class GitHubInstance:
    """Configuration for a single GitHub instance."""

    name: str
    base_url: str
    token: str | None = None
    orgs: tuple[str, ...] = ()


@dataclass(frozen=True)
class GitLabInstance:
    """Configuration for a single GitLab instance."""

    name: str
    base_url: str
    token: str | None = None
    orgs: tuple[str, ...] = ()


class GitHubConfig(BaseModel):
    """GitHub provider configuration."""

    enabled: bool = Field(default=False)
    token: str | None = Field(default=None)
    base_url: str = Field(default="https://api.github.com")
    instances: list[dict[str, Any]] = Field(default_factory=list)

    def get_instances(self) -> list[GitHubInstance]:
        """Get all configured GitHub instances.

        Token resolution order per instance:
        1. Explicit ``token`` field in the instance dict
        2. Environment variable named by ``token_env`` (set by Helm from per-instance secrets)
        3. Top-level ``self.token`` (from ``GIT__GITHUB__TOKEN`` env var)
        """
        result: list[GitHubInstance] = []

        for item in self.instances:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "")
            base_url = item.get("base_url", "")
            if not name or not base_url:
                continue
            token = item.get("token")
            if not token:
                token_env = item.get("token_env")
                if token_env:
                    token = os.environ.get(token_env)
            if not token:
                token = self.token
            orgs = tuple(item.get("orgs", []))
            result.append(GitHubInstance(name, base_url, token, orgs))

        if not result and (self.enabled or self.token):
            result.append(GitHubInstance("GitHub", self.base_url, self.token))

        return result


class GitLabConfig(BaseModel):
    """GitLab provider configuration."""

    enabled: bool = Field(default=False)
    token: str | None = Field(default=None)
    base_url: str = Field(default="https://gitlab.com")
    instances: list[dict[str, Any]] = Field(default_factory=list)

    def get_instances(self) -> list[GitLabInstance]:
        """Get all configured GitLab instances.

        Token resolution order per instance:
        1. Explicit ``token`` field in the instance dict
        2. Environment variable named by ``token_env`` (set by Helm from per-instance secrets)
        3. Top-level ``self.token`` (from ``GIT__GITLAB__TOKEN`` env var)
        """
        result: list[GitLabInstance] = []

        for item in self.instances:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "")
            base_url = item.get("base_url", "")
            if not name or not base_url:
                continue
            token = item.get("token")
            if not token:
                token_env = item.get("token_env")
                if token_env:
                    token = os.environ.get(token_env)
            if not token:
                token = self.token
            orgs = tuple(item.get("orgs", []))
            result.append(GitLabInstance(name, base_url, token, orgs))

        if not result and (self.enabled or self.token):
            result.append(GitLabInstance("GitLab", self.base_url, self.token))

        return result


class GitConfig(BaseModel):
    """Git provider configuration (shared across niuu services)."""

    github: GitHubConfig = Field(default_factory=GitHubConfig)
    gitlab: GitLabConfig = Field(default_factory=GitLabConfig)


class HttpAuthAdapterConfig(BaseModel):
    """Dynamic adapter config for outbound HTTP auth/header providers."""

    adapter: str = Field(
        default="niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


class DynamicAdapterConfig(BaseModel):
    """Dynamic adapter config for discovery and other pluggable services."""

    adapter: str = Field(default="")
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


class InstanceSeedConfig(BaseModel):
    """Config-seeded runtime instance registration."""

    id: str | None = Field(default=None)
    kind: InstanceKind = Field(default=InstanceKind.VOLUNDR)
    slug: str = Field(default="")
    name: str = Field(default="")
    base_url: str = Field(default="")
    visibility: InstanceVisibility = Field(default=InstanceVisibility.SYSTEM)
    owner_id: str | None = Field(default=None)
    tenant_id: str | None = Field(default=None)
    enabled: bool = Field(default=True)
    is_default: bool = Field(default=False)
    config: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class InstanceCatalogEntryConfig(BaseModel):
    """Config-driven runtime kind metadata for Guild registration UX."""

    kind: InstanceKind = Field(default=InstanceKind.VOLUNDR)
    label: str = Field(default="")
    rune: str = Field(default="")
    summary: str = Field(default="")
    detail: str = Field(default="")
    registerable: bool = Field(default=True)
    filterable: bool = Field(default=True)


def _default_instance_catalog() -> list[InstanceCatalogEntryConfig]:
    return [
        InstanceCatalogEntryConfig(
            kind=InstanceKind.VOLUNDR,
            label="Volundr",
            rune="ᚲ",
            summary="session forge",
            detail="spawns remote dev pods",
        ),
        InstanceCatalogEntryConfig(
            kind=InstanceKind.MIMIR,
            label="Mimir",
            rune="ᛗ",
            summary="knowledge index",
            detail="chronicles, embeddings, graph",
        ),
        InstanceCatalogEntryConfig(
            kind=InstanceKind.BIFROST,
            label="Bifrost",
            rune="ᚨ",
            summary="LLM gateway",
            detail="routes inference",
        ),
        InstanceCatalogEntryConfig(
            kind=InstanceKind.TING,
            label="Tyr",
            rune="✦",
            summary="saga coordinator",
            detail="dispatch ravens",
        ),
        InstanceCatalogEntryConfig(
            kind=InstanceKind.RAVN,
            label="Ravn",
            rune="ᚱ",
            summary="agent runtime",
            detail="runs ravens and wardens",
        ),
        InstanceCatalogEntryConfig(
            kind=InstanceKind.OBSERVATORY,
            label="Observatory",
            rune="◉",
            summary="topology surface",
            detail="discovers and streams platform state",
        ),
    ]


class InstanceRegistryConfig(BaseModel):
    """Shared registry config for runtime instances."""

    instances: list[InstanceSeedConfig] = Field(default_factory=list)
    catalog: list[InstanceCatalogEntryConfig] = Field(default_factory=_default_instance_catalog)


def has_enabled_instance_kind(settings: Any, kind: InstanceKind) -> bool:
    """Return whether settings configure an enabled runtime instance kind."""

    registry = getattr(settings, "niuu", None)
    for instance in getattr(registry, "instances", ()):
        try:
            instance_kind = InstanceKind(getattr(instance, "kind", ""))
        except ValueError:
            continue
        if instance_kind == kind and getattr(instance, "enabled", True):
            return True
    return False


class CorsConfig(BaseSettings):
    """Shared CORS configuration for Niuu HTTP services."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

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

    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["*"],
        validation_alias=AliasChoices("allowed_origins", "CORS_ORIGINS"),
    )
    allow_credentials: bool = Field(
        default=True,
        validation_alias=AliasChoices("allow_credentials", "CORS_ALLOW_CREDENTIALS"),
    )
    allow_methods: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["*"],
        validation_alias=AliasChoices("allow_methods", "CORS_ALLOW_METHODS"),
    )
    allow_headers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["*"],
        validation_alias=AliasChoices("allow_headers", "CORS_ALLOW_HEADERS"),
    )

    @field_validator("allowed_origins", "allow_methods", "allow_headers", mode="before")
    @classmethod
    def _normalize_list(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


def _config_paths() -> list[Path]:
    """Config file search paths (same locations as Volundr)."""
    env = os.environ.get("NIUU_CONFIG")
    if env:
        return [Path(env)]
    return [
        Path("./config.yaml"),
        Path("/etc/volundr/config.yaml"),
    ]


class NiuuHostConfig(BaseSettings):
    """Typed behavior for the unified local Niuu host."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

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

    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias=AliasChoices("cors_origins", "NIUU_CORS_ORIGINS"),
    )
    forge_state_file: str = Field(
        default="~/.niuu/forge-state.json",
        validation_alias=AliasChoices("forge_state_file", "NIUU_FORGE_STATE_FILE"),
    )
    no_web: bool = Field(
        default=False,
        validation_alias=AliasChoices("no_web", "NIUU_NO_WEB"),
    )
    database_mode: Literal["auto", "embedded", "external"] = Field(
        default="auto",
        validation_alias=AliasChoices("database_mode", "NIUU_DATABASE_MODE"),
    )
    pgdata_dir: str = Field(
        default="",
        validation_alias=AliasChoices("pgdata_dir", "NIUU_PGDATA_DIR"),
    )
    external_database_host: str = Field(
        default="",
        validation_alias=AliasChoices("external_database_host", "DATABASE__HOST"),
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        del cls
        if not isinstance(value, str):
            return value
        return [item.strip() for item in value.split(",") if item.strip()]

    @field_validator("database_mode", mode="before")
    @classmethod
    def _normalize_database_mode(cls, value: object) -> object:
        del cls
        if isinstance(value, str) and not value.strip():
            return "auto"
        return value


class NiuuSettings(BaseSettings):
    """Minimal settings for the niuu shared services.

    Reads the shared git, CORS, and host sections from the platform YAML config
    without depending on ``volundr.config.Settings``.
    """

    model_config = SettingsConfigDict(
        yaml_file=_config_paths(),
        yaml_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    git: GitConfig = Field(default_factory=GitConfig)
    cors: CorsConfig = Field(default_factory=CorsConfig)
    host: Annotated[NiuuHostConfig, NoDecode] = Field(
        default_factory=NiuuHostConfig,
    )

    @field_validator("host", mode="before")
    @classmethod
    def _ignore_bare_bind_host(cls, value: object) -> object:
        del cls
        if isinstance(value, str) and value == os.environ.get("HOST"):
            return NiuuHostConfig()
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
        )
