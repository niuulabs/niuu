"""Bundle a complete offline Python runtime in one executable with PyApp."""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
PYAPP_VERSION = "0.31.0"
PYAPP_SHA256 = "16bc883dd936584eef8d0d838feef3e839c340f201f36cc05eb810f29df92c22"
PYTHON_VERSION = "3.12"


def run(*command: str | Path, **kwargs) -> None:
    subprocess.run([str(arg) for arg in command], check=True, **kwargs)


def pyapp_source(work: Path) -> Path:
    archive = work / "source.tar.gz"
    if not archive.exists():
        url = f"https://github.com/ofek/pyapp/releases/download/v{PYAPP_VERSION}/source.tar.gz"
        with urlopen(url) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != PYAPP_SHA256:
        raise ValueError(f"PyApp source checksum mismatch: {archive}; remove it and retry")
    with tarfile.open(archive) as source:
        source.extractall(work, filter="data")
    return work / f"pyapp-v{PYAPP_VERSION}"


def relocate_postgres(root: Path) -> None:
    """Keep native PostgreSQL dependencies relative to the relocated library tree."""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as source:
            magic = source.read(4)
        if platform.system() == "Linux" and magic == b"\x7fELF":
            relative = os.path.relpath(root / "lib", path.parent)
            rpath = "$ORIGIN" if relative == "." else f"$ORIGIN/{relative}"
            run("patchelf", "--set-rpath", rpath, path)
        if platform.system() == "Darwin" and magic in {b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"}:
            run("codesign", "--force", "--sign", "-", path)


def build(name: str, output: Path) -> Path:
    work = ROOT / "build" / "pyapp"
    work.mkdir(parents=True, exist_ok=True)
    source = pyapp_source(work)
    payload = work / "python"
    managed_python = Path(
        subprocess.check_output(
            ["uv", "python", "find", "--system", "--managed-python", PYTHON_VERSION], text=True
        ).strip()
    ).resolve()
    if payload.exists():
        shutil.rmtree(payload)
    shutil.copytree(managed_python.parent.parent, payload, symlinks=True)
    python = payload / "bin" / "python3"
    requirements = work / "requirements.txt"
    run(
        "uv",
        "export",
        "--frozen",
        "--no-dev",
        "--extra",
        "cli",
        "--no-emit-project",
        "--format",
        "requirements-txt",
        "--output-file",
        requirements,
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
    )
    run(
        "uv",
        "pip",
        "sync",
        "--python",
        python,
        "--compile-bytecode",
        "--require-hashes",
        "--break-system-packages",
        requirements,
    )
    wheels = work / "wheels"
    wheels.mkdir(exist_ok=True)
    run("uv", "build", "--wheel", "--out-dir", wheels, cwd=ROOT)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    wheel = wheels / f"{project['name'].replace('-', '_')}-{project['version']}-py3-none-any.whl"
    run(
        "uv",
        "pip",
        "install",
        "--python",
        python,
        "--no-deps",
        "--compile-bytecode",
        "--break-system-packages",
        wheel,
    )
    site = payload / "lib" / f"python{PYTHON_VERSION}" / "site-packages"
    shutil.copytree(ROOT / "web-next/apps/niuu/dist", site / "cli/web/dist", dirs_exist_ok=True)
    postgres = site / "niuu/pginstall"
    shutil.copytree(ROOT / "build/pginstall", postgres, symlinks=True)
    relocate_postgres(postgres)
    distribution = source / "distribution.tar.zst"
    uncompressed = source / "distribution.tar"
    with tarfile.open(uncompressed, "w") as archive:
        archive.add(payload, arcname="python")
    run("zstd", "-1", "-T0", "-f", uncompressed, "-o", distribution)
    uncompressed.unlink()
    env = {
        **os.environ,
        "PYAPP_PROJECT_NAME": name,
        "PYAPP_PROJECT_VERSION": project["version"],
        "PYAPP_PYTHON_VERSION": PYTHON_VERSION,
        "PYAPP_DISTRIBUTION_PATH": "distribution.tar.zst",
        "PYAPP_DISTRIBUTION_PATH_PREFIX": "python",
        "PYAPP_DISTRIBUTION_PYTHON_PATH": "bin/python3",
        "PYAPP_DISTRIBUTION_SITE_PACKAGES_PATH": f"lib/python{PYTHON_VERSION}/site-packages",
        "PYAPP_FULL_ISOLATION": "true",
        "PYAPP_SKIP_INSTALL": "true",
        "PYAPP_EXEC_SPEC": "niuu.__main__:main",
        "PYAPP_SELF_COMMAND": "none",
        "CARGO_TARGET_DIR": str(ROOT / ".build-cache" / "pyapp-target"),
    }
    # Cache Rust dependencies, but always embed this build's payload afresh.
    run("cargo", "clean", "--package", "pyapp", "--release", cwd=source, env=env)
    run("cargo", "build", "--release", "--locked", "--timings", cwd=source, env=env)
    machine = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    output.mkdir(parents=True, exist_ok=True)
    binary = output / f"{name}-{platform.system().lower()}-{machine}"
    shutil.copy2(Path(env["CARGO_TARGET_DIR"]) / "release" / "pyapp", binary)
    shutil.copy2(
        Path(env["CARGO_TARGET_DIR"]) / "cargo-timings/cargo-timing.html",
        output / "launcher-timing.html",
    )
    run("cargo", "clean", "--package", "pyapp", "--release", cwd=source, env=env)
    print(f"Built {binary} ({binary.stat().st_size:,} bytes)", flush=True)
    return binary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="niuu")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    build(args.name, args.output_dir)
