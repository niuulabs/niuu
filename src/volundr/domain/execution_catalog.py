"""Immutable, provider-neutral compute execution catalog models."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_SHA256_PATTERN = r"^[a-f0-9]{64}$"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class ExecutionCatalogError(RuntimeError):
    """The configured execution catalog is invalid or unavailable."""


class ExecutionCatalogNotFoundError(ExecutionCatalogError):
    """An exact catalog entry was not found."""


class ExecutionCatalogIntegrityError(ExecutionCatalogError):
    """Catalog content does not match its pinned identity."""


class ExecutionPlanMismatchError(ExecutionCatalogIntegrityError):
    """A durable execution pin no longer matches its catalog or active lease."""


class ExecutionSelectionError(ValueError):
    """A caller supplied an invalid or conflicting execution selection."""


class CatalogRef(_FrozenModel):
    """An exact immutable catalog entry reference."""

    id: str = Field(pattern=_IDENTIFIER_PATTERN)
    revision: str = Field(pattern=_IDENTIFIER_PATTERN)

    @classmethod
    def parse(cls, value: str) -> CatalogRef:
        """Parse the required ``id@revision`` selection syntax."""
        entry_id, separator, revision = value.partition("@")
        if separator != "@" or "@" in revision or not entry_id or not revision:
            raise ValueError("Execution selection must use exact 'id@revision' syntax")
        return cls(id=entry_id, revision=revision)

    def __str__(self) -> str:
        return f"{self.id}@{self.revision}"


class AdapterBinding(_FrozenModel):
    """A dynamically imported adapter and deployment-resolved constructor arguments."""

    binding_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    adapter: str = Field(pattern=r"^(?:[A-Za-z_]\w*\.)+[A-Za-z_]\w*$")
    kwargs: dict[str, JsonValue] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("secret_kwargs_env")
    @classmethod
    def validate_secret_environment_references(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not environment.strip() for key, environment in value.items()):
            raise ValueError("Secret kwarg and environment variable names must not be blank")
        return value


class TrustMode(StrEnum):
    PROVIDER_IDENTITY = "provider_identity"
    SSH_CA = "ssh_ca"
    TOFU = "tofu"


class AccessTrust(_FrozenModel):
    mode: TrustMode
    kwargs: dict[str, JsonValue] = Field(default_factory=dict)


class CatalogEntry(_FrozenModel):
    """Common immutable identity assigned to all catalog entries."""

    id: str = Field(pattern=_IDENTIFIER_PATTERN)
    revision: str = Field(pattern=_IDENTIFIER_PATTERN)
    digest: str = Field(pattern=_SHA256_PATTERN, repr=False)

    @property
    def ref(self) -> CatalogRef:
        return CatalogRef(id=self.id, revision=self.revision)


class MachineProfile(CatalogEntry):
    """Reference to infrastructure configuration owned by MachineProvider."""

    provider_profile: str = Field(min_length=1)
    host_requirements: dict[str, str] = Field(default_factory=dict)


class ScriptMediaType(StrEnum):
    EXECUTABLE = "application/x-executable"
    SHELL = "text/x-shellscript"


class ScriptArtifact(_FrozenModel):
    """A verified packaged recipe artifact with its trusted local path."""

    id: str = Field(pattern=_IDENTIFIER_PATTERN)
    package_path: str = Field(min_length=1)
    resolved_path: Path
    sha256: str = Field(pattern=_SHA256_PATTERN)
    media_type: ScriptMediaType = ScriptMediaType.EXECUTABLE

    @field_validator("package_path")
    @classmethod
    def require_relative_package_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or not value.strip():
            raise ValueError("Artifact package_path must be a contained relative path")
        return path.as_posix()

    @field_validator("resolved_path")
    @classmethod
    def require_absolute_resolved_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Artifact resolved_path must be absolute")
        return value


class HostRecipeStage(_FrozenModel):
    """One ordered, idempotently replayable host preparation stage."""

    id: str = Field(pattern=_IDENTIFIER_PATTERN)
    principal: str = Field(min_length=1)
    timeout_seconds: float = Field(gt=0)
    check: str = Field(pattern=_IDENTIFIER_PATTERN)
    apply: str = Field(pattern=_IDENTIFIER_PATTERN)
    verify: str = Field(pattern=_IDENTIFIER_PATTERN)


class HostRecipe(CatalogEntry):
    """Ordered host preparation using only verified packaged artifacts."""

    preparation: AdapterBinding
    artifacts: tuple[ScriptArtifact, ...]
    stages: tuple[HostRecipeStage, ...]

    @model_validator(mode="after")
    def validate_recipe_graph(self) -> HostRecipe:
        artifact_ids = [artifact.id for artifact in self.artifacts]
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ValueError("Host recipe artifact IDs must be unique")
        stage_ids = [stage.id for stage in self.stages]
        if len(set(stage_ids)) != len(stage_ids):
            raise ValueError("Host recipe stage IDs must be unique")
        if not self.stages:
            raise ValueError("Host recipe must define at least one stage")
        known_artifacts = set(artifact_ids)
        for stage in self.stages:
            referenced = {stage.check, stage.apply, stage.verify}
            missing = sorted(referenced - known_artifacts)
            if missing:
                raise ValueError(
                    f"Host recipe stage {stage.id!r} references unknown artifacts: {missing}"
                )
        return self


class AccessConfiguration(CatalogEntry):
    """Guest transport construction and independently selected trust contract."""

    transport: AdapterBinding
    principal: str = Field(min_length=1)
    trust: AccessTrust


class RuntimeProfile(CatalogEntry):
    """Pinned session runtime and its contributor contract."""

    runtime: AdapterBinding
    contributor_backend: str = Field(min_length=1)
    storage_mode: str = Field(min_length=1)
    capabilities: tuple[str, ...] = ()
    host_requirements: dict[str, str] = Field(default_factory=dict)

    @field_validator("capabilities")
    @classmethod
    def require_unique_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not capability.strip() for capability in value):
            raise ValueError("Runtime capabilities must not be blank")
        if len(set(value)) != len(value):
            raise ValueError("Runtime capabilities must be unique")
        return value


class ExecutionProfile(CatalogEntry):
    """Approved composition of infrastructure, preparation, access, and runtime."""

    pool_id: str = Field(min_length=1)
    provider_binding: str = Field(pattern=_IDENTIFIER_PATTERN)
    machine: CatalogRef
    host_recipe: CatalogRef
    access: CatalogRef
    runtime: CatalogRef


class ExecutionCatalogSnapshot(_FrozenModel):
    """One validated read of every file-backed catalog entry."""

    default_execution: CatalogRef
    legacy_compute_profiles: dict[str, CatalogRef] = Field(default_factory=dict)
    machines: tuple[MachineProfile, ...]
    host_recipes: tuple[HostRecipe, ...]
    access_configurations: tuple[AccessConfiguration, ...]
    runtimes: tuple[RuntimeProfile, ...]
    executions: tuple[ExecutionProfile, ...]

    @field_validator("legacy_compute_profiles")
    @classmethod
    def validate_legacy_profile_names(
        cls,
        value: dict[str, CatalogRef],
    ) -> dict[str, CatalogRef]:
        if any(not name.strip() for name in value):
            raise ValueError("Legacy compute profile names must not be blank")
        return value


class ResolvedExecutionPlan(_FrozenModel):
    """Complete pinned execution data safe to persist with a session or lease."""

    plan_version: Literal[1] = 1
    execution: CatalogRef
    plan_digest: str = Field(pattern=_SHA256_PATTERN, repr=False)
    pool_id: str
    provider_binding: str
    provider_profile: str
    machine: MachineProfile
    host_recipe: HostRecipe
    access: AccessConfiguration
    runtime: RuntimeProfile
    host_recipe_digest: str = Field(pattern=_SHA256_PATTERN, repr=False)
    host_compatibility_digest: str = Field(pattern=_SHA256_PATTERN, repr=False)
    session_execution_digest: str = Field(pattern=_SHA256_PATTERN, repr=False)

    @property
    def preparation_binding_id(self) -> str:
        return self.host_recipe.preparation.binding_id

    @property
    def runtime_binding_id(self) -> str:
        return self.runtime.runtime.binding_id

    @property
    def contributor_backend(self) -> str:
        return self.runtime.contributor_backend

    @property
    def capabilities(self) -> tuple[str, ...]:
        return self.runtime.capabilities

    @property
    def storage_mode(self) -> str:
        return self.runtime.storage_mode

    @property
    def host_requirements(self) -> dict[str, str]:
        requirements = dict(self.machine.host_requirements)
        for name, value in self.runtime.host_requirements.items():
            current = requirements.get(name)
            if current is not None and current != value:
                raise ExecutionCatalogIntegrityError(
                    f"Machine and runtime require conflicting host value for {name!r}"
                )
            requirements[name] = value
        return requirements
