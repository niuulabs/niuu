"""Running-code identity remains independent of later source and environment edits."""

from unittest.mock import patch

from niuu import build_identity as module


def test_identity_uses_explicit_release_and_hashes_source(tmp_path, monkeypatch):
    location = tmp_path / "src" / "niuu" / "build_identity.py"
    location.parent.mkdir(parents=True)
    location.write_text("original implementation")
    monkeypatch.setattr(module, "__file__", str(location))
    monkeypatch.setenv("NIUU_BUILD_REVISION", "release-commit")
    monkeypatch.setenv("NIUU_BUILD_VERSION", "forge-validation-build")
    with patch.object(module.subprocess, "check_output", return_value=b""):
        original = module.build_identity()
        location.write_text("new implementation")
        changed = module.build_identity()
    assert original["revision"] == "release-commit"
    assert original["build"] == "forge-validation-build"
    assert original["dirty"] is False
    assert original["source_sha256"] != changed["source_sha256"]


def test_source_distribution_without_git_reports_unknown_not_failure(tmp_path, monkeypatch):
    location = tmp_path / "src" / "niuu" / "build_identity.py"
    location.parent.mkdir(parents=True)
    location.write_text("installed source")
    monkeypatch.setattr(module, "__file__", str(location))
    monkeypatch.delenv("NIUU_BUILD_REVISION", raising=False)
    with patch.object(module.subprocess, "check_output", side_effect=OSError):
        identity = module.build_identity()
    assert identity["revision"] == "unknown"
    assert len(identity["source_sha256"]) == 64
