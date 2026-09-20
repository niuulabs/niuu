"""Composition helpers for the Skuld runtime."""

from niuu.ports.evidence import ArtifactDigestResolver, EvidenceGateVerifier
from niuu.utils import import_class, resolve_secret_kwargs
from skuld.config import SkuldSettings


def create_evidence_verifier(settings: SkuldSettings) -> EvidenceGateVerifier | None:
    configuration = settings.workflow.evidence_verifier
    if configuration is None:
        return None
    verifier = import_class(configuration.adapter)(
        **resolve_secret_kwargs(configuration.kwargs, configuration.secret_kwargs_env)
    )
    if not isinstance(verifier, EvidenceGateVerifier):
        raise TypeError("Workflow evidence verifier must implement EvidenceGateVerifier")
    return verifier


def create_evidence_artifacts(settings: SkuldSettings) -> ArtifactDigestResolver | None:
    configuration = settings.workflow.evidence_artifacts
    if configuration is None:
        return None
    resolver = import_class(configuration.adapter)(
        **resolve_secret_kwargs(configuration.kwargs, configuration.secret_kwargs_env)
    )
    if not isinstance(resolver, ArtifactDigestResolver):
        raise TypeError("Workflow evidence artifacts must implement ArtifactDigestResolver")
    return resolver
