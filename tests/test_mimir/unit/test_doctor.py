"""Unit tests for mimir.doctor — health checks D01–D08 and safe auto-fixes.

Each check is exercised through its pass/warn/fail paths using a real
MarkdownMimirAdapter backed by tmp_path.  Remote registry health probes are
mocked with respx — no network and no Docker.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.doctor import DoctorCheck, DoctorReport, run_doctor, run_fixes
from mimir.registry import MimirRegistryEntry, MimirRegistryStore
from mimir.router import MimirRouter
from niuu.adapters.search.sqlite import SqliteSearchAdapter
from niuu.domain.mimir import MimirSource, compute_content_hash

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEALTHY_PAGE = "---\ntype: topic\n---\n# Valhalla\nA hall where chosen warriors gather.\n"


def _root(tmp_path: Path) -> Path:
    return tmp_path / "mimir"


def _adapter(tmp_path: Path, *, with_search: bool = False) -> MarkdownMimirAdapter:
    search_port = None
    if with_search:
        search_port = SqliteSearchAdapter(path=str(_root(tmp_path) / "search.db"))
    return MarkdownMimirAdapter(root=_root(tmp_path), search_port=search_port)


def _write_page(adapter: MarkdownMimirAdapter, path: str, content: str) -> None:
    """Write a page directly into the wiki, bypassing the index update."""
    full = adapter._wiki / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def _check(report: DoctorReport, check_id: str) -> DoctorCheck:
    matches = [check for check in report.checks if check.id == check_id]
    assert len(matches) == 1, f"expected exactly one {check_id} check"
    return matches[0]


async def _healthy_doctor_setup(tmp_path: Path) -> MarkdownMimirAdapter:
    """Adapter with one indexed page, frontmatter type, and a built search index."""
    adapter = _adapter(tmp_path, with_search=True)
    await adapter.upsert_page("technical/valhalla.md", _HEALTHY_PAGE)
    await adapter.rebuild_search_index()
    return adapter


def _source(source_id: str, content: str = "Odin gave an eye for wisdom.") -> MimirSource:
    return MimirSource(
        source_id=source_id,
        title="Norse notes",
        content=content,
        source_type="document",
        origin_url=None,
        content_hash=compute_content_hash(content),
        ingested_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# D01 — wiki root
# ---------------------------------------------------------------------------


async def test_d01_pass_on_healthy_root(tmp_path: Path) -> None:
    adapter = await _healthy_doctor_setup(tmp_path)
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D01")
    assert check.status == "pass"
    assert "MIMIR.md present" in check.detail


async def test_d01_fail_when_root_missing(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    report = await run_doctor(adapter, tmp_path / "does-not-exist")
    check = _check(report, "D01")
    assert check.status == "fail"
    assert "does not exist" in check.detail
    assert report.exit_code == 2


async def test_d01_fail_when_root_not_writable(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    root = _root(tmp_path)
    root.chmod(0o555)
    try:
        report = await run_doctor(adapter, root)
    finally:
        root.chmod(0o755)
    check = _check(report, "D01")
    assert check.status == "fail"
    assert "not writable" in check.detail


async def test_d01_warn_when_mimir_md_missing(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    (_root(tmp_path) / "MIMIR.md").unlink()
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D01")
    assert check.status == "warn"
    assert "MIMIR.md" in check.detail


# ---------------------------------------------------------------------------
# D02 — index sync
# ---------------------------------------------------------------------------


async def test_d02_pass_when_index_in_sync(tmp_path: Path) -> None:
    adapter = await _healthy_doctor_setup(tmp_path)
    report = await run_doctor(adapter, _root(tmp_path))
    assert _check(report, "D02").status == "pass"


async def test_d02_warn_when_index_out_of_sync(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(adapter, "technical/orphan.md", _HEALTHY_PAGE)
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D02")
    assert check.status == "warn"
    assert check.fixable
    assert "out of sync" in check.detail


async def test_d02_and_d06_fail_when_lint_raises(tmp_path: Path, monkeypatch) -> None:
    adapter = _adapter(tmp_path)

    async def _boom(fix: bool = False):
        raise RuntimeError("lint exploded")

    monkeypatch.setattr(adapter, "lint", _boom)
    report = await run_doctor(adapter, _root(tmp_path))
    assert _check(report, "D02").status == "fail"
    assert _check(report, "D06").status == "fail"
    assert "lint exploded" in _check(report, "D06").detail


# ---------------------------------------------------------------------------
# D03 — search index
# ---------------------------------------------------------------------------


async def test_d03_warn_when_search_db_missing(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D03")
    assert check.status == "warn"
    assert check.fixable
    assert "not found" in check.detail


async def test_d03_warn_when_pages_exist_but_nothing_indexed(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, with_search=True)
    await adapter.upsert_page("technical/valhalla.md", _HEALTHY_PAGE)
    # Wipe the index so the chunk count drops to zero.
    conn = sqlite3.connect(str(_root(tmp_path) / "search.db"))
    conn.execute("DELETE FROM search_index")
    conn.commit()
    conn.close()
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D03")
    assert check.status == "warn"
    assert check.fixable
    assert "0 chunks" in check.detail


async def test_d03_pass_when_index_consistent(tmp_path: Path) -> None:
    adapter = await _healthy_doctor_setup(tmp_path)
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D03")
    assert check.status == "pass"
    assert "1 wiki page(s)" in check.detail


async def test_d03_warn_when_table_unreadable(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    db_path = _root(tmp_path) / "search.db"
    conn = sqlite3.connect(str(db_path))  # valid sqlite file, no search_index table
    conn.execute("CREATE TABLE other (id TEXT)")
    conn.commit()
    conn.close()
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D03")
    assert check.status == "warn"
    assert "unreadable" in check.detail


async def test_d03_explicit_search_db_path(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    report = await run_doctor(adapter, _root(tmp_path), search_db=tmp_path / "elsewhere.db")
    assert _check(report, "D03").status == "warn"


# ---------------------------------------------------------------------------
# D04 — embedding stack
# ---------------------------------------------------------------------------


async def test_d04_pass_when_not_configured(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D04")
    assert check.status == "pass"
    assert "FTS-only" in check.detail


async def test_d04_warn_when_modules_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("mimir.doctor._module_available", lambda name: False)
    adapter = _adapter(tmp_path)
    report = await run_doctor(adapter, _root(tmp_path), embedding_model="all-MiniLM-L6-v2")
    check = _check(report, "D04")
    assert check.status == "warn"
    assert "sentence-transformers" in check.detail
    assert "sqlite-vec" in check.detail


async def test_d04_pass_when_configured_and_available(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("mimir.doctor._module_available", lambda name: True)
    adapter = _adapter(tmp_path)
    report = await run_doctor(adapter, _root(tmp_path), embedding_model="all-MiniLM-L6-v2")
    check = _check(report, "D04")
    assert check.status == "pass"
    assert "all-MiniLM-L6-v2" in check.detail


# ---------------------------------------------------------------------------
# D05 — registry mounts
# ---------------------------------------------------------------------------


async def test_d05_pass_without_registry(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D05")
    assert check.status == "pass"
    assert "no enabled registry mounts" in check.detail


async def test_d05_local_mounts(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    store = MimirRegistryStore(tmp_path / "registry.json")
    store.save_entry(MimirRegistryEntry(name="good", kind="local", path=str(_root(tmp_path))))
    report = await run_doctor(adapter, _root(tmp_path), registry_store=store)
    assert _check(report, "D05").status == "pass"

    store.save_entry(MimirRegistryEntry(name="bad", kind="local", path=str(tmp_path / "missing")))
    report = await run_doctor(adapter, _root(tmp_path), registry_store=store)
    check = _check(report, "D05")
    assert check.status == "warn"
    assert "'bad'" in check.detail


@respx.mock
async def test_d05_remote_mount_healthy(tmp_path: Path) -> None:
    respx.get("http://mimir.test/health").mock(return_value=httpx.Response(200))
    adapter = _adapter(tmp_path)
    store = MimirRegistryStore(tmp_path / "registry.json")
    store.save_entry(MimirRegistryEntry(name="remote", kind="remote", url="http://mimir.test"))
    report = await run_doctor(adapter, _root(tmp_path), registry_store=store)
    check = _check(report, "D05")
    assert check.status == "pass"
    assert "1 enabled mount(s) healthy" in check.detail


@respx.mock
async def test_d05_remote_mount_unreachable_or_unhealthy(tmp_path: Path) -> None:
    respx.get("http://down.test/health").mock(side_effect=httpx.ConnectError("refused"))
    respx.get("http://sick.test/health").mock(return_value=httpx.Response(503))
    adapter = _adapter(tmp_path)
    store = MimirRegistryStore(tmp_path / "registry.json")
    store.save_entry(MimirRegistryEntry(name="down", kind="remote", url="http://down.test"))
    store.save_entry(MimirRegistryEntry(name="sick", kind="remote", url="http://sick.test"))
    store.save_entry(MimirRegistryEntry(name="nourl", kind="remote", url=""))
    store.save_entry(
        MimirRegistryEntry(name="off", kind="remote", url="http://off.test", enabled=False)
    )
    report = await run_doctor(adapter, _root(tmp_path), registry_store=store)
    check = _check(report, "D05")
    assert check.status == "warn"
    assert "unreachable" in check.detail
    assert "HTTP 503" in check.detail
    assert "no URL configured" in check.detail
    assert "off" not in check.detail  # disabled mounts are skipped


# ---------------------------------------------------------------------------
# D06 — lint summary
# ---------------------------------------------------------------------------


async def test_d06_pass_when_clean(tmp_path: Path) -> None:
    adapter = await _healthy_doctor_setup(tmp_path)
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D06")
    assert check.status == "pass"
    assert "errors=0 warnings=0" in check.detail


async def test_d06_warn_on_lint_warnings(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(adapter, "technical/orphan.md", _HEALTHY_PAGE)
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D06")
    assert check.status == "warn"
    assert report.exit_code == 1


async def test_d06_fail_on_lint_errors(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    await adapter.upsert_page(
        "technical/timeline.md",
        "---\ntype: topic\n---\n# Timeline Page\nA page with a sourceless timeline.\n"
        "## Timeline\n- 2026-01-01: Something happened.\n",
    )
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D06")
    assert check.status == "fail"
    assert report.exit_code == 2


# ---------------------------------------------------------------------------
# D07 — orphaned raw sources
# ---------------------------------------------------------------------------


async def test_d07_pass_without_sources(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D07")
    assert check.status == "pass"
    assert "no raw sources" in check.detail


async def test_d07_warn_on_orphaned_source(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    await adapter.ingest(_source("src_orphan1"))
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D07")
    assert check.status == "warn"
    assert "src_orphan1" in check.detail


async def test_d07_pass_when_source_referenced(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    await adapter.ingest(_source("src_used1"))
    await adapter.upsert_page(
        "technical/odin.md",
        "---\ntype: topic\n---\n# Odin\nWisdom notes.\n<!-- sources: src_used1 -->\n",
    )
    report = await run_doctor(adapter, _root(tmp_path))
    assert _check(report, "D07").status == "pass"


async def test_d07_skips_unreadable_raw_json(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    (adapter._raw / "garbage.json").write_text("{not json", encoding="utf-8")
    report = await run_doctor(adapter, _root(tmp_path))
    assert _check(report, "D07").status == "pass"


# ---------------------------------------------------------------------------
# D08 — smoke search
# ---------------------------------------------------------------------------


async def test_d08_pass_on_working_search(tmp_path: Path) -> None:
    adapter = await _healthy_doctor_setup(tmp_path)
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D08")
    assert check.status == "pass"
    assert "without error" in check.detail


async def test_d08_fail_when_search_raises(tmp_path: Path, monkeypatch) -> None:
    adapter = _adapter(tmp_path)

    async def _boom(query: str):
        raise sqlite3.OperationalError("malformed database")

    monkeypatch.setattr(adapter, "search", _boom)
    report = await run_doctor(adapter, _root(tmp_path))
    check = _check(report, "D08")
    assert check.status == "fail"
    assert "OperationalError" in check.detail
    assert report.exit_code == 2


# ---------------------------------------------------------------------------
# DoctorReport mechanics
# ---------------------------------------------------------------------------


def test_report_exit_codes_and_score() -> None:
    ok = DoctorCheck(id="D01", title="a", status="pass")
    warn = DoctorCheck(id="D02", title="b", status="warn")
    fail = DoctorCheck(id="D03", title="c", status="fail")

    assert DoctorReport(checks=[ok]).exit_code == 0
    assert DoctorReport(checks=[ok, warn]).exit_code == 1
    assert DoctorReport(checks=[ok, warn, fail]).exit_code == 2
    assert DoctorReport(checks=[]).worst_status == "pass"

    report = DoctorReport(checks=[ok, warn, fail])
    assert report.passed == 1
    assert report.total == 3
    assert report.score == "1/3"


def test_report_to_dict_and_format_text() -> None:
    report = DoctorReport(
        checks=[
            DoctorCheck(id="D01", title="wiki root", status="pass", detail="fine"),
            DoctorCheck(
                id="D02",
                title="index sync",
                status="warn",
                detail="drift",
                remediation="run --fix",
                fixable=True,
            ),
            DoctorCheck(id="D08", title="smoke search", status="fail", detail="broken"),
        ]
    )
    payload = report.to_dict()
    assert payload["status"] == "fail"
    assert payload["score"] == "1/3"
    assert payload["exit_code"] == 2
    assert len(payload["checks"]) == 3
    assert payload["checks"][1]["fixable"] is True

    text = report.format_text()
    assert "✓ D01" in text
    assert "! D02" in text
    assert "✗ D08" in text
    assert "[fixable]" in text
    assert "→ run --fix" in text


# ---------------------------------------------------------------------------
# run_fixes — repair scenario
# ---------------------------------------------------------------------------


async def test_run_fixes_strictly_improves_score(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, with_search=True)
    # Healthy, indexed page.
    await adapter.upsert_page("technical/valhalla.md", _HEALTHY_PAGE)
    # Deliberately broken page: not in index.md (L01/L11), broken wikilink with
    # a close match (L05), and no frontmatter type (L12).
    _write_page(
        adapter,
        "technical/broken.md",
        "# Broken Page\nReferences [[valhala]] with a typo and has no frontmatter.\n",
    )

    before = await run_doctor(adapter, _root(tmp_path))
    assert before.exit_code == 1
    assert _check(before, "D02").status == "warn"
    assert _check(before, "D06").status == "warn"

    fixes = await run_fixes(adapter)
    assert any("lint auto-fix" in fix for fix in fixes)
    assert any("search index rebuilt" in fix for fix in fixes)

    after = await run_doctor(adapter, _root(tmp_path))
    assert after.passed > before.passed
    assert after.exit_code == 0
    # The broken wikilink was rewritten and the frontmatter type added.
    fixed_content = (adapter._wiki / "technical/broken.md").read_text(encoding="utf-8")
    assert "[[valhalla]]" in fixed_content
    assert "type: topic" in fixed_content


async def test_run_fixes_noop_on_healthy_wiki_without_search_port(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    await adapter.upsert_page("technical/valhalla.md", _HEALTHY_PAGE)
    fixes = await run_fixes(adapter)
    assert fixes == []


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------


def _make_app(tmp_path: Path) -> FastAPI:
    adapter = MarkdownMimirAdapter(root=tmp_path / "mimir")
    router = MimirRouter(adapter=adapter, name="test", role="local")
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    return app


def test_doctor_endpoint_returns_report(tmp_path: Path) -> None:
    client = TestClient(_make_app(tmp_path))
    response = client.get("/mimir/doctor")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"pass", "warn", "fail"}
    assert len(payload["checks"]) == 8
    assert payload["score"].endswith("/8")
    assert {check["id"] for check in payload["checks"]} == {
        "D01",
        "D02",
        "D03",
        "D04",
        "D05",
        "D06",
        "D07",
        "D08",
    }


def test_doctor_endpoint_requires_filesystem_adapter(tmp_path: Path) -> None:
    from ravn.adapters.mimir.composite import CompositeMimirAdapter
    from ravn.domain.mimir import MimirMount

    local = MarkdownMimirAdapter(root=tmp_path / "local")
    adapter = CompositeMimirAdapter(
        mounts=[MimirMount(name="local", port=local, role="local", read_priority=0)],
    )
    router = MimirRouter(adapter=adapter, name="test", role="local")
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    client = TestClient(app)
    response = client.get("/mimir/doctor")
    assert response.status_code == 501


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture()
def cli_runner() -> CliRunner:
    return CliRunner()


def test_cli_doctor_healthy_exits_zero(tmp_path: Path, cli_runner: CliRunner) -> None:
    from mimir.__main__ import app

    root = _root(tmp_path)
    adapter = MarkdownMimirAdapter(
        root=root, search_port=SqliteSearchAdapter(path=str(root / "search.db"))
    )
    import asyncio

    asyncio.run(adapter.upsert_page("technical/valhalla.md", _HEALTHY_PAGE))
    asyncio.run(adapter.rebuild_search_index())

    result = cli_runner.invoke(app, ["doctor", "--path", str(root)])
    assert result.exit_code == 0, result.output
    assert "Mímir doctor — 8/8 checks passed (pass)" in result.output


def test_cli_doctor_broken_then_fix(tmp_path: Path, cli_runner: CliRunner) -> None:
    from mimir.__main__ import app

    root = _root(tmp_path)
    adapter = MarkdownMimirAdapter(root=root)
    _write_page(adapter, "technical/broken.md", "# Broken\nNot in the index.\n")

    broken = cli_runner.invoke(app, ["doctor", "--path", str(root)])
    assert broken.exit_code == 1, broken.output
    assert "! D02" in broken.output

    fixed = cli_runner.invoke(app, ["doctor", "--path", str(root), "--fix"])
    assert fixed.exit_code == 0, fixed.output
    assert "fixed:" in fixed.output
    assert "8/8 checks passed (pass)" in fixed.output


def test_cli_doctor_json_output(tmp_path: Path, cli_runner: CliRunner) -> None:
    from mimir.__main__ import app

    root = _root(tmp_path)
    MarkdownMimirAdapter(root=root)

    result = cli_runner.invoke(app, ["doctor", "--path", str(root), "--json"])
    payload = json.loads(result.output)
    assert payload["total"] == 8
    assert payload["fixes_applied"] == []
    assert result.exit_code == payload["exit_code"]
