"""Tests for review widgets installed from external module packages."""

from __future__ import annotations

from pathlib import Path

import pytest

from niuu.review_widgets import ReviewWidgetError, load_review_widgets

MANIFEST = """
schema_version: 1
id: acme-print
requires:
  niuu_review_widget: 1
review_widgets:
  - name: print-quality
    title: Print quality
    entry: widgets/quality.html
"""


def _package(root: Path, manifest: str = MANIFEST, html: str = "<p>Stills</p>") -> str:
    (root / "widgets").mkdir(parents=True, exist_ok=True)
    (root / "widgets" / "quality.html").write_text(html)
    path = root / "niuu-module.yaml"
    path.write_text(manifest)
    return str(path)


def test_a_module_installs_its_widgets(tmp_path: Path) -> None:
    widgets = load_review_widgets([_package(tmp_path)], max_bytes=1024)

    widget = widgets["acme-print/print-quality"]
    assert widget.title == "Print quality"
    assert widget.html == "<p>Stills</p>"


def test_no_manifests_install_nothing() -> None:
    assert load_review_widgets([], max_bytes=1024) == {}


def test_a_missing_entry_stops_startup(tmp_path: Path) -> None:
    path = _package(tmp_path)
    (tmp_path / "widgets" / "quality.html").unlink()

    with pytest.raises(ReviewWidgetError, match="Could not read review widget"):
        load_review_widgets([path], max_bytes=1024)


def test_an_oversized_widget_says_what_to_do(tmp_path: Path) -> None:
    path = _package(tmp_path, html="x" * 100)

    with pytest.raises(ReviewWidgetError, match="review_widgets.max_page_bytes"):
        load_review_widgets([path], max_bytes=10)


def test_a_widget_cannot_reach_outside_its_package(tmp_path: Path) -> None:
    path = _package(tmp_path, MANIFEST.replace("widgets/quality.html", "../secret.html"))

    with pytest.raises(ReviewWidgetError, match="Invalid module manifest"):
        load_review_widgets([path], max_bytes=1024)


def test_a_symlink_cannot_reach_outside_its_package(tmp_path: Path) -> None:
    secret = tmp_path / "secret.html"
    secret.write_text("secret")
    package = tmp_path / "package"
    path = _package(package)
    (package / "widgets" / "quality.html").unlink()
    (package / "widgets" / "quality.html").symlink_to(secret)

    with pytest.raises(ReviewWidgetError, match="outside its package"):
        load_review_widgets([path], max_bytes=1024)


def test_two_packages_cannot_claim_one_widget(tmp_path: Path) -> None:
    first = _package(tmp_path / "a")
    second = _package(tmp_path / "b")

    with pytest.raises(ReviewWidgetError, match="Duplicate review widget"):
        load_review_widgets([first, second], max_bytes=1024)


def test_an_unreadable_manifest_stops_startup(tmp_path: Path) -> None:
    with pytest.raises(ReviewWidgetError, match="Invalid module manifest"):
        load_review_widgets([str(tmp_path / "missing.yaml")], max_bytes=1024)
