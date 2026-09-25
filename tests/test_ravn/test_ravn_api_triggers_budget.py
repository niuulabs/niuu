"""Tests for the Ravn API's /triggers and /budget routes."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient as _TestClient

from ravn.api import create_app
from ravn.config import Settings

#: Every persona_name this file's trigger fixtures create triggers for.
#: create_trigger_endpoint now requires the caller to administer the target
#: persona (checked against resident_directory.list_ravens, which reads
#: PlatformRuntimePort.list_resident_runtimes) — _FakePlatform below stands
#: in for that so these tests do not need a real Völundr or an httpx mock at
#: every one of this file's ~15 POST /triggers call sites.
_TEST_PERSONAS = ["product-resident", "p"]


class _FakePlatform:
    """Minimal PlatformRuntimePort double: only list_resident_runtimes is
    implemented, the one method create_trigger_endpoint's persona-ownership
    check calls (via ResidentDirectory.list_ravens)."""

    def __init__(self, persona_names: list[str]) -> None:
        self._runtimes = [
            {"id": f"runtime-{i}", "persona_name": name} for i, name in enumerate(persona_names)
        ]

    async def list_resident_runtimes(
        self, auth_headers: dict[str, str], auth_params: dict[str, str]
    ) -> list[dict[str, Any]]:
        del auth_headers, auth_params
        return self._runtimes


def _client(
    app, *, user_id="dev-user", tenant="default", roles="volundr:developer", owner_scoped=False
):
    """Simulate identity headers supplied by the trusted Envoy proxy.

    ``owner_scoped=True`` also attaches a bearer token carrying
    ``workload_owner_scoped: true`` — POST /budget/spend requires one (see
    niuu.domain.services.token_scope.workload_owner_scoped), which dev
    header-auth alone never provides. The token's signature is never
    verified here (Envoy's job upstream in production; token_scope only
    reads the already-trusted claims), so any signing key works.
    """
    import jwt

    headers = {
        "x-auth-user-id": user_id,
        "x-auth-tenant": tenant,
        "x-auth-roles": roles,
    }
    if owner_scoped:
        token = jwt.encode(
            {"workload_owner_scoped": True},
            "test-only-signing-key-32-bytes-long!",
            algorithm="HS256",
        )
        headers["authorization"] = f"Bearer {token}"
    return _TestClient(app, headers=headers)


def _app(settings):
    """create_app wired with _FakePlatform so persona-ownership checks pass
    for this file's fixed set of test persona names."""
    return create_app(
        settings=settings,
        platform_runtime=_FakePlatform(_TEST_PERSONAS),
    )


def _settings(tmp_path):
    """Both stores are opt-in (empty adapter by default — see
    TriggerStoreConfig/BudgetLedgerConfig); these tests are specifically
    about trigger/budget behavior WITH a store configured, so opt both in
    explicitly, the same way the chart does for persistence.enabled=true."""
    return Settings(
        trigger_store={
            "adapter": "ravn.adapters.trigger_store.FileTriggerStore",
            "kwargs": {"path": str(tmp_path / "triggers.json")},
        },
        budget_ledger={
            "adapter": "ravn.adapters.budget_ledger.FileBudgetLedger",
            "kwargs": {"path": str(tmp_path / "ledger.json")},
        },
    )


def _budget_ledger_path(settings) -> str:
    return settings.budget_ledger.kwargs["path"]


