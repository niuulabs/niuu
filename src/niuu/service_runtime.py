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


def create_authorization_adapter(settings):
    """Compose the shared authorization port from the configured dynamic adapter."""
    from identity.ports import AuthorizationPort

    config = settings.authorization
    cls = import_class(config.adapter)
    _validate_authorization_adapter_class(cls, getattr(settings, "auth_mode", "envoy"))
    adapter = cls(**resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env))
    if not isinstance(adapter, AuthorizationPort):
        raise TypeError("Configured authorization adapter must implement AuthorizationPort")
    return adapter


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

    tenant_resolver = None
    resolver_config = getattr(config, "tenant_resolver", None)
    resolver_adapter_path = str(getattr(resolver_config, "adapter", "") or "")
    if resolver_adapter_path:
        resolver_kwargs = resolve_secret_kwargs(
            dict(getattr(resolver_config, "kwargs", {}) or {}),
            dict(getattr(resolver_config, "secret_kwargs_env", {}) or {}),
        )
        tenant_resolver = import_class(resolver_adapter_path)(**resolver_kwargs)

    return WorkloadIdentityService(
        config,
        signing_key_pem=signing_key_pem,
        verifiers=verifiers,
        tenant_resolver=tenant_resolver,
    )


#: Token issuer that cannot be verified in-process: it signs with a local
#: symmetric HS256 key that only the process holding it can check, so a
#: separate JWKS-based verifier (``auth.mode: oidc``) can never validate it.
_UNVERIFIABLE_TOKEN_ISSUER = "niuu.adapters.memory_token_issuer.MemoryTokenIssuer"

#: The only auth_mode values a co-hosted service may declare. Any other
#: value (typo, stale config) fails startup instead of silently falling
#: through to whichever branch string-matches first.
_KNOWN_AUTH_MODES = frozenset({"envoy", "none", "oidc"})


def _get_auth_mode(settings: ServiceSettings) -> str:
    """Read ``settings.auth_mode``, defaulting to ``"envoy"`` only for
    packages that have not yet adopted the explicit ``auth.mode`` contract.

    Kept deliberately narrow: every package this fix touches (Völundr's
    shared ``Settings``, Ting's ``Settings``) declares ``auth_mode`` as a
    real field with its own default, so this fallback is never actually
    exercised for them — a forgotten wire-up there surfaces as a concrete
    ``auth_mode`` value flowing through the checks below, not as silence.
    Packages this round does not touch (guild, credentials, tracker,
    features, integrations, personas, observatory) have no ``auth_mode``
    field at all yet; defaulting them to ``"envoy"`` preserves their
    unchanged Kubernetes-only behaviour rather than crashing every
    co-hosted service that has not opted into this contract. Migrating
    them is tracked as follow-up work, not silently done here.
    """
    auth_mode = getattr(settings, "auth_mode", "envoy")
    if auth_mode not in _KNOWN_AUTH_MODES:
        raise ValueError(
            f"Unknown auth_mode: {auth_mode!r} (expected one of {sorted(_KNOWN_AUTH_MODES)})"
        )
    return auth_mode


def _validate_identity_adapter_class(cls: type, auth_mode: str) -> None:
    """Enforce that the identity adapter's guarantees match the declared mode.

    ``auth.mode: oidc`` is a claim that every inbound identity path on this
    host is signature-verified; starting with anything else — including the
    allow-all default — would let that claim be false while ``/auth/config``
    still reports ``oidc``. ``auth.mode: none`` is the mirror claim ("no
    authentication at all"); starting with an adapter that partially trusts
    headers or partially verifies tokens would misreport what protection
    actually exists. Both directions fail loudly instead of drifting.
    """
    from identity.adapters.identity import (
        AllowAllHeaderAuthenticationAdapter,
        AllowAllIdentityAdapter,
        EnvoyHeaderAuthenticationAdapter,
        EnvoyHeaderIdentityAdapter,
    )
    from identity.adapters.jwks import JwksBearerAuthenticationAdapter, JwksIdentityAdapter

    verifies_signature = issubclass(cls, (JwksIdentityAdapter, JwksBearerAuthenticationAdapter))
    # JwksIdentityAdapter itself subclasses EnvoyHeaderIdentityAdapter (it reuses
    # its JIT-provisioning pipeline against claims it verified itself) — exclude
    # it explicitly so a legitimate oidc adapter is never flagged as Envoy-trusting.
    trusts_envoy = (
        issubclass(cls, (EnvoyHeaderIdentityAdapter, EnvoyHeaderAuthenticationAdapter))
        and not verifies_signature
    )
    is_allow_all = issubclass(cls, (AllowAllIdentityAdapter, AllowAllHeaderAuthenticationAdapter))
    name = f"{cls.__module__}.{cls.__qualname__}"

    if auth_mode == "envoy":
        return

    if trusts_envoy:
        raise ValueError(
            f"identity.adapter={name} trusts x-auth-* headers as already verified "
            f"by Envoy, but this host declared auth_mode={auth_mode!r} (no Envoy in "
            "front of it). Configure auth.mode: oidc for in-process JWT verification, "
            "or auth.mode: none to accept unauthenticated access explicitly — never "
            "point identity.adapter at an Envoy-trusting adapter here."
        )

    if auth_mode == "oidc" and not verifies_signature:
        raise ValueError(
            f"auth.mode: oidc requires an identity adapter that verifies a bearer "
            f"token's signature, but identity.adapter={name} does not. Configure "
            "identity.adapters.jwks.JwksIdentityAdapter (or "
            "JwksBearerAuthenticationAdapter for a header-only slot such as "
            "RAVN_API_AUTH), or set auth.mode: none to run without authentication "
            "explicitly instead of silently starting an unverified host."
        )

    if auth_mode == "none" and not is_allow_all:
        raise ValueError(
            f"auth.mode: none requires the explicit allow-all identity adapter, but "
            f"identity.adapter={name} is configured. Configure "
            "identity.adapters.identity.AllowAllIdentityAdapter (or "
            "AllowAllHeaderAuthenticationAdapter for a header-only slot), or set "
            "auth.mode: oidc if this adapter genuinely verifies tokens."
        )

    if auth_mode == "none":
        logger.warning(
            "authentication disabled (auth.mode: none): every caller is treated as admin"
        )


