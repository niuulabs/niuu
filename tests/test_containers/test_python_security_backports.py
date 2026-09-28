"""Backport installation and VEX must fail closed on unrelated runtime evidence."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "containers" / "python-security"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_install_validates_all_modules_before_changing_anything(tmp_path):
    installer = load("install")
    source = tmp_path / "source"
    stdlib = tmp_path / "stdlib"
    source.mkdir()
    stdlib.mkdir()
    manifest = {"files": {}}
    for name in ("first.py", "second.py"):
        (source / name).write_bytes(b"patched")
        (stdlib / name).write_bytes(b"original")
        manifest["files"][name] = {
            "original": hashlib.sha256(b"original").hexdigest(),
            "patched": hashlib.sha256(b"patched").hexdigest(),
        }
    (stdlib / "second.py").write_bytes(b"unexpected upstream change")
    with pytest.raises(RuntimeError, match="Unsupported original"):
        installer.install(source, stdlib, manifest)
    assert (stdlib / "first.py").read_bytes() == b"original"

    (stdlib / "second.py").write_bytes(b"original")
    (source / "second.py").write_bytes(b"corrupt backport")
    with pytest.raises(RuntimeError, match="Invalid backported"):
        installer.install(source, stdlib, manifest)
    assert (stdlib / "first.py").read_bytes() == b"original"

    (source / "second.py").write_bytes(b"patched")
    cache = stdlib / "__pycache__"
    cache.mkdir()
    (cache / "first.cpython-314.pyc").write_bytes(b"stale")
    installer.install(source, stdlib, manifest)
    assert all((stdlib / name).read_bytes() == b"patched" for name in manifest["files"])
    assert not list(cache.iterdir())


@pytest.fixture
def vex_inputs():
    import json

    manifest = json.loads((ROOT / "manifest.json").read_text())
    image = "ghcr.io/niuulabs/niuu@sha256:" + "a" * 64
    evidence = {
        "python_version": manifest["python_version"],
        "files": {name: item["patched"] for name, item in manifest["files"].items()},
        "cves": manifest["cves"],
        "executable": "/usr/local/bin/python3.14",
        "library": "/usr/local/lib/libpython3.14.so.1.0",
        "image": image,
        "patches": [item["commit"] for item in manifest["patches"]],
    }
    scan = {
        "source": {"target": {"repoDigests": [image]}},
        "matches": [
            {
                "vulnerability": {"id": cve},
                "artifact": {
                    "name": "python",
                    "type": "binary",
                    "version": "3.14.7",
                    "purl": "pkg:generic/python@3.14.7",
                    "locations": [
                        {
                            "path": "/usr/local/bin/python3.14",
                            "annotations": {"evidence": "primary"},
                        },
                        {
                            "path": "/usr/local/lib/libpython3.14.so.1.0",
                            "annotations": {"evidence": "supporting"},
                        },
                    ],
                },
            }
            for cve in [*manifest["cves"], "CVE-2099-99999"]
        ],
    }
    return image, evidence, scan, manifest


@pytest.mark.parametrize("library_evidence", ["primary", "supporting"])
def test_vex_is_limited_to_verified_image_component_and_six_cves(vex_inputs, library_evidence):
    for match in vex_inputs[2]["matches"]:
        match["artifact"]["locations"][1]["annotations"]["evidence"] = library_evidence
    result = load("vex").document(*vex_inputs)
    image, _, _, manifest = vex_inputs
    assert {s["vulnerability"]["name"] for s in result["statements"]} == set(manifest["cves"])
    assert len(result["statements"]) == 6
    for statement in result["statements"]:
        assert statement["status"] == "fixed"
        assert image.split("@", 1)[1] in statement["products"][0]["@id"]
        assert statement["products"][0]["subcomponents"] == [{"@id": "pkg:generic/python@3.14.7"}]


@pytest.mark.parametrize(
    "invalid",
    [
        "digest",
        "hash",
        "version",
        "cves",
        "scan",
        "runtime",
        "extra_runtime",
        "no_primary",
        "unknown",
        "library",
    ],
)
def test_vex_rejects_unverified_evidence(vex_inputs, invalid):
    image, evidence, scan, manifest = vex_inputs
    if invalid == "digest":
        image = "ghcr.io/niuulabs/niuu:dev"
    elif invalid == "hash":
        evidence["files"]["poplib.py"] = "wrong"
    elif invalid == "version":
        evidence["python_version"] = "3.14.6"
    elif invalid == "cves":
        evidence["cves"] = []
    elif invalid == "scan":
        scan["source"]["target"]["repoDigests"] = []
    elif invalid == "runtime":
        scan["matches"][0]["artifact"]["locations"] = [{"path": "/other/python3.14"}]
    elif invalid == "extra_runtime":
        scan["matches"][0]["artifact"]["locations"].append(
            {"path": "/other/python3.14", "annotations": {"evidence": "primary"}}
        )
    elif invalid == "library":
        scan["matches"][0]["artifact"]["locations"][1]["path"] = "/other/libpython3.14.so.1.0"
    elif invalid == "unknown":
        scan["matches"][0]["artifact"]["locations"][0].pop("annotations")
    elif invalid == "no_primary":
        scan["matches"][0]["artifact"]["locations"].pop(0)
    with pytest.raises(ValueError):
        load("vex").document(image, evidence, scan, manifest)
