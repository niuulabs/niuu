#!/usr/bin/env python3
"""Regenerate `vendor/envoy_authz_protos/` from upstream `.proto` sources.

Why this exists
----------------
`identity/adapters/envoy_authz.py` and `identity/authz_main.py` need exactly
one thing from the Envoy xDS ecosystem: the generated Python bindings for
`envoy.service.auth.v3` (the ext_authz gRPC service) and its transitive
message dependencies. The `xds-protos` PyPI wheel used to provide that, but
it also bundles its own copy of `opentelemetry/proto/**`, which collides
file-for-file with the real `opentelemetry-proto` package pulled in by the
`otel` extra. Whichever wheel installs last wins, and when `xds-protos` wins,
`opentelemetry.proto.common.v1.common_pb2.InstrumentationScope` fails to
import and OTLP export breaks at startup (see docs/site/operations/
observability.md).

Rather than fight install order, this repo vendors only the ~18 proto
modules the authz server actually needs (verified by walking the
`DESCRIPTOR.dependencies` closure of `external_auth_pb2` — see the assertion
in `tests/test_packaging_dependencies.py`) and drops the `xds-protos`
dependency entirely. No installed distribution then owns any file under
`opentelemetry/proto/`, so the collision cannot happen under any extras
combination, including CI's `--all-extras` test matrix.

`google.rpc.status_pb2` / `google.rpc.code_pb2` are NOT vendored here: they
already ship from `googleapis-common-protos`, a transitive dependency of
`a2a-sdk` that this project already installs unconditionally.

Usage
-----
    python3 scripts/generate_envoy_authz_protos.py

Requires network access (to fetch pinned `.proto` sources from GitHub) and
Python 3. The script creates its own throwaway virtualenv for `grpcio-tools`
so it does not touch the project's own dependency set; only `grpcio` (already
a base dependency, used elsewhere for gRPC clients) and `protobuf` (already a
transitive dependency of `a2a-sdk`) are needed at runtime.

To bump the pinned Envoy/xDS/validate revisions, edit the `_REF` constants
below and re-run.
"""

from __future__ import annotations

import glob
import re
import shutil
import subprocess
import tempfile
import venv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = REPO_ROOT / "vendor" / "envoy_authz_protos"

# Pinned source revisions. Bump deliberately and re-run this script; do not
# track a moving branch in the generated output.
ENVOY_REF = "v1.39.0"
XDS_REF = "main"  # cncf/xds has no tagged releases; pin a resolved commit below instead.
VALIDATE_REF = "main"  # bufbuild/protoc-gen-validate; pin a resolved commit below instead.

# grpcio-tools/protobuf versions used only to *generate* the code. Pinned to
# the project's `grpcio` floor (pyproject.toml `dependencies`): the
# `*_pb2_grpc.py` codegen embeds a minimum grpcio version check, so
# generating with a newer grpcio-tools than the project's grpcio floor would
# make the vendored stub refuse to import against the locked grpcio. The
# protobuf version matches the locked runtime version (see uv.lock) so the
# generated descriptors are compatible with what actually runs in prod.
GRPCIO_TOOLS_VERSION = "1.78.0"
PROTOBUF_VERSION = "6.33.5"

# The one proto with an actual gRPC service definition. Every other fetched
# file only produces message types; protoc still emits a trivial
# `*_pb2_grpc.py` for those (no services), which we discard to avoid
# vendoring 16 files that are pure boilerplate.
SERVICE_PROTO = "envoy/service/auth/v3/external_auth.proto"

# Entry point: everything else is discovered by walking `import "...";`
# statements transitively.
SEED_PROTOS = [SERVICE_PROTO]

IMPORT_RE = re.compile(r'^\s*import\s+(?:public\s+|weak\s+)?"([^"]+)"\s*;', re.MULTILINE)


def _source_for(path: str, venv_site_packages: Path) -> tuple[str, str, str] | None:
    """Return (repo, ref, path-in-repo) to fetch `path` from, or None to skip.

    `google/protobuf/*.proto` are the well-known types bundled with protoc
    itself. `google/rpc/*.proto` are read from the already-installed
    `googleapis-common-protos` package (present via `a2a-sdk`) purely so
    protoc can resolve the import at compile time — we do not vendor a
    generated `google/rpc/status_pb2.py`; the runtime one from
    `googleapis-common-protos` is used instead.
    """
    if path.startswith("envoy/"):
        return ("envoyproxy/envoy", ENVOY_REF, f"api/{path}")
    if path.startswith(("udpa/", "xds/")):
        return ("cncf/xds", XDS_REF, path)
    if path.startswith("validate/"):
        return ("bufbuild/protoc-gen-validate", VALIDATE_REF, path)
    if path.startswith("google/protobuf/"):
        return None
    if path.startswith("google/rpc/"):
        local = venv_site_packages / path
        if not local.exists():
            raise FileNotFoundError(
                f"{local} not found — is googleapis-common-protos installed? (`uv sync` first)"
            )
        return ("__local__", "", str(local))
    raise ValueError(f"no known source for proto import {path!r}")


