"""Build a GitHub Pages docs bundle with an optional branch preview overlay."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SITE_URL = "https://niuulabs.github.io/volundr/"
MKDOCS_MATERIAL_SPEC = "mkdocs-material==9.7.5"


def run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def capture(command: list[str], *, cwd: Path) -> str:
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def latest_version(worktree: Path) -> str:
    version = capture(
        [
            "bash",
            "-lc",
            "git tag -l 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname "
            "| grep -v -- '-' | head -1 | sed 's/^v//' || true",
        ],
        cwd=worktree,
    )
    return version or "0.0.0-dev"


def inject_version(worktree: Path, version: str) -> None:
    pyproject = worktree / "pyproject.toml"
    text = pyproject.read_text()
    updated = re.sub(r'^version = ".*"$', f'version = "{version}"', text, flags=re.MULTILINE)
    pyproject.write_text(updated)


def write_site_url_override(worktree: Path, site_url: str) -> Path:
    config_path = worktree / ".mkdocs.build.yml"
    text = (worktree / "mkdocs.yml").read_text()
    if DEFAULT_SITE_URL not in text:
        msg = f"Expected to find default site_url {DEFAULT_SITE_URL!r} in mkdocs.yml"
        raise RuntimeError(msg)
    config_path.write_text(text.replace(DEFAULT_SITE_URL, site_url))
    return config_path


def build_ref(ref: str, destination: Path, *, site_url: str | None) -> None:
    scratch = Path(tempfile.mkdtemp(prefix="docs-build-"))
    worktree = scratch / "repo"
    try:
        run(["git", "worktree", "add", "--detach", str(worktree), ref], cwd=REPO_ROOT)
        version = latest_version(worktree)
        inject_version(worktree, version)
        run(
            [
                "uv",
                "run",
                "--extra",
                "dev",
                "python",
                "scripts/extract_openapi.py",
                "-o",
                "docs/site/openapi.json",
            ],
            cwd=worktree,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if site_url:
            config_path = write_site_url_override(worktree, site_url)
            run(
                [
                    "uvx",
                    "--from",
                    MKDOCS_MATERIAL_SPEC,
                    "mkdocs",
                    "build",
                    "--strict",
                    "-f",
                    str(config_path),
                    "--site-dir",
                    str(destination),
                ],
                cwd=worktree,
            )
            return
        run(
            [
                "uvx",
                "--from",
                MKDOCS_MATERIAL_SPEC,
                "mkdocs",
                "build",
                "--strict",
                "--site-dir",
                str(destination),
            ],
            cwd=worktree,
        )
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)], cwd=REPO_ROOT, check=False
        )
        shutil.rmtree(scratch, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, help="Final combined site directory")
    parser.add_argument("--root-ref", required=True, help="Git ref to build at site root")
    parser.add_argument("--preview-ref", help="Git ref to build as a preview overlay")
    parser.add_argument(
        "--preview-subdir", help="Subdirectory inside the final site for the preview build"
    )
    parser.add_argument("--preview-site-url", help="site_url to bake into the preview build")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    build_ref(args.root_ref, output_dir, site_url=None)

    if not args.preview_ref:
        return
    if not args.preview_subdir or not args.preview_site_url:
        msg = "preview-subdir and preview-site-url are required when preview-ref is provided"
        raise RuntimeError(msg)

    preview_dir = Path(tempfile.mkdtemp(prefix="docs-preview-site-"))
    try:
        build_ref(args.preview_ref, preview_dir, site_url=args.preview_site_url)
        overlay_target = output_dir / args.preview_subdir
        if overlay_target.exists():
            shutil.rmtree(overlay_target)
        shutil.copytree(preview_dir, overlay_target)
    finally:
        shutil.rmtree(preview_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
