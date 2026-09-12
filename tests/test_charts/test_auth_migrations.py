"""Auth schema changes ship consistently in local, CLI, and owning Helm charts."""

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    "chart,names,variant",
    [
        (
            "volundr",
            [
                "000062_pat_authority",
                "000063_topology_source_authority",
                "000064_instance_tenant_attribution",
            ],
            "volundr",
        ),
        (
            "guild",
            [
                "000062_pat_authority",
                "000063_topology_source_authority",
                "000064_instance_tenant_attribution",
            ],
            "volundr",
        ),
        ("niuu-shared", ["000062_pat_authority"], "volundr"),
        ("ting", ["000033_pat_authority", "000034_resource_tenants"], "ting"),
    ],
)
def test_auth_migration_copies_match(chart, names, variant):
    docs = list(
        yaml.safe_load_all(
            subprocess.check_output(
                [
                    "helm",
                    "template",
                    "test",
                    str(ROOT / "charts" / chart),
                    "--set",
                    "migrations.enabled=true",
                ]
            )
        )
    )
    data = next(
        d["data"]
        for d in docs
        if d and d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-migrations")
    )
    for name in names:
        for direction in ["up", "down"]:
            file = f"{name}.{direction}.sql"
            source = ROOT / "migrations" / ("ting" if variant == "ting" else "") / file
            sql = source.read_text()
            assert (ROOT / "src/cli/migrations" / variant / file).read_text() == sql
            assert data[file].strip() == sql.strip()
    if chart == "guild":
        assert "000061_observatory_fragments.up.sql" in data
