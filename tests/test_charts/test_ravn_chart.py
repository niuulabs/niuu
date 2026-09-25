"""Tests for the ravn Helm chart: config.yaml rendering and store wiring.

Both trigger_store and budget_ledger are opt-in (see
ravn.config.TriggerStoreConfig/BudgetLedgerConfig — empty adapter by
default), gated in the chart by triggerStore.enabled/budgetLedger.enabled
(also default false) — the feature-specific opt-in. database.enabled/
persistence.enabled are general-purpose flags that predate this feature
(persistence already backs the cron job store and a sqlite memory backend
under the same mount) and must NOT by themselves turn trigger/budget storage
on for a release that already set them for something else — e.g. valhalla's
and noatun's values-niuu.yaml already set ravn.persistence.enabled: true.

With no new values set (this chart's defaults, e.g. ymir's values-niuu.yaml:
persistence.enabled: false, no database block, and every live cluster's
absent triggerStore/budgetLedger), neither store is configured and this
chart must keep rendering exactly as it does today: no config.yaml, no
config volume, /api/v1/ravn/triggers and /api/v1/ravn/budget/* return 503 at
runtime. triggerStore.enabled/budgetLedger.enabled=true then picks the
Postgres adapter if database.enabled, else the file adapter if
persistence.enabled, else the render fails (behind an HPA — see hpa.yaml —
the file adapter cannot be shared correctly across replicas either).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
CHART_DIR = REPO_ROOT / "charts" / "ravn"


def _render(*extra_args: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "test", str(CHART_DIR), *extra_args],
        check=True,
        capture_output=True,
        text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _render_fails(*extra_args: str) -> str:
    result = subprocess.run(
        ["helm", "template", "test", str(CHART_DIR), *extra_args],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    return result.stderr


def _configmap(docs: list[dict]) -> dict | None:
    return next(
        (
            doc
            for doc in docs
            if doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "test-ravn-config"
        ),
        None,
    )


def _deployment(docs: list[dict]) -> dict:
    return next(doc for doc in docs if doc.get("kind") == "Deployment")


def _container_env(deployment: dict) -> dict[str, dict]:
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return {item["name"]: item for item in container["env"]}


class TestNoStoreConfiguredByDefault:
    """This chart's defaults — no new values set, e.g. ymir's
    values-niuu.yaml (ravn.persistence.enabled: false, no database block) —
    must render exactly as they do today: no store, no config, no PVC."""

    def test_renders_successfully(self) -> None:
        docs = _render()
        assert _deployment(docs) is not None

    def test_no_configmap_rendered(self) -> None:
        docs = _render()
        assert _configmap(docs) is None

    def test_no_config_volume_mounted(self) -> None:
        deployment = _deployment(_render())
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        mount_names = {m["name"] for m in container.get("volumeMounts") or []}
        assert "ravn-config" not in mount_names
        assert "ravn-data" not in mount_names

    def test_no_dsn_env_var(self) -> None:
        env = _container_env(_deployment(_render()))
        assert "RAVN_STORE_DSN" not in env

    def test_no_pvc_rendered(self) -> None:
        docs = _render()
        assert not any(doc.get("kind") == "PersistentVolumeClaim" for doc in docs)

    def test_renders_successfully_with_multiple_replicas(self) -> None:
        """No store at all means nothing to lose — replicaCount/autoscaling
        impose no constraint when neither persistence nor database is set."""
        docs = _render("--set", "replicaCount=5")
        assert _deployment(docs) is not None

    def test_renders_successfully_with_autoscaling(self) -> None:
        docs = _render("--set", "autoscaling.enabled=true")
        assert _deployment(docs) is not None


class TestPersistenceAloneWithoutTriggerStoreOrBudgetLedgerOptIn:
    """The MUST-FIX this closes: persistence.enabled=true predates this
    feature (it already backs the cron job store and a sqlite memory
    backend) — valhalla's and noatun's values-niuu.yaml already set it. It
    must not, by itself, silently light up trigger/budget storage for a
    release that set it for something else."""

    def test_no_configmap_rendered(self) -> None:
        docs = _render("--set", "persistence.enabled=true")
        assert _configmap(docs) is None

    def test_no_dsn_env_var(self) -> None:
        env = _container_env(_deployment(_render("--set", "persistence.enabled=true")))
        assert "RAVN_STORE_DSN" not in env

    def test_renders_successfully_with_multiple_replicas(self) -> None:
        """No store resolved means no file-store-vs-replicas conflict —
        persistence.enabled alone imposes no replica constraint."""
        docs = _render("--set", "persistence.enabled=true", "--set", "replicaCount=5")
        assert _deployment(docs) is not None


class TestDatabaseAloneWithoutTriggerStoreOrBudgetLedgerOptIn:
    """database.enabled=true is new to this feature, but still must not
    require database.existingSecret or wire a store unless a store is
    actually opted into — a future, unrelated use of database.enabled must
    not pay for infrastructure this feature alone would otherwise demand."""

    def test_renders_without_an_existing_secret(self) -> None:
        docs = _render("--set", "database.enabled=true")
        assert _deployment(docs) is not None

    def test_no_configmap_rendered(self) -> None:
        docs = _render("--set", "database.enabled=true")
        assert _configmap(docs) is None

    def test_no_dsn_env_var(self) -> None:
        env = _container_env(_deployment(_render("--set", "database.enabled=true")))
        assert "RAVN_STORE_DSN" not in env


class TestTriggerStoreAndBudgetLedgerOptIn:
    """triggerStore.enabled/budgetLedger.enabled=true is the explicit,
    feature-specific opt-in — it then picks Postgres (database.enabled) or
    file (persistence.enabled) as the backing store."""

    def test_file_store_via_persistence(self) -> None:
        docs = _render(
            "--set",
            "triggerStore.enabled=true",
            "--set",
            "budgetLedger.enabled=true",
            "--set",
            "persistence.enabled=true",
        )
        configmap = _configmap(docs)
        assert configmap is not None
        settings = yaml.safe_load(configmap["data"]["config.yaml"])
        assert (
            settings["trigger_store"]["adapter"] == "ravn.adapters.trigger_store.FileTriggerStore"
        )
        assert settings["budget_ledger"]["adapter"] == (
            "ravn.adapters.budget_ledger.FileBudgetLedger"
        )

    def test_file_paths_are_under_the_persistence_mount(self) -> None:
        docs = _render(
            "--set",
            "triggerStore.enabled=true",
            "--set",
            "budgetLedger.enabled=true",
            "--set",
            "persistence.enabled=true",
        )
        settings = yaml.safe_load(_configmap(docs)["data"]["config.yaml"])
        assert settings["trigger_store"]["kwargs"]["path"] == (
            "/home/niuu/.ravn/ravn_triggers.json"
        )
        assert settings["budget_ledger"]["kwargs"]["path"] == (
            "/home/niuu/.ravn/ravn_budget_ledger.json"
        )

    def test_custom_mount_path_is_honoured(self) -> None:
        docs = _render(
            "--set",
            "triggerStore.enabled=true",
            "--set",
            "persistence.enabled=true",
            "--set",
            "persistence.mountPath=/data",
        )
        settings = yaml.safe_load(_configmap(docs)["data"]["config.yaml"])
        assert settings["trigger_store"]["kwargs"]["path"] == "/data/ravn_triggers.json"

    def test_config_volume_is_mounted(self) -> None:
        deployment = _deployment(
            _render(
                "--set",
                "triggerStore.enabled=true",
                "--set",
                "persistence.enabled=true",
            )
        )
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        mount_names = {m["name"] for m in container.get("volumeMounts") or []}
        assert "ravn-config" in mount_names
        assert "ravn-data" in mount_names

    def test_operator_settings_are_preserved_alongside_the_synthesized_stores(self) -> None:
        docs = _render(
            "--set",
            "triggerStore.enabled=true",
            "--set",
            "persistence.enabled=true",
            "--set",
            "config.settings.resident_triggers.enabled=true",
        )
        settings = yaml.safe_load(_configmap(docs)["data"]["config.yaml"])
        assert settings["resident_triggers"]["enabled"] is True
        assert "trigger_store" in settings

    def test_only_trigger_store_opted_in_leaves_budget_ledger_unset(self) -> None:
        docs = _render(
            "--set",
            "triggerStore.enabled=true",
            "--set",
            "persistence.enabled=true",
        )
        settings = yaml.safe_load(_configmap(docs)["data"]["config.yaml"])
        assert "trigger_store" in settings
        assert "budget_ledger" not in settings

    def test_postgres_store_via_database(self) -> None:
        docs = _render(
            "--set",
            "triggerStore.enabled=true",
            "--set",
            "budgetLedger.enabled=true",
            "--set",
            "database.enabled=true",
            "--set",
            "database.existingSecret=ravn-db",
        )
        configmap = _configmap(docs)
        assert configmap is not None
        settings = yaml.safe_load(configmap["data"]["config.yaml"])
        assert settings["trigger_store"]["adapter"] == (
            "ravn.adapters.trigger_store.LazyPostgresTriggerStore"
        )
        assert settings["budget_ledger"]["adapter"] == (
            "ravn.adapters.budget_ledger.LazyPostgresBudgetLedger"
        )

    def test_database_takes_precedence_over_persistence_when_both_set(self) -> None:
        docs = _render(
            "--set",
            "triggerStore.enabled=true",
            "--set",
            "database.enabled=true",
            "--set",
            "database.existingSecret=ravn-db",
            "--set",
            "persistence.enabled=true",
        )
        settings = yaml.safe_load(_configmap(docs)["data"]["config.yaml"])
        assert settings["trigger_store"]["adapter"] == (
            "ravn.adapters.trigger_store.LazyPostgresTriggerStore"
        )

    def test_dsn_env_var_sourced_from_the_existing_secret(self) -> None:
        deployment = _deployment(
            _render(
                "--set",
                "triggerStore.enabled=true",
                "--set",
                "database.enabled=true",
                "--set",
                "database.existingSecret=ravn-db",
            )
        )
        env = _container_env(deployment)
        secret_ref = env["RAVN_STORE_DSN"]["valueFrom"]["secretKeyRef"]
        assert secret_ref == {"name": "ravn-db", "key": "dsn"}

    def test_trigger_store_and_budget_ledger_share_one_dsn_env_var(self) -> None:
        docs = _render(
            "--set",
            "triggerStore.enabled=true",
            "--set",
            "budgetLedger.enabled=true",
            "--set",
            "database.enabled=true",
            "--set",
            "database.existingSecret=ravn-db",
        )
        settings = yaml.safe_load(_configmap(docs)["data"]["config.yaml"])
        trigger_dsn_var = settings["trigger_store"]["secret_kwargs_env"]["dsn"]
        budget_dsn_var = settings["budget_ledger"]["secret_kwargs_env"]["dsn"]
        assert trigger_dsn_var == budget_dsn_var == "RAVN_STORE_DSN"

    def test_custom_secret_key_is_honoured(self) -> None:
        deployment = _deployment(
            _render(
                "--set",
                "triggerStore.enabled=true",
                "--set",
                "database.enabled=true",
                "--set",
                "database.existingSecret=ravn-db",
                "--set",
                "database.secretKey=connection-string",
            )
        )
        env = _container_env(deployment)
        assert env["RAVN_STORE_DSN"]["valueFrom"]["secretKeyRef"]["key"] == "connection-string"

    def test_config_volume_and_env_are_rendered_for_postgres(self) -> None:
        deployment = _deployment(
            _render(
                "--set",
                "triggerStore.enabled=true",
                "--set",
                "database.enabled=true",
                "--set",
                "database.existingSecret=ravn-db",
            )
        )
        env = _container_env(deployment)
        assert env["RAVN_CONFIG"]["value"] == "/etc/ravn/config.yaml"
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        mount_names = {m["name"] for m in container.get("volumeMounts") or []}
        assert "ravn-config" in mount_names


class TestPersistenceValidation:
    """The render guard fires when a store is actually selected —
    triggerStore.enabled/budgetLedger.enabled without a backing store, a
    file-backed store needs persistence.enabled=true, the Postgres adapter
    needs database.enabled=true, and the file-backed store additionally
    cannot safely run more than one replica."""

    def test_default_values_render_successfully(self) -> None:
        """ymir-like: nothing set — no store, no failure."""
        docs = _render()
        assert _deployment(docs) is not None

    def test_trigger_store_enabled_without_persistence_or_database_fails(self) -> None:
        stderr = _render_fails("--set", "triggerStore.enabled=true")
        assert "triggerStore.enabled=true requires" in stderr

    def test_budget_ledger_enabled_without_persistence_or_database_fails(self) -> None:
        stderr = _render_fails("--set", "budgetLedger.enabled=true")
        assert "budgetLedger.enabled=true requires" in stderr

    def test_trigger_store_with_persistence_at_one_replica_renders(self) -> None:
        docs = _render("--set", "triggerStore.enabled=true", "--set", "persistence.enabled=true")
        assert _deployment(docs) is not None

    def test_trigger_store_with_database_renders(self) -> None:
        docs = _render(
            "--set",
            "triggerStore.enabled=true",
            "--set",
            "database.enabled=true",
            "--set",
            "database.existingSecret=ravn-db",
        )
        assert _deployment(docs) is not None

    def test_trigger_store_with_persistence_and_replica_count_above_one_fails(self) -> None:
        stderr = _render_fails(
            "--set",
            "triggerStore.enabled=true",
            "--set",
            "persistence.enabled=true",
            "--set",
            "replicaCount=2",
        )
        assert "file-backed trigger_store/budget_ledger" in stderr

    def test_trigger_store_with_persistence_and_autoscaling_fails(self) -> None:
        stderr = _render_fails(
            "--set",
            "triggerStore.enabled=true",
            "--set",
            "persistence.enabled=true",
            "--set",
            "autoscaling.enabled=true",
        )
        assert "file-backed trigger_store/budget_ledger" in stderr

    def test_trigger_store_with_database_and_autoscaling_renders(self) -> None:
        docs = _render(
            "--set",
            "triggerStore.enabled=true",
            "--set",
            "database.enabled=true",
            "--set",
            "database.existingSecret=ravn-db",
            "--set",
            "autoscaling.enabled=true",
        )
        assert _deployment(docs) is not None

    def test_trigger_store_with_database_and_replica_count_above_one_renders(self) -> None:
        docs = _render(
            "--set",
            "triggerStore.enabled=true",
            "--set",
            "database.enabled=true",
            "--set",
            "database.existingSecret=ravn-db",
            "--set",
            "replicaCount=3",
        )
        assert _deployment(docs) is not None

    def test_a_store_enabled_without_persistence_or_database_fails(self) -> None:
        """An operator manually naming a file-backed adapter in
        config.settings, without also setting persistence.enabled=true,
        must still be caught — the guard checks the adapter that will
        actually be rendered, not just triggerStore.enabled/database/
        persistence."""
        stderr = _render_fails(
            "--set",
            "config.settings.trigger_store.adapter=ravn.adapters.trigger_store.FileTriggerStore",
        )
        assert "without persistence.enabled=true" in stderr

    def test_a_postgres_adapter_without_database_enabled_fails(self) -> None:
        stderr = _render_fails(
            "--set",
            "config.settings.budget_ledger.adapter="
            "ravn.adapters.budget_ledger.LazyPostgresBudgetLedger",
        )
        assert "without database.enabled=true" in stderr

    def test_missing_existing_secret_fails_the_render(self) -> None:
        stderr = _render_fails(
            "--set", "triggerStore.enabled=true", "--set", "database.enabled=true"
        )
        assert "database.existingSecret is required" in stderr


if __name__ == "__main__":
    pytest.main([__file__])
