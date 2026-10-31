"""Install verified backports only over the exact supported upstream modules."""

import hashlib
import json
import shutil
import sys
import sysconfig
from pathlib import Path


def install(source: Path, stdlib: Path, manifest: dict) -> None:
    for name, hashes in manifest["files"].items():
        if hashlib.sha256((stdlib / name).read_bytes()).hexdigest() != hashes["original"]:
            raise RuntimeError(f"Unsupported original stdlib module: {name}")
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != hashes["patched"]:
            raise RuntimeError(f"Invalid backported stdlib module: {name}")
    for name in manifest["files"]:
        destination = stdlib / name
        shutil.copyfile(source / name, destination)
        for cached in (destination.parent / "__pycache__").glob(f"{destination.stem}.*.pyc"):
            cached.unlink()


if __name__ == "__main__":
    manifest = json.loads(Path(__file__).with_name("manifest.json").read_text())
    if sys.version.split()[0] != manifest["python_version"]:
        raise RuntimeError(
            "Python version changed; review the security backports before rebuilding"
        )
    install(Path(sys.argv[1]), Path(sysconfig.get_path("stdlib")), manifest)
