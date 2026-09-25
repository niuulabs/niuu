# Vendored Envoy ext_authz protos

Generated Python bindings for exactly the proto messages and the one gRPC
service that `identity/adapters/envoy_authz.py` and `identity/authz_main.py`
need to implement Envoy's `envoy.service.auth.v3.Authorization` external
authorization gRPC API.

## Why this is vendored instead of a dependency

The `xds-protos` PyPI wheel (previously a base dependency of this project)
provides `envoy.service.auth.v3` bindings, but it also bundles its own copy
of `opentelemetry/proto/**`. Those files collide file-for-file with the real
`opentelemetry-proto` package that the `otel` extra installs — whichever
wheel is installed last wins, and when `xds-protos` wins,
`opentelemetry.proto.common.v1.common_pb2.InstrumentationScope` fails to
import and OTLP export breaks at startup. This also affected CI, whose test
jobs install `--all-extras` (both `otel` and, via the then-base dependency,
`xds-protos`) with no control over install order.

This directory vendors only the ~18 proto modules actually reachable from
`envoy.service.auth.v3.external_auth_pb2`'s dependency closure (verified in
`tests/test_packaging_dependencies.py`), so `xds-protos` is no longer a
dependency at all and no installed distribution can ever own a file under
`opentelemetry/proto/` except `opentelemetry-proto` itself.

`google.rpc.status_pb2` / `google.rpc.code_pb2` (also used by
`envoy_authz.py`) are **not** vendored here — they already ship from
`googleapis-common-protos`, a transitive dependency of `a2a-sdk` that this
project installs unconditionally regardless of the `otel` extra.

## Layout

Import paths are kept identical to what `xds-protos` provided
(`envoy.service.auth.v3.external_auth_pb2`, etc.), so no code changes were
needed in `identity/adapters/envoy_authz.py` or `identity/authz_main.py`.
This directory (`envoy_authz_protos/`) is a filesystem grouping only and is
never itself imported; `envoy/`, `xds/`, `udpa/`, and `validate/` are each
registered as their own top-level package:

- in `pyproject.toml`'s `[tool.hatch.build.targets.wheel]` `packages` list,
  so they ship in the built wheel;
- in `pyproject.toml`'s `[tool.pytest.ini_options]` `pythonpath`, so they
  resolve when running tests straight from the source tree.

Only `envoy/service/auth/v3/external_auth_pb2_grpc.py` is kept among the
`*_pb2_grpc.py` files protoc generates — it is the only proto here with an
actual service definition; the rest would be empty boilerplate.

## Regenerating

Run from the repo root with a synced `.venv` (`uv sync --extra dev`) and
network access:

```bash
python3 scripts/generate_envoy_authz_protos.py
```

This re-fetches the pinned `.proto` sources (see the `_REF` constants in
that script), regenerates every file in this directory from scratch, and
prints the targeted tests to run afterward to verify the result.

Do this when bumping the pinned Envoy/xDS/protoc-gen-validate revisions, or
when Envoy's ext_authz v3 API gains a field this adapter needs.
