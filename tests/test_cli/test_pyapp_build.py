"""Native resources must remain usable after the runtime moves to its user cache."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/build_pyapp.py"
spec = importlib.util.spec_from_file_location("build_pyapp", SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_postgres_libraries_are_relative_and_aliases_are_not_patched_twice(tmp_path, monkeypatch):
    postgres = tmp_path / "pginstall"
    for name in ("bin/postgres", "lib/libpq.so.5", "lib/postgresql/vector.so"):
        path = postgres / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x7fELFtest fixture")
    (postgres / "lib/libpq.so").symlink_to("libpq.so.5")
    (postgres / "bin/pg_config").write_text("#!/bin/sh\n")
    calls = []
    monkeypatch.setattr(builder.platform, "system", lambda: "Linux")
    monkeypatch.setattr(builder, "run", lambda *args: calls.append(args))
    builder.relocate_postgres(postgres)
    assert {str(call[-1].relative_to(postgres)): call[-2] for call in calls} == {
        "bin/postgres": "$ORIGIN/../lib",
        "lib/libpq.so.5": "$ORIGIN",
        "lib/postgresql/vector.so": "$ORIGIN/..",
    }


def test_rejects_changed_launcher_source_before_extracting(tmp_path):
    (tmp_path / "source.tar.gz").write_bytes(b"not the pinned source archive")
    with pytest.raises(ValueError, match="checksum mismatch"):
        builder.pyapp_source(tmp_path)