class TestStoresAreOptIn:
    """Both trigger_store and budget_ledger default to an empty adapter (see
    TriggerStoreConfig/BudgetLedgerConfig) — with neither configured, every
    already-deployed chart (no new values set) must keep returning exactly
    what it returns today: 503 with the remedy, not an unrequested file
    store writing into the container filesystem."""

    def test_default_settings_has_no_store_configured(self) -> None:
        settings = Settings()
        assert settings.trigger_store.adapter == ""
        assert settings.budget_ledger.adapter == ""

    def test_get_triggers_is_503_with_the_remedy(self) -> None:
        client = _client(_app(settings=Settings()))
        resp = client.get("/api/v1/ravn/triggers")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Ravn trigger persistence is unavailable"

    def test_create_trigger_is_503_before_any_validation(self) -> None:
        """503 (nothing to store into) takes priority over a 422 the spec
        would otherwise earn — even an invalid kind must not mask the more
        fundamental "there is no store" problem."""
        client = _client(_app(settings=Settings()))
        resp = client.post(
            "/api/v1/ravn/triggers",
            json={"kind": "not-a-real-kind", "persona_name": "p", "spec": "x", "enabled": True},
        )
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Ravn trigger persistence is unavailable"

    def test_delete_trigger_is_503(self) -> None:
        client = _client(_app(settings=Settings()))
        resp = client.delete("/api/v1/ravn/triggers/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Ravn trigger persistence is unavailable"

    def test_get_budget_fleet_is_503_with_the_remedy(self) -> None:
        client = _client(_app(settings=Settings()))
        resp = client.get("/api/v1/ravn/budget/fleet")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Ravn budget persistence is unavailable"

    def test_get_budget_me_is_503(self) -> None:
        client = _client(_app(settings=Settings()))
        resp = client.get("/api/v1/ravn/budget/me")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Ravn budget persistence is unavailable"

    def test_get_budget_by_id_is_503(self) -> None:
        client = _client(_app(settings=Settings()))
        resp = client.get("/api/v1/ravn/budget/some-ravn-id")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Ravn budget persistence is unavailable"

    def test_post_budget_spend_is_503_before_the_owner_scoped_check(self) -> None:
        client = _client(_app(settings=Settings()))
        resp = client.post(
            "/api/v1/ravn/budget/spend",
            json={"cost_usd": 0.1, "model": "m", "cap_usd": 1.0, "warn_at": 0.8},
        )
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Ravn budget persistence is unavailable"


class TestTriggerRoutes:
    def test_create_and_list_round_trip(self, tmp_path) -> None:
        client = _client(_app(settings=_settings(tmp_path)))
        resp = client.post(
            "/api/v1/ravn/triggers",
            json={
                "kind": "cron",
                "persona_name": "product-resident",
                "spec": "*/15 * * * *",
                "enabled": True,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["kind"] == "cron"
        assert body["persona_name"] == "product-resident"
        assert body["enabled"] is True

        listed = client.get("/api/v1/ravn/triggers").json()
        assert len(listed) == 1
        assert listed[0]["id"] == body["id"]

    def test_bad_cron_spec_is_rejected_with_422_not_stored(self, tmp_path) -> None:
        client = _client(_app(settings=_settings(tmp_path)))
        resp = client.post(
            "/api/v1/ravn/triggers",
            json={
                "kind": "cron",
                "persona_name": "p",
                "spec": "not a cron expression",
                "enabled": True,
            },
        )
        assert resp.status_code == 422
        assert client.get("/api/v1/ravn/triggers").json() == []

    def test_unknown_kind_is_rejected_with_422(self, tmp_path) -> None:
        client = _client(_app(settings=_settings(tmp_path)))
        resp = client.post(
            "/api/v1/ravn/triggers",
            json={"kind": "webhook", "persona_name": "p", "spec": "anything", "enabled": True},
        )
        assert resp.status_code == 422

    def test_event_spec_with_whitespace_is_rejected(self, tmp_path) -> None:
        client = _client(_app(settings=_settings(tmp_path)))
        resp = client.post(
            "/api/v1/ravn/triggers",
            json={
                "kind": "event",
                "persona_name": "p",
                "spec": "github pull request opened",
                "enabled": True,
            },
        )
        assert resp.status_code == 422

    def test_list_is_scoped_to_caller_tenant(self, tmp_path) -> None:
        app = _app(settings=_settings(tmp_path))
        tenant_a = _client(app, tenant="tenant-a")
        tenant_b = _client(app, tenant="tenant-b")

        tenant_a.post(
            "/api/v1/ravn/triggers",
            json={"kind": "cron", "persona_name": "p", "spec": "0 2 * * *", "enabled": True},
        )

        assert len(tenant_a.get("/api/v1/ravn/triggers").json()) == 1
        assert tenant_b.get("/api/v1/ravn/triggers").json() == []

    def test_delete_by_another_tenant_is_404_not_found(self, tmp_path) -> None:
        app = _app(settings=_settings(tmp_path))
        tenant_a = _client(app, tenant="tenant-a")
        tenant_b = _client(app, tenant="tenant-b")

        created = tenant_a.post(
            "/api/v1/ravn/triggers",
            json={"kind": "cron", "persona_name": "p", "spec": "0 2 * * *", "enabled": True},
        ).json()

        resp = tenant_b.delete(f"/api/v1/ravn/triggers/{created['id']}")
        assert resp.status_code == 404
        # Still there for its own tenant.
        assert len(tenant_a.get("/api/v1/ravn/triggers").json()) == 1

    def test_delete_by_non_owner_same_tenant_is_403(self, tmp_path) -> None:
        app = _app(settings=_settings(tmp_path))
        owner = _client(app, user_id="owner", tenant="tenant-a")
        other = _client(app, user_id="someone-else", tenant="tenant-a", roles="volundr:developer")

        created = owner.post(
            "/api/v1/ravn/triggers",
            json={"kind": "cron", "persona_name": "p", "spec": "0 2 * * *", "enabled": True},
        ).json()

        resp = other.delete(f"/api/v1/ravn/triggers/{created['id']}")
        assert resp.status_code == 403

    def test_delete_by_tenant_admin_succeeds(self, tmp_path) -> None:
        app = _app(settings=_settings(tmp_path))
        owner = _client(app, user_id="owner", tenant="tenant-a")
        admin = _client(app, user_id="admin-user", tenant="tenant-a", roles="volundr:admin")

        created = owner.post(
            "/api/v1/ravn/triggers",
            json={"kind": "cron", "persona_name": "p", "spec": "0 2 * * *", "enabled": True},
        ).json()

        resp = admin.delete(f"/api/v1/ravn/triggers/{created['id']}")
        assert resp.status_code == 204
        assert owner.get("/api/v1/ravn/triggers").json() == []

    def test_delete_owner_succeeds(self, tmp_path) -> None:
        client = _client(_app(settings=_settings(tmp_path)))
        created = client.post(
            "/api/v1/ravn/triggers",
            json={"kind": "cron", "persona_name": "p", "spec": "0 2 * * *", "enabled": True},
        ).json()
        resp = client.delete(f"/api/v1/ravn/triggers/{created['id']}")
        assert resp.status_code == 204

    def test_delete_non_uuid_id_is_404_not_500(self, tmp_path) -> None:
        client = _client(_app(settings=_settings(tmp_path)))
        resp = client.delete("/api/v1/ravn/triggers/not-a-uuid")
        assert resp.status_code == 404

    def test_delete_missing_trigger_is_404(self, tmp_path) -> None:
        client = _client(_app(settings=_settings(tmp_path)))
        resp = client.delete("/api/v1/ravn/triggers/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_create_for_a_persona_the_caller_does_not_administer_is_403(self, tmp_path) -> None:
        """Without this check, any tenant member could attach a trigger to
        someone else's resident — the resident executes it regardless of who
        created the row, since ApiTriggerSource only filters by
        persona_name, never by who owns the trigger."""
        client = _client(_app(settings=_settings(tmp_path)))
        resp = client.post(
            "/api/v1/ravn/triggers",
            json={
                "kind": "cron",
                "persona_name": "not-one-of-my-residents",
                "spec": "0 2 * * *",
                "enabled": True,
            },
        )
        assert resp.status_code == 403
        assert client.get("/api/v1/ravn/triggers").json() == []


class TestEventTriggerRepoAllowlist:
    """No live signal exists to check a freeform event-trigger `repo` field
    against real tenant ownership (see ravn.config.TriggerRepoAllowlistConfig
    for why), so it is checked against an explicit, operator-maintained,
    default-deny allowlist instead. Without this, any tenant member could
    name another tenant's private repo and receive its PR/push/issue
    content over the (system-wide) Sleipnir event bus."""

    def _settings_with_grants(self, tmp_path, grants: dict[str, list[str]]):
        return Settings(
            trigger_store={
                "adapter": "ravn.adapters.trigger_store.FileTriggerStore",
                "kwargs": {"path": str(tmp_path / "triggers.json")},
            },
            budget_ledger={
                "adapter": "ravn.adapters.budget_ledger.FileBudgetLedger",
                "kwargs": {"path": str(tmp_path / "ledger.json")},
            },
            trigger_repo_allowlist={"grants": grants},
        )

    def test_event_trigger_for_an_ungranted_repo_is_422(self, tmp_path) -> None:
        client = _client(_app(settings=self._settings_with_grants(tmp_path, {})))
        resp = client.post(
            "/api/v1/ravn/triggers",
            json={
                "kind": "event",
                "persona_name": "p",
                "spec": "github.pr.opened",
                "repo": "someone-elses/private-repo",
                "enabled": True,
            },
        )
        assert resp.status_code == 422
        assert "not granted" in resp.json()["detail"]

    def test_event_trigger_for_a_granted_repo_succeeds(self, tmp_path) -> None:
        settings = self._settings_with_grants(tmp_path, {"default": ["org/repo"]})
        client = _client(_app(settings=settings))
        resp = client.post(
            "/api/v1/ravn/triggers",
            json={
                "kind": "event",
                "persona_name": "p",
                "spec": "github.pr.opened",
                "repo": "org/repo",
                "enabled": True,
            },
        )
        assert resp.status_code == 201

    def test_wildcard_grant_allows_any_repo_for_that_tenant(self, tmp_path) -> None:
        settings = self._settings_with_grants(tmp_path, {"default": ["*"]})
        client = _client(_app(settings=settings))
        resp = client.post(
            "/api/v1/ravn/triggers",
            json={
                "kind": "event",
                "persona_name": "p",
                "spec": "github.pr.opened",
                "repo": "any/repo-at-all",
                "enabled": True,
            },
        )
        assert resp.status_code == 201

    def test_a_grant_for_a_different_tenant_does_not_apply(self, tmp_path) -> None:
        settings = self._settings_with_grants(tmp_path, {"tenant-a": ["org/repo"]})
        client = _client(_app(settings=settings), tenant="tenant-b")
        resp = client.post(
            "/api/v1/ravn/triggers",
            json={
                "kind": "event",
                "persona_name": "p",
                "spec": "github.pr.opened",
                "repo": "org/repo",
                "enabled": True,
            },
        )
        assert resp.status_code == 422

    def test_cron_triggers_are_never_subject_to_the_repo_allowlist(self, tmp_path) -> None:
        """Cron triggers carry no repo at all — the allowlist check must
        only run for kind == "event"."""
        client = _client(_app(settings=self._settings_with_grants(tmp_path, {})))
        resp = client.post(
            "/api/v1/ravn/triggers",
            json={"kind": "cron", "persona_name": "p", "spec": "0 2 * * *", "enabled": True},
        )
        assert resp.status_code == 201


class TestBudgetRoutes:
    def test_budget_for_unknown_ravn_is_zero(self, tmp_path) -> None:
        client = _client(_app(settings=_settings(tmp_path)))
        resp = client.get("/api/v1/ravn/budget/some-ravn-id")
        assert resp.status_code == 200
        assert resp.json() == {"spent_usd": 0.0, "cap_usd": 0.0, "warn_at": 0.0}

    def test_fleet_budget_empty_is_zero(self, tmp_path) -> None:
        client = _client(_app(settings=_settings(tmp_path)))
        resp = client.get("/api/v1/ravn/budget/fleet")
        assert resp.status_code == 200
        assert resp.json() == {"spent_usd": 0.0, "cap_usd": 0.0, "warn_at": 0.0}

    def test_budget_reports_what_was_recorded_and_warn_at_is_a_fraction(self, tmp_path) -> None:
        from datetime import UTC, datetime

        from ravn.adapters.budget_ledger.file_store import FileBudgetLedger

        settings = _settings(tmp_path)
        ledger = FileBudgetLedger(_budget_ledger_path(settings))
        import asyncio

        asyncio.run(
            ledger.record_spend(
                "ravn-1",
                tenant_id="default",
                day=datetime.now(UTC).date(),
                cost_usd=0.42,
                cap_usd=1.0,
                warn_at=0.8,
            )
        )
        client = _client(_app(settings=settings))
        resp = client.get("/api/v1/ravn/budget/ravn-1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["spent_usd"] == 0.42
        assert body["cap_usd"] == 1.0
        assert body["warn_at"] == 0.8

    def test_budget_is_scoped_to_caller_tenant(self, tmp_path) -> None:
        from datetime import UTC, datetime

        from ravn.adapters.budget_ledger.file_store import FileBudgetLedger

        settings = _settings(tmp_path)
        ledger = FileBudgetLedger(_budget_ledger_path(settings))
        import asyncio

        asyncio.run(
            ledger.record_spend(
                "ravn-1",
                tenant_id="tenant-a",
                day=datetime.now(UTC).date(),
                cost_usd=5.0,
                cap_usd=10.0,
                warn_at=0.8,
            )
        )
        app = _app(settings=settings)
        tenant_b = _client(app, tenant="tenant-b")
        resp = tenant_b.get("/api/v1/ravn/budget/ravn-1")
        assert resp.json()["spent_usd"] == 0.0


class TestBudgetSpendAndMe:
    """POST /budget/spend + GET /budget/me — no ravn_id/tenant_id ever in the
    request; both are derived server-side from the caller's principal."""

    def test_spend_then_me_round_trip_for_the_same_caller(self, tmp_path) -> None:
        client = _client(
            _app(settings=_settings(tmp_path)), user_id="resident-1", owner_scoped=True
        )

        resp = client.post(
            "/api/v1/ravn/budget/spend",
            json={"cost_usd": 0.42, "model": "claude-sonnet-4-6", "cap_usd": 1.0, "warn_at": 0.8},
        )
        assert resp.status_code == 200
        assert resp.json() == {"spent_usd": 0.42, "cap_usd": 1.0, "warn_at": 0.8}

        me = client.get("/api/v1/ravn/budget/me")
        assert me.json()["spent_usd"] == 0.42
        # This client's bearer token carries workload_owner_scoped: true
        # (owner_scoped=True above, required now for /budget/spend to admit
        # it at all) — /budget/me honestly reflects that back.
        assert me.json()["owner_scoped"] is True

    def test_spend_accumulates_across_calls(self, tmp_path) -> None:
        client = _client(
            _app(settings=_settings(tmp_path)), user_id="resident-1", owner_scoped=True
        )
        client.post(
            "/api/v1/ravn/budget/spend",
            json={"cost_usd": 0.1, "model": "m", "cap_usd": 1.0, "warn_at": 0.8},
        )
        resp = client.post(
            "/api/v1/ravn/budget/spend",
            json={"cost_usd": 0.2, "model": "m", "cap_usd": 1.0, "warn_at": 0.8},
        )
        assert resp.json()["spent_usd"] == pytest.approx(0.3)

    def test_spend_without_an_owner_scoped_token_is_403(self, tmp_path) -> None:
        """The MUST-FIX this closes: without this check, any authenticated
        caller (human session, ordinary PAT, or a workload token that only
        proves membership in a shared mapping) could write spend rows and
        inflate the tenant's fleet totals — only a resident's own
        PlatformBudgetReporter should ever be able to call this route."""
        client = _client(_app(settings=_settings(tmp_path)), user_id="resident-1")
        resp = client.post(
            "/api/v1/ravn/budget/spend",
            json={"cost_usd": 0.1, "model": "m", "cap_usd": 1.0, "warn_at": 0.8},
        )
        assert resp.status_code == 403
        # And nothing was recorded.
        me = _client(
            _app(settings=_settings(tmp_path)), user_id="resident-1", owner_scoped=True
        ).get("/api/v1/ravn/budget/me")
        assert me.json()["spent_usd"] == 0.0

    def test_two_different_callers_do_not_share_spend(self, tmp_path) -> None:
        settings = _settings(tmp_path)
        app = _app(settings=settings)
        resident_a = _client(app, user_id="resident-a", tenant="tenant-x", owner_scoped=True)
        resident_b = _client(app, user_id="resident-b", tenant="tenant-x")

        resident_a.post(
            "/api/v1/ravn/budget/spend",
            json={"cost_usd": 9.0, "model": "m", "cap_usd": 10.0, "warn_at": 0.8},
        )
        me_b = resident_b.get("/api/v1/ravn/budget/me")
        assert me_b.json()["spent_usd"] == 0.0

    def test_spend_rejects_warn_at_outside_0_1(self, tmp_path) -> None:
        client = _client(_app(settings=_settings(tmp_path)))
        resp = client.post(
            "/api/v1/ravn/budget/spend",
            json={"cost_usd": 0.1, "model": "m", "cap_usd": 1.0, "warn_at": 80},
        )
        assert resp.status_code == 422

    def test_spend_rejects_negative_cost(self, tmp_path) -> None:
        client = _client(_app(settings=_settings(tmp_path)))
        resp = client.post(
            "/api/v1/ravn/budget/spend",
            json={"cost_usd": -1.0, "model": "m", "cap_usd": 1.0, "warn_at": 0.5},
        )
        assert resp.status_code == 422