def _fetch(repo: str, ref: str, path_in_repo: str) -> str:
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/{path_in_repo}"
    result = subprocess.run(["curl", "-sfL", url], capture_output=True, text=True, check=False)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"failed to fetch {url}: {result.stderr}")
    return result.stdout


def _collect_proto_sources(proto_src_dir: Path, venv_site_packages: Path) -> list[str]:
    """Fetch SEED_PROTOS and their transitive imports into proto_src_dir.

    Returns the list of proto paths that should be passed to protoc as
    compile targets (i.e. everything except google/rpc/*.proto, which is
    only present so imports resolve).
    """
    visited: set[str] = set()
    queue = list(SEED_PROTOS)
    compile_targets: list[str] = []

    while queue:
        path = queue.pop()
        if path in visited:
            continue
        visited.add(path)

        source = _source_for(path, venv_site_packages)
        dest = proto_src_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)

        if source is None:
            continue

        repo, ref, loc = source
        content = Path(loc).read_text() if repo == "__local__" else _fetch(repo, ref, loc)
        dest.write_text(content)

        if not path.startswith("google/rpc/"):
            compile_targets.append(path)

        for imported in IMPORT_RE.findall(content):
            if imported not in visited:
                queue.append(imported)

    return sorted(compile_targets)


def _make_gen_venv(tmp: Path) -> Path:
    gen_venv = tmp / "genenv"
    venv.EnvBuilder(with_pip=True).create(gen_venv)
    python = gen_venv / "bin" / "python"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "-q",
            f"grpcio-tools=={GRPCIO_TOOLS_VERSION}",
            f"protobuf=={PROTOBUF_VERSION}",
        ],
        check=True,
    )
    return python


def main() -> None:
    venv_site_packages_matches = glob.glob(str(REPO_ROOT / ".venv/lib/python3.*/site-packages"))
    if not venv_site_packages_matches:
        raise SystemExit(
            "no .venv found — run `uv sync --extra dev` first so "
            "googleapis-common-protos is available to read google/rpc/*.proto from"
        )
    venv_site_packages = Path(venv_site_packages_matches[0])

    with tempfile.TemporaryDirectory(prefix="envoy-authz-protogen-") as tmp_str:
        tmp = Path(tmp_str)
        proto_src = tmp / "proto_src"
        gen_out = tmp / "gen_out"
        proto_src.mkdir()
        gen_out.mkdir()

        compile_targets = _collect_proto_sources(proto_src, venv_site_packages)
        print(f"Fetched {len(compile_targets)} proto files:")
        for target in compile_targets:
            print(f"  {target}")

        python = _make_gen_venv(tmp)
        subprocess.run(
            [
                str(python),
                "-m",
                "grpc_tools.protoc",
                f"-I{proto_src}",
                f"--python_out={gen_out}",
                f"--pyi_out={gen_out}",
                f"--grpc_python_out={gen_out}",
                *compile_targets,
            ],
            check=True,
            cwd=REPO_ROOT,
        )

        if VENDOR_ROOT.exists():
            shutil.rmtree(VENDOR_ROOT)
        VENDOR_ROOT.mkdir(parents=True)

        for generated in sorted(gen_out.rglob("*")):
            if not generated.is_file():
                continue
            rel = generated.relative_to(gen_out)
            # Discard the trivial `*_pb2_grpc.py` stubs protoc emits for
            # proto files with no service definitions — only external_auth
            # has one.
            if rel.name.endswith("_pb2_grpc.py") and rel.as_posix() != (
                SERVICE_PROTO.replace(".proto", "_pb2_grpc.py")
            ):
                continue
            dest = VENDOR_ROOT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(generated, dest)

        # protoc does not emit __init__.py; add empty ones at every package
        # level so this vendors cleanly as a normal (non-namespace) package
        # tree, matching how xds-protos itself shipped. VENDOR_ROOT itself
        # (`envoy_authz_protos/`) is a filesystem grouping only, never
        # imported as a package, so it gets no __init__.py: `envoy`, `xds`,
        # `udpa`, `validate` are each registered as their own top-level
        # package (pyproject.toml `[tool.hatch.build.targets.wheel]` and
        # pytest's `pythonpath`) so imports stay `envoy.service.auth.v3...`,
        # matching upstream xds-protos and requiring no code changes in
        # identity/adapters/envoy_authz.py or identity/authz_main.py.
        for directory in sorted(VENDOR_ROOT.rglob("*")):
            if directory.is_dir():
                (directory / "__init__.py").touch()

    print(f"\nWrote vendored protos to {VENDOR_ROOT}")
    print(
        "Run `git status` to review, then `uv run pytest tests/test_adapters/test_envoy_authz.py "
        "tests/test_adapters/test_authz_main.py tests/test_packaging_dependencies.py` to verify."
    )


if __name__ == "__main__":
    main()
