# OpenShell Python SDK source

This directory replaces the previously committed wheel with reviewable Python
source, built locally by uv. No GitHub release download is needed during a
repository or container install.

Upstream: https://github.com/jve/OpenShell/tree/e9825820d9d8ea809f7de3b4160c105f83848d95

The `openshell/` package (including generated protobuf Python bindings and type
stubs) and `LICENSE` are copied unchanged from the
`openshell-0.1.8.dev40+ge9825820d-py3-none-any.whl` asset of the
[`niuu-sdk-e9825820d` release](https://github.com/jve/OpenShell/releases/tag/niuu-sdk-e9825820d).
The original wheel SHA-256 is
`434731a0f8988615c6dc14f5e1ec9d533d503f983ee27dd1a36fe0e5528412ec`.
All 39 package files are preserved byte for byte, including upstream tests.
The local `pyproject.toml` records the wheel's version, Python requirement and
runtime dependencies, and uses Hatchling to package the existing source without
requiring an upstream Git checkout or protobuf generation tools.

To update, download the desired upstream wheel outside the repository, verify
its SHA-256 against the release pin, and replace `openshell/` and `LICENSE` with
its source files. Update this provenance, the local package metadata and the
root project's `openshell` extra URL/hash together, then run `uv lock`.
Do not commit the downloaded wheel. The published niuu extra still points pip
at the upstream release; uv uses this directory through `[tool.uv.sources]`.

Build and check the vendored dependency with:

```sh
uv build --wheel --out-dir /tmp/openshell-build vendor/openshell
uv run --frozen --extra dev pytest tests/test_containers/test_vendored_openshell_source.py
```
