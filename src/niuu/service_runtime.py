"""Shared runtime helpers for co-hosted Niuu service apps."""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Protocol

import uvicorn

from niuu.domain.logging import LoggingConfig
from niuu.domain.services.pat_validator import PATValidator
from niuu.domain.services.workload_identity import WorkloadIdentityService
from niuu.utils import import_class, resolve_secret_kwargs

logger = logging.getLogger(__name__)


class ServiceSettings(Protocol):
    """Settings sections required by shared service runtime builders."""

    identity: Any
    storage: Any
    credential_store: Any
    pat: Any


_SHARED_CREDENTIAL_STORES: dict[str, object] = {}
_SHARED_CREDENTIAL_STORE_REFS: dict[str, int] = {}


def configure_logging(config: LoggingConfig | None = None) -> None:
    """Configure logging from shared settings."""
    if config is None:
        config = LoggingConfig()

    level_name = config.level.upper()
    log_format = config.format.lower()
    level = getattr(logging, level_name, logging.INFO)

    if log_format == "json":
        fmt = (
            '{"time":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        )
    else:
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    logging.basicConfig(
        level=level,
        format=fmt,
        stream=sys.stderr,
        force=True,
    )
    logging.getLogger().setLevel(level)
    logger.info(
        "Logging configured: level=%s, format=%s",
        level_name,
        log_format,
    )


def create_workload_identity_service(config: Any) -> WorkloadIdentityService:
    """Resolve secret material and verifier adapters at the composition boundary."""
    signing_key_pem = str(getattr(config, "signing_key_pem", "") or "")
    signing_key_env = str(getattr(config, "signing_key_env", "") or "")
    if not signing_key_pem and signing_key_env:
        signing_key_pem = os.environ.get(signing_key_env, "")

    verifiers = {}
    for verifier_config in getattr(config, "verifiers", []) or []:
        kwargs = resolve_secret_kwargs(
            dict(getattr(verifier_config, "kwargs", {}) or {}),
            dict(getattr(verifier_config, "secret_kwargs_env", {}) or {}),
        )
        verifier_class = import_class(str(getattr(verifier_config, "adapter")))
        verifiers[str(getattr(verifier_config, "name"))] = verifier_class(**kwargs)

    return WorkloadIdentityService(
        config,
        signing_key_pem=signing_key_pem,
        verifiers=verifiers,
    )


def create_identity_adapter(
    settings: ServiceSettings,
    user_repository,
    storage=None,
    tenant_service=None,
):
    """Create the shared identity adapter from dynamic config."""
    config = settings.identity
    kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
    kwargs = dict(kwargs)
    kwargs["user_repository"] = user_repository
    kwargs["role_mapping"] = settings.identity.role_mapping
    if storage is not None:
        kwargs["storage"] = storage
    if tenant_service is not None:
        kwargs["tenant_service"] = tenant_service
    cls = import_class(config.adapter)
    instance = cls(**kwargs)
    logger.info("Identity adapter: %s", config.adapter.rsplit(".", 1)[-1])
    return instance


def create_storage_adapter(settings: ServiceSettings):
    """Create the shared storage adapter from dynamic config."""
    config = settings.storage
    cls = import_class(config.adapter)
    kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Storage adapter: %s", config.adapter.rsplit(".", 1)[-1])
    return instance


def _credential_store_cache_key(settings: ServiceSettings) -> str:
    config = settings.credential_store
    kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
    return json.dumps(
        {
            "adapter": config.adapter,
            "kwargs": kwargs,
        },
        sort_keys=True,
        default=str,
    )


def create_credential_store(settings: ServiceSettings):
    """Create or reuse the shared credential store."""
    cache_key = _credential_store_cache_key(settings)
    cached = _SHARED_CREDENTIAL_STORES.get(cache_key)
    if cached is not None:
        _SHARED_CREDENTIAL_STORE_REFS[cache_key] = (
            _SHARED_CREDENTIAL_STORE_REFS.get(cache_key, 0) + 1
        )
        return cached

    config = settings.credential_store
    cls = import_class(config.adapter)
    kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
    instance = cls(**kwargs)
    _SHARED_CREDENTIAL_STORES[cache_key] = instance
    _SHARED_CREDENTIAL_STORE_REFS[cache_key] = 1
    logger.info("Credential store: %s", config.adapter.rsplit(".", 1)[-1])
    return instance


def release_credential_store(settings: ServiceSettings) -> None:
    """Release one reference to the shared credential store."""
    cache_key = _credential_store_cache_key(settings)
    refs = _SHARED_CREDENTIAL_STORE_REFS.get(cache_key, 0)
    if refs <= 1:
        _SHARED_CREDENTIAL_STORE_REFS.pop(cache_key, None)
        _SHARED_CREDENTIAL_STORES.pop(cache_key, None)
        return
    _SHARED_CREDENTIAL_STORE_REFS[cache_key] = refs - 1


def create_pat_validator(settings: ServiceSettings, pat_repository) -> PATValidator:
    """Create the shared PAT validator."""
    return PATValidator(
        repo=pat_repository,
        cache_ttl=settings.pat.revocation_cache_ttl,
        revoked_cache_ttl=settings.pat.revoked_cache_ttl,
    )


def run_service_app(import_path: str, default_port: int) -> None:
    """Run a service app via uvicorn with simple env overrides."""
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", str(default_port)))
    uvicorn.run(import_path, host=host, port=port)
