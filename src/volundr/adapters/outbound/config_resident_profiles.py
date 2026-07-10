"""Configuration-backed resident deployment profile provider."""

from __future__ import annotations

from volundr.config import ResidentProfileConfig
from volundr.domain.models import ResidentDeploymentProfile
from volundr.domain.ports import ResidentDeploymentProfileProvider


class ConfigResidentDeploymentProfileProvider(ResidentDeploymentProfileProvider):
    """Expose enabled, operator-approved resident deployment profiles."""

    def __init__(self, profiles: list[ResidentProfileConfig]) -> None:
        self._profiles = {
            profile.id: ResidentDeploymentProfile(
                id=profile.id,
                display_name=profile.display_name,
                description=profile.description,
                backend=profile.backend,
                engine=profile.engine,
                capabilities=profile.capabilities,
                default_model=profile.default_model,
                allowed_models=profile.allowed_models,
                labels=profile.labels,
                deployment=profile.deployment,
            )
            for profile in profiles
            if profile.enabled
        }

    def get(self, profile_id: str) -> ResidentDeploymentProfile | None:
        return self._profiles.get(profile_id)

    def list(self) -> list[ResidentDeploymentProfile]:
        return sorted(self._profiles.values(), key=lambda profile: profile.id)
