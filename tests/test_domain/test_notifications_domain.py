"""Forge notification domain: scope, queries, rule matching, quiet hours, ids."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from niuu.domain.models import Principal
from niuu.domain.notifications import (
    NotificationKind,
    NotificationSeverity,
    NotificationSource,
    notification_id,
    notification_visible_to,
)
from volundr.domain.models import Session
from volundr.domain.notifications import (
    Notification,
    NotificationCandidate,
    NotificationQuery,
    NotificationQuietHours,
    NotificationRule,
    NotificationRuleMatch,
    NotificationRuleSpec,
    NotificationScope,
    rules_matching,
)
from volundr.domain.services.notifications import (
    attention_dedupe_key,
    engine_resolver_for,
    submit_dedupe_key,
)


def _notification(**overrides) -> Notification:
    values = {
        "id": uuid4(),
        "seq": 1,
        "dedupe_key": "k",
        "owner_id": "owner-a",
        "tenant_id": "tenant-a",
        "kind": NotificationKind.MILESTONE,
        "severity": NotificationSeverity.INFO,
        "source": NotificationSource.AGENT,
        "title": "t",
        "created_at": datetime.now(UTC),
        "metadata": {"turn_id": "x"},
    }
    values.update(overrides)
    return Notification(**values)


def _rule(**overrides) -> NotificationRule:
    now = datetime.now(UTC)
    values = {
        "id": uuid4(),
        "owner_id": "owner-a",
        "name": "r",
        "sink": "ops",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return NotificationRule(**values)


class TestVisibility:
    def test_owner_sees_own_notification_in_any_tenant(self):
        assert notification_visible_to(
            user_id="u", roles=[], tenant_id="t1", owner_id="u", notification_tenant_id="t2"
        )

    def test_non_admin_never_sees_another_owner(self):
        assert not notification_visible_to(
            user_id="u",
            roles=["volundr:developer"],
            tenant_id="t",
            owner_id="v",
            notification_tenant_id="t",
        )

    @pytest.mark.parametrize(
        ("tenant", "expected"), [("t", True), (None, True), ("", True), ("other", False)]
    )
    def test_admin_sees_own_tenant_and_untenanted(self, tenant, expected):
        assert (
            notification_visible_to(
                user_id="admin",
                roles=["volundr:admin"],
                tenant_id="t",
                owner_id="v",
                notification_tenant_id=tenant,
            )
            is expected
        )

    def test_empty_owner_is_nobody(self):
        assert not notification_visible_to(
            user_id="", roles=[], tenant_id="t", owner_id="", notification_tenant_id="t"
        )

    def test_scope_for_principal(self):
        admin = NotificationScope.for_principal(
            Principal(user_id="a", email="", tenant_id="t", roles=["volundr:admin"])
        )
        dev = NotificationScope.for_principal(
            Principal(user_id="d", email="", tenant_id="", roles=["volundr:developer"])
        )
        assert admin.is_admin and admin.allows("x", "t") and not admin.allows("x", "u")
        assert not dev.is_admin and dev.tenant_id == "" and dev.allows("d", "t")
        assert not dev.allows("x", None)


class TestQuery:
    def test_before_and_after_are_exclusive(self):
        with pytest.raises(ValueError, match="either"):
            NotificationQuery(limit=5, before=3, after=1)

    def test_limit_must_be_positive(self):
        with pytest.raises(ValueError, match="limit"):
            NotificationQuery(limit=0)

    def test_severities_at_or_above_minimum(self):
        assert NotificationQuery(limit=1).severities() == ()
        assert NotificationQuery(
            limit=1, min_severity=NotificationSeverity.WARNING
        ).severities() == (
            NotificationSeverity.WARNING,
            NotificationSeverity.CRITICAL,
        )
        assert NotificationQuery(limit=1, after=0).ascending
        assert not NotificationQuery(limit=1, before=9).ascending


class TestRuleMatch:
    def test_empty_match_selects_everything(self):
        assert NotificationRuleMatch().matches(_notification())

    @pytest.mark.parametrize(
        ("match", "notification", "expected"),
        [
            ({"kinds": ["decision"]}, {}, False),
            ({"kinds": ["milestone", "decision"]}, {}, True),
            ({"min_severity": "warning"}, {"severity": "success"}, False),
            ({"min_severity": "warning"}, {"severity": "critical"}, True),
            ({"sources": ["system"]}, {}, False),
            ({"sources": ["agent"]}, {}, True),
            ({"project_ids": ["p1"]}, {}, False),
            ({"project_ids": ["p1"]}, {"project_id": "p1"}, True),
        ],
    )
    def test_each_dimension(self, match, notification, expected):
        assert NotificationRuleMatch(**match).matches(_notification(**notification)) is expected

    def test_session_ids(self):
        sid = uuid4()
        match = NotificationRuleMatch(session_ids=[sid])
        assert match.matches(_notification(session_id=sid))
        assert not match.matches(_notification(session_id=uuid4()))
        assert not match.matches(_notification())

    def test_rejects_unknown_fields_and_blank_projects(self):
        with pytest.raises(ValidationError):
            NotificationRuleMatch(channels=["x"])
        with pytest.raises(ValidationError, match="project_ids"):
            NotificationRuleMatch(project_ids=[""])

    def test_rules_matching_is_owner_and_enabled_scoped(self):
        keep = _rule()
        rules = [
            keep,
            _rule(enabled=False),
            _rule(owner_id="owner-b"),
            _rule(match=NotificationRuleMatch(kinds=["error"])),
        ]
        assert rules_matching(_notification(), rules) == [keep]


class TestQuietHours:
    @pytest.mark.parametrize("value", ["7:00", "24:00", "12:60", "noon"])
    def test_rejects_bad_clock(self, value):
        with pytest.raises(ValidationError, match="HH:MM"):
            NotificationQuietHours(start=value, end="07:00", timezone="UTC")

    def test_rejects_unknown_timezone_and_empty_window(self):
        with pytest.raises(ValidationError, match="timezone"):
            NotificationQuietHours(start="22:00", end="07:00", timezone="Mars/Olympus")
        with pytest.raises(ValidationError, match="differ"):
            NotificationQuietHours(start="07:00", end="07:00", timezone="UTC")

    def test_overnight_window_in_its_timezone(self):
        quiet = NotificationQuietHours(start="22:00", end="07:00", timezone="Europe/London")
        winter = datetime(2026, 1, 10, 23, 30, tzinfo=UTC)  # 23:30 London
        summer = datetime(2026, 7, 10, 21, 30, tzinfo=UTC)  # 22:30 London (BST)
        assert quiet.is_quiet(winter) and quiet.is_quiet(summer)
        assert quiet.is_quiet(datetime(2026, 1, 10, 6, 59, tzinfo=UTC))
        assert not quiet.is_quiet(datetime(2026, 1, 10, 7, 0, tzinfo=UTC))
        assert not quiet.is_quiet(datetime(2026, 1, 10, 12, 0, tzinfo=UTC))

    def test_daytime_window_and_severity_override(self):
        quiet = NotificationQuietHours(
            start="09:00", end="17:00", timezone="UTC", allow_min_severity="warning"
        )
        noon = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
        assert quiet.holds_back(NotificationSeverity.INFO, noon)
        assert not quiet.holds_back(NotificationSeverity.WARNING, noon)
        assert not quiet.holds_back(
            NotificationSeverity.INFO, datetime(2026, 1, 10, 18, tzinfo=UTC)
        )

    def test_requires_aware_instant(self):
        quiet = NotificationQuietHours(start="09:00", end="17:00", timezone="UTC")
        with pytest.raises(ValueError, match="timezone-aware"):
            quiet.is_quiet(datetime(2026, 1, 10, 12))


class TestRuleSpec:
    def test_name_is_normalized_and_required(self):
        assert NotificationRuleSpec(name="  my   rule ", sink="ops").name == "my rule"
        with pytest.raises(ValidationError, match="blank"):
            NotificationRuleSpec(name="   ", sink="ops")

    @pytest.mark.parametrize("sink", ["Ops", "-ops", "a b", "x" * 65])
    def test_sink_name_pattern(self, sink):
        with pytest.raises(ValidationError):
            NotificationRuleSpec(name="r", sink=sink)

    def test_rate_limit_is_validated(self):
        spec = NotificationRuleSpec(
            name="r", sink="ops", config={"rate_limit": {"max_count": 2, "window_seconds": 60}}
        )
        assert spec.rate_limit.window_seconds == 60
        assert NotificationRuleSpec(name="r", sink="ops").rate_limit is None
        with pytest.raises(ValidationError):
            NotificationRuleSpec(name="r", sink="ops", config={"rate_limit": {"max_count": 0}})
        with pytest.raises(ValidationError, match="at most"):
            NotificationRuleSpec(name="r", sink="ops", config={str(i): i for i in range(51)})


class TestIdentities:
    def test_candidate_id_is_deterministic(self):
        candidate = NotificationCandidate(
            dedupe_key="turn:s:t:milestone",
            owner_id="o",
            kind="milestone",
            severity="info",
            source="agent",
            title="x",
        )
        assert candidate.id == notification_id("turn:s:t:milestone")

    def test_wire_excludes_internal_fields(self):
        wire = _notification().wire()
        assert "dedupe_key" not in wire and "metadata" not in wire
        assert wire["kind"] == "milestone" and isinstance(wire["id"], str)

    def test_wire_exposes_the_anchor_turn_or_an_explicit_none(self):
        assert _notification().wire()["turn_id"] == "x"
        assert _notification(metadata={}).wire()["turn_id"] is None
        assert _notification(metadata={"turn_id": ""}).turn_id is None
        assert _notification(metadata={"turn_id": 7}).turn_id is None

    def test_attention_and_submit_keys(self):
        sid = uuid4()
        since = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
        assert attention_dedupe_key(sid, since, "") == f"attention:{sid}:{since.isoformat()}"
        assert attention_dedupe_key(sid, since, "q1").endswith(":q1")
        assert submit_dedupe_key(sid, "u", "k") == f"submit:{sid}:u:k"
        assert submit_dedupe_key(None, "u", "k") == "submit:-:u:k"

    def test_engine_resolver(self):
        resolve = engine_resolver_for(
            {"skuldClaude": "claude", "skuldCodex": "codex-ws", "bare": None}, "skuldClaude"
        )
        assert resolve(Session(name="s")) == "claude"
        assert resolve(Session(name="s", session_definition="skuldCodex")) == "codex"
        assert resolve(Session(name="s", session_definition="bare")) is None
        assert resolve(Session(name="s", session_definition="unknown")) is None