def _validate_authorization_adapter_class(cls: type, auth_mode: str) -> None:
    """Mirror of :func:`_validate_identity_adapter_class` for authorization.

    ``auth.mode: oidc`` requires the bundled Cedar policies (matching
    Kubernetes); ``auth.mode: none`` requires the explicit allow-all
    authorizer, for the same "claim must match reality" reason.
    """
    from identity.adapters.authorization import AllowAllAuthorizationAdapter
    from identity.adapters.cedar import CedarAuthorizationAdapter

    is_cedar = issubclass(cls, CedarAuthorizationAdapter)
    is_allow_all = issubclass(cls, AllowAllAuthorizationAdapter)
    name = f"{cls.__module__}.{cls.__qualname__}"

    if auth_mode == "envoy":
        return

    if auth_mode == "oidc" and not is_cedar:
        raise ValueError(
            f"auth.mode: oidc requires Cedar authorization (matching Kubernetes), but "
            f"authorization.adapter={name} is configured. Configure "
            "identity.adapters.cedar.CedarAuthorizationAdapter, or set auth.mode: none "
            "to run without authorization checks explicitly."
        )

    if auth_mode == "none" and not is_allow_all:
        raise ValueError(
            f"auth.mode: none requires the explicit allow-all authorizer, but "
            f"authorization.adapter={name} is configured. Configure "
            "identity.adapters.authorization.AllowAllAuthorizationAdapter, or set "
            "auth.mode: oidc if this adapter genuinely enforces policy."
        )


def create_identity_adapter(
    settings: ServiceSettings,
    user_repository,
    storage=None,
    tenant_service=None,
):
    """Create the shared identity adapter from dynamic config."""
    config = settings.identity
    cls = import_class(config.adapter)
    auth_mode = _get_auth_mode(settings)
    _validate_identity_adapter_class(cls, auth_mode)

    pat_config = getattr(settings, "pat", None)
    token_issuer_adapter = getattr(pat_config, "token_issuer_adapter", "")
    if auth_mode == "oidc" and token_issuer_adapter == _UNVERIFIABLE_TOKEN_ISSUER:
        raise ValueError(
            f"auth.mode: oidc cannot verify PATs issued by {token_issuer_adapter!r}: "
            "it signs with a local HS256 secret that no JWKS endpoint publishes. "
            "Configure pat.token_issuer_adapter to an IDP-backed issuer (e.g. "
            "niuu.adapters.keycloak_token_issuer.KeycloakTokenIssuer) that shares "
            "the OIDC issuer configured in auth.oidc.issuers."
        )

    kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
    kwargs = dict(kwargs)
    kwargs["user_repository"] = user_repository
    kwargs["role_mapping"] = settings.identity.role_mapping
    if storage is not None:
        kwargs["storage"] = storage
    if tenant_service is not None:
        kwargs["tenant_service"] = tenant_service
    instance = cls(**kwargs)
    logger.info("Identity adapter: %s", config.adapter.rsplit(".", 1)[-1])
    return instance


async def seed_development_identity(identity, user_repository) -> None:
    """Persist the configured dev principal and its admin membership at startup."""
    from identity.adapters.identity import AllowAllIdentityAdapter
    from identity.models import TenantMembership, TenantRole

    if not isinstance(identity, AllowAllIdentityAdapter):
        return
    principal = await identity.validate_token("allow-all")
    await identity.get_or_provision_user(principal)
    await user_repository.add_membership(
        TenantMembership(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            role=TenantRole.ADMIN,
        )
    )


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
    return import_class(settings.pat.validator_adapter)(
        **settings.pat.validator_kwargs,
        repo=pat_repository,
        cache_ttl=settings.pat.revocation_cache_ttl,
        revoked_cache_ttl=settings.pat.revoked_cache_ttl,
    )


def run_service_app(import_path: str, default_port: int) -> None:
    """Run a service app via uvicorn with simple env overrides."""
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", str(default_port)))
    uvicorn.run(import_path, host=host, port=port)
