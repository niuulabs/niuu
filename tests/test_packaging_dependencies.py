"""Guard against the opentelemetry/proto/** file collision regressing.

The `xds-protos` wheel used to be a base dependency (needed only for the
`envoy.service.auth.v3` ext_authz bindings — see
`identity/adapters/envoy_authz.py`), but it also bundled its own copy of
`opentelemetry/proto/**`, which collides file-for-file with the real
`opentelemetry-proto` package installed by the `otel` extra. Whichever wheel
installed last won, and when `xds-protos` won, OTLP export broke at startup
with an ImportError on `InstrumentationScope` (see
docs/site/operations/observability.md and
vendor/envoy_authz_protos/README.md).

`xds-protos` was removed in favor of vendoring only the ~18 proto modules
`envoy.service.auth.v3.external_auth_pb2` actually needs (see
`scripts/generate_envoy_authz_protos.py`). These tests assert that fix holds:
no installed distribution may claim a file that another one also claims
under `opentelemetry/proto/`, and the otel extra's import path works when
installed.
"""

from __future__ import annotations

from importlib.metadata import distributions

import pytest


def _opentelemetry_proto_file_owners() -> dict[str, set[str]]:
    """Map each installed file path under opentelemetry/proto/ to its owning distribution names."""
    owners: dict[str, set[str]] = {}
    for dist in distributions():
        name = dist.metadata["Name"]
        for file in dist.files or ():
            path = file.as_posix()
            if not path.startswith("opentelemetry/proto/"):
                continue
            owners.setdefault(path, set()).add(name)
    return owners


def test_no_two_distributions_own_the_same_opentelemetry_proto_file() -> None:
    """Whichever wheel installs last silently wins a same-path collision; there must be none."""
    owners = _opentelemetry_proto_file_owners()

    collisions = {path: names for path, names in owners.items() if len(names) > 1}

    assert not collisions, (
        "multiple installed distributions own the same opentelemetry/proto/ file, "
        f"so install order silently decides which wins: {collisions}. "
        "A wheel other than opentelemetry-proto is bundling its own copy of the "
        "OpenTelemetry proto modules — see vendor/envoy_authz_protos/README.md "
        "for the xds-protos incident this guards against."
    )


def test_xds_protos_is_not_installed() -> None:
    """xds-protos is the known historical source of the collision; it must stay removed.

    envoy.service.auth.v3 bindings come from vendor/envoy_authz_protos/ instead
    (see scripts/generate_envoy_authz_protos.py). Reintroducing xds-protos as a
    dependency reintroduces the collision this module guards against.
    """
    installed_names = {dist.metadata["Name"] for dist in distributions()}

    assert "xds-protos" not in installed_names


def test_instrumentation_scope_imports_when_otel_extra_installed() -> None:
    """The otel extra's OTLP proto types must import; this is what the collision broke.

    Skips cleanly (rather than failing) when the otel extra is not installed,
    per the no-fallbacks rule's "protocol-level optional read" escape: absence
    of an optional extra is an expected steady state here, not a degraded path.
    """
    otel_proto = pytest.importorskip(
        "opentelemetry.proto.common.v1.common_pb2",
        reason="otel extra not installed",
    )

    assert hasattr(otel_proto, "InstrumentationScope")
    scope = otel_proto.InstrumentationScope(name="test-packaging-guard")
    assert scope.name == "test-packaging-guard"


def test_envoy_authz_bindings_import_independently_of_otel() -> None:
    """The vendored ext_authz bindings must not depend on the otel extra being installed."""
    from envoy.service.auth.v3 import external_auth_pb2, external_auth_pb2_grpc

    assert external_auth_pb2.CheckRequest is not None
    assert external_auth_pb2_grpc.AuthorizationServicer is not None
