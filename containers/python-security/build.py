"""Build and test the stdlib backports from checksum-pinned upstream inputs."""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

security = Path(__file__).resolve().parent


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sections(patch: str) -> dict[str, str]:
    """Split a git patch into its per-file sections, keyed by file path."""
    parts = re.split(r"(?m)^(?=diff --git )", patch)
    return {part.split()[2].removeprefix("a/"): part for part in parts[1:]}


def check_adaptation(upstream: str, adapted: str, adaptation: dict) -> None:
    """Fail unless the adapted patch differs from upstream exactly as declared.

    Every upstream file section must be copied byte for byte, except the
    declared dropped files; the only added sections are the declared adapted
    files. The adaptation cannot change anything else unnoticed.
    """
    original = sections(upstream)
    changed = sections(adapted)
    dropped = set(adaptation["dropped_files"])
    added = set(adaptation["adapted_files"])
    if not dropped <= original.keys() or added & original.keys():
        raise RuntimeError("Adapted patch declares files that do not match upstream")
    if changed.keys() != (original.keys() - dropped) | added:
        raise RuntimeError("Adapted patch touches files other than the declared ones")
    for name in original.keys() - dropped:
        if changed[name] != original[name]:
            raise RuntimeError(f"Adapted patch changes upstream section: {name}")


def checked_patch(item: dict) -> Path:
    """Return the patch to apply, after every pinned checksum has matched."""
    upstream = security / f"{item['commit']}.patch"
    if sha256(upstream.read_bytes()) != item["sha256"]:
        raise RuntimeError(f"Upstream patch checksum mismatch: {upstream.name}")
    adaptation = item.get("adapted")
    if adaptation is None:
        return upstream
    adapted = security / adaptation["patch"]
    if sha256(adapted.read_bytes()) != adaptation["sha256"]:
        raise RuntimeError(f"Adapted patch checksum mismatch: {adapted.name}")
    check_adaptation(
        upstream.read_text(encoding="utf-8"), adapted.read_text(encoding="utf-8"), adaptation
    )
    return adapted


def main(destination: Path) -> None:
    manifest = json.loads((security / "manifest.json").read_text())
    patches = [checked_patch(item) for item in manifest["patches"]]
    version = manifest["python_version"]
    url = f"https://github.com/python/cpython/archive/refs/tags/v{version}.tar.gz"
    with urllib.request.urlopen(url, timeout=120) as response:
        archive = response.read()
    if sha256(archive) != manifest["source_sha256"]:
        raise RuntimeError("CPython source archive checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="cpython-backports-") as work:
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(work, filter="data")
        source_dir = Path(work) / f"cpython-{version}"
        for patch in patches:
            subprocess.run(
                ["patch", "--batch", "--fuzz=0", "-p1", "-i", str(patch)],
                cwd=source_dir,
                check=True,
            )
        for arguments in manifest["tests"]:
            subprocess.run(
                [sys.executable, "-m", "test", *arguments],
                cwd=source_dir,
                env={**os.environ, "PYTHONPATH": str(source_dir / "Lib")},
                check=True,
            )
        for name, hashes in manifest["files"].items():
            source_file = source_dir / "Lib" / name
            if sha256(source_file.read_bytes()) != hashes["patched"]:
                raise RuntimeError(f"Patched stdlib checksum mismatch: {name}")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, target)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
