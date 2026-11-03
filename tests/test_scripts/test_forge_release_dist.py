"""A Forge release always carries its web app (the promotion-ten 404, 2026-10-31)."""

from __future__ import annotations

import importlib.util
import io
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "forge_release_dist", ROOT / "scripts/forge_release_dist.py"
)
release_dist = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_dist)

HEALTH = "http://127.0.0.1:9/health"


@pytest.fixture(autouse=True)
def serving_elsewhere(monkeypatch):
    """The host serves some other release unless a test says otherwise."""
    monkeypatch.setattr(release_dist, "serving_build", lambda url: "forge-live")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo: Path, path: str, text: str) -> str:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)
    git(repo, "add", path)
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", path)
    return git(repo, "rev-parse", "HEAD")


def build(release: Path) -> None:
    dist = release / release_dist.DIST
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>")
    (dist / "assets" / "app.js").write_text("x")


@pytest.fixture
def releases(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(repo, ".gitignore", "dist/\n")  # as in the real repo: a build is never tracked
    first = commit(repo, "web-next/app.ts", "one")
    commit(repo, "src/api.py", "api change")

    def release(name: str, revision: str = "HEAD") -> Path:
        path = tmp_path / name
        git(repo, "worktree", "add", "-q", "--detach", str(path), revision)
        return path

    return repo, first, release


def test_an_unchanged_web_app_is_copied_from_the_previous_release(releases):
    _, first, release = releases
    previous, new = release("nine", first), release("ten")
    build(previous)

    assert "dist copied from nine" in release_dist.place(new, previous, health_url=HEALTH)

    release_dist.check(new)
    assert (new / release_dist.DIST / "assets" / "app.js").read_text() == "x"


def test_an_empty_dist_folder_is_replaced_not_trusted(releases):
    _, first, release = releases
    previous, new = release("nine", first), release("ten")
    build(previous)
    (new / release_dist.DIST).mkdir(parents=True)

    release_dist.place(new, previous, health_url=HEALTH)

    release_dist.check(new)


def test_a_changed_web_app_must_be_built(releases):
    repo, first, release = releases
    commit(repo, "web-next/app.ts", "two")
    previous, new = release("nine", first), release("ten")
    build(previous)

    with pytest.raises(release_dist.DistError, match="web-next changed since nine"):
        release_dist.place(new, previous, health_url=HEALTH)


def test_a_dist_just_built_is_kept(releases):
    _, first, release = releases
    previous, new = release("nine", first), release("ten")
    build(new)

    kept = release_dist.place(new, previous, health_url=HEALTH, built=True)
    assert kept == "ten: built dist kept"


def test_a_stale_dist_is_not_trusted_without_built(releases):
    repo, first, release = releases
    commit(repo, "web-next/app.ts", "two")
    previous, new = release("nine", first), release("ten")
    build(previous)
    build(new)  # e.g. inherited by copying a release dir

    with pytest.raises(release_dist.DistError, match="web-next changed since nine"):
        release_dist.place(new, previous, health_url=HEALTH)


@pytest.mark.parametrize("dirty", ["nine", "ten"])
def test_uncommitted_web_changes_are_refused(releases, dirty):
    _, first, release = releases
    trees = {"nine": release("nine", first), "ten": release("ten")}
    build(trees["nine"])
    (trees[dirty] / "web-next" / "app.ts").write_text("edited in place")

    with pytest.raises(release_dist.DistError, match="uncommitted web-next"):
        release_dist.place(trees["ten"], trees["nine"], health_url=HEALTH)


@pytest.mark.parametrize("state", ["missing", "no index", "empty assets"])
def test_check_refuses_a_release_without_its_web_app(releases, state, capsys):
    _, _, release = releases
    new = release("ten")
    if state != "missing":
        build(new)
    if state == "no index":
        (new / release_dist.DIST / "index.html").unlink()
    if state == "empty assets":
        (new / release_dist.DIST / "assets" / "app.js").unlink()

    assert release_dist.main(["check", str(new)]) == 1
    assert "has no web app" in capsys.readouterr().err


def test_the_serving_release_is_never_touched(releases, monkeypatch):
    _, first, release = releases
    previous, new = release("nine", first), release("ten")
    build(previous)
    monkeypatch.setattr(release_dist, "serving_build", lambda url: "ten")

    with pytest.raises(release_dist.DistError, match="ten is the release this host is serving"):
        release_dist.place(new, previous, health_url=HEALTH)
    assert not (new / release_dist.DIST).exists()


def test_a_release_a_process_runs_in_is_never_touched(releases):
    _, first, release = releases
    previous, new = release("nine", first), release("ten")
    build(previous)
    (new / "src").mkdir(exist_ok=True)
    with subprocess.Popen(["sleep", "30"], cwd=new / "src") as running:
        try:
            with pytest.raises(release_dist.DistError, match=f"{running.pid}"):
                release_dist.place(new, previous, health_url=HEALTH)
        finally:
            running.kill()


def test_an_unreadable_health_refuses_everything(releases, monkeypatch):
    _, first, release = releases
    previous, new = release("nine", first), release("ten")
    build(previous)
    monkeypatch.undo()  # the real lookup, against a port nothing listens on

    with pytest.raises(release_dist.DistError, match="refusing to touch any release"):
        release_dist.place(new, previous, health_url=HEALTH)


@pytest.mark.parametrize("body", [b"[]", b'"ok"', b"{}"])
def test_a_health_without_a_build_refuses_everything(monkeypatch, body):
    monkeypatch.undo()

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Opener:
        def open(self, url, timeout):
            return Answer(body)

    monkeypatch.setattr(release_dist, "build_opener", lambda *handlers: Opener())

    with pytest.raises(release_dist.DistError, match="names no build"):
        release_dist.serving_build(HEALTH)
