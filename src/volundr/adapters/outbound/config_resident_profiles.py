"""Configuration-backed resident deployment profile provider."""

from __future__ import annotations

from volundr.config import ResidentProfileConfig
from volundr.domain.models import ResidentDeploymentProfile
from volundr.domain.ports import PricingProvider, ResidentDeploymentProfileProvider


class ConfigResidentDeploymentProfileProvider(ResidentDeploymentProfileProvider):
    """Expose enabled, operator-approved resident deployment profiles."""

    def __init__(
        self,
        profiles: list[ResidentProfileConfig],
        model_catalog: PricingProvider | None = None,
    ) -> None:
        self._profiles = {profile.id: profile for profile in profiles if profile.enabled}
        self._model_catalog = model_catalog

    def get(self, profile_id: str) -> ResidentDeploymentProfile | None:
        profile = self._profiles.get(profile_id)
        if profile is None:
            return None
        return self._resolve(profile)

    def list(self) -> list[ResidentDeploymentProfile]:
        resolved = (self._resolve(profile) for profile in self._profiles.values())
        return sorted(
            (profile for profile in resolved if profile is not None),
            key=lambda profile: profile.id,
        )

    def _resolve(self, config: ResidentProfileConfig) -> ResidentDeploymentProfile | None:
        allowed_models = list(config.allowed_models)
        default_model = config.default_model
        if self._model_catalog is not None:
            allowed_models = self._catalog_models(config)
            if not allowed_models:
                return None
            default_model = self._runtime_model_id(config, config.default_model)
            if default_model not in allowed_models:
                return None

        return ResidentDeploymentProfile(
            id=config.id,
            display_name=config.display_name,
            description=config.description,
            backend=config.backend,
            engine=config.engine,
            capabilities=config.capabilities,
            default_model=default_model,
            allowed_models=allowed_models,
            model_prefix=config.model_prefix,
            labels=config.labels,
            deployment=config.deployment,
        )

    def _catalog_models(self, config: ResidentProfileConfig) -> list[str]:
        catalog = self._model_catalog
        if catalog is None:
            return list(config.allowed_models)

        vendors = {vendor.strip().lower() for vendor in config.catalog_vendors if vendor.strip()}
        models = [
            model
            for model in catalog.list_models()
            if not vendors or model.vendor.strip().lower() in vendors
        ]
        available_ids = {model.id for model in models}
        if config.allowed_models:
            return [
                model_id
                for model_id in config.allowed_models
                if self._catalog_model_id(config, model_id) in available_ids
            ]
        return [self._runtime_model_id(config, model.id) for model in models]

    @staticmethod
    def _catalog_model_id(config: ResidentProfileConfig, model_id: str) -> str:
        if config.model_prefix and model_id.startswith(config.model_prefix):
            return model_id[len(config.model_prefix) :]
        return model_id

    @classmethod
    def _runtime_model_id(cls, config: ResidentProfileConfig, model_id: str) -> str:
        canonical = cls._catalog_model_id(config, model_id)
        return f"{config.model_prefix}{canonical}"
