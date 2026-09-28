"""Build and test the five stdlib backports from checksum-pinned upstream inputs."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

security = Path(__file__).resolve().parent
manifest = json.loads((security / "manifest.json").read_text())
version = manifest["python_version"]
url = f"https://github.com/python/cpython/archive/refs/tags/v{version}.tar.gz"
with urllib.request.urlopen(url, timeout=120) as response:
    archive = response.read()
if hashlib.sha256(archive).hexdigest() != manifest["source_sha256"]:
    raise RuntimeError("CPython source archive checksum mismatch")
with tarfile.open(fileobj=io.BytesIO(archive)) as source:
    source.extractall("/tmp/cpython-backports", filter="data")
source_dir = Path(f"/tmp/cpython-backports/cpython-{version}")
for item in manifest["patches"]:
    patch = security / f"{item['commit']}.patch"
    if hashlib.sha256(patch.read_bytes()).hexdigest() != item["sha256"]:
        raise RuntimeError(f"Upstream patch checksum mismatch: {patch.name}")
    subprocess.run(
        ["patch", "--batch", "--fuzz=0", "-p1", "-i", str(patch)],
        cwd=source_dir,
        check=True,
    )
subprocess.run(
    [
        sys.executable,
        "-m",
        "test",
        "-j",
        "2",
        "test_poplib",
        "test_urllib2",
        "test_zipfile",
        "test_codecs",
        "test_unicodedata",
        "test_tarfile",
    ],
    cwd=source_dir,
    env={**os.environ, "PYTHONPATH": str(source_dir / "Lib")},
    check=True,
)
for name, hashes in manifest["files"].items():
    source_file = source_dir / "Lib" / name
    if hashlib.sha256(source_file.read_bytes()).hexdigest() != hashes["patched"]:
        raise RuntimeError(f"Patched stdlib checksum mismatch: {name}")
    destination = Path(sys.argv[1]) / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_file, destination)
