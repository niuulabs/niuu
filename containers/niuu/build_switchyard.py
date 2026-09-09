"""Build the exact locked Switchyard source into a container-local wheel."""

import hashlib
import json
import platform
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def main() -> None:
    lock = tomllib.loads(Path("uv.lock").read_text())
    (package,) = [p for p in lock["package"] if p["name"] == "nemo-switchyard"]
    source = urlsplit(package["source"]["git"])
    revision = source.fragment
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Switchyard must be locked to a full Git commit")
    repository = urlunsplit((source.scheme, source.netloc, source.path, "", ""))
    output = Path("/wheels")
    output.mkdir()
    with tempfile.TemporaryDirectory(prefix="switchyard-") as directory:
        subprocess.run(["git", "init", directory], check=True)
        subprocess.run(
            ["git", "-C", directory, "fetch", "--depth=1", repository, revision], check=True
        )
        subprocess.run(["git", "-C", directory, "checkout", "--detach", "FETCH_HEAD"], check=True)
        actual = subprocess.check_output(
            ["git", "-C", directory, "rev-parse", "HEAD"], text=True
        ).strip()
        if actual != revision:
            raise ValueError("Fetched Switchyard revision does not match uv.lock")
        subprocess.run(
            [
                "uv",
                "build",
                "--wheel",
                "--python",
                "/usr/local/bin/python",
                "--no-python-downloads",
                "--config-setting=build-args=--locked",
                "--build-constraints",
                "switchyard-build-constraints.txt",
                "--out-dir",
                str(output),
                directory,
            ],
            check=True,
        )
    (wheel,) = output.glob("*.whl")
    with wheel.open("rb") as wheel_file:
        digest = hashlib.file_digest(wheel_file, "sha256").hexdigest()
    (output / "switchyard-build.json").write_text(
        json.dumps(
            {
                "repository": repository,
                "revision": revision,
                "version": package["version"],
                "architecture": platform.machine(),
                "wheel": wheel.name,
                "sha256": digest,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
