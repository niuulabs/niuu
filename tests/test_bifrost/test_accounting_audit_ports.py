"""Tests for accounting and audit port dataclasses and interface contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bifrost.ports.accounting import (
    AccountingPort,
    AccountingSummary,
    AccountingTimeSeries,
    RequestRecord,
)
from bifrost.ports.audit import AuditEvent, AuditPort


class TestRequestRecord:
    def test_minimal_construction(self):
        r = RequestRecord(
            agent_id="agent-1",
            tenant_id="tenant-1",
            model="claude-sonnet-4-6",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            timestamp=datetime.now(UTC),
        )
        assert r.agent_id == "agent-1"
        assert r.request_id == ""
        assert r.streaming is False
        assert r.cache_read_tokens == 0
        assert r.cache_write_tokens == 0
        assert r.reasoning_tokens == 0

    def test_full_construction(self):
        ts = datetime.now(UTC)
        r = RequestRecord(
            agent_id="agent-x",
            tenant_id="tenant-x",
            model="gpt-4o",
            input_tokens=200,
            output_tokens=80,
            cost_usd=0.005,
            timestamp=ts,
            request_id="req-abc",
            session_id="sess-123",
            saga_id="saga-456",
            provider="openai",
            latency_ms=150.5,
            streaming=True,
            cache_read_tokens=10,
            cache_write_tokens=5,
            reasoning_tokens=20,
        )
        assert r.provider == "openai"
        assert r.streaming is True
        assert r.reasoning_tokens == 20


class TestAccountingSummary:
    def test_defaults_are_zero(self):
        s = AccountingSummary()
        assert s.total_requests == 0
        assert s.total_input_tokens == 0
        assert s.total_output_tokens == 0
        assert s.total_cost_usd == 0.0
        assert s.by_model == {}
        assert s.by_provider == {}


class TestAccountingTimeSeries:
    def test_construction(self):
        ts = AccountingTimeSeries(
            bucket="2026-04-05T00:00:00+00:00",
            requests=10,
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.05,
        )
        assert ts.bucket == "2026-04-05T00:00:00+00:00"
        assert ts.requests == 10


class TestAuditEvent:
    def test_minimal_construction(self):
        e = AuditEvent(
            request_id="req-1",
            agent_id="agent-1",
            tenant_id="tenant-1",
            model="claude-sonnet-4-6",
            timestamp=datetime.now(UTC),
        )
        assert e.outcome == "success"
        assert e.status_code == 200
        assert e.rule_name == ""
        assert e.tags == {}
        assert e.error_message == ""

    def test_rejection_event(self):
        e = AuditEvent(
            request_id="req-2",
            agent_id="agent-2",
            tenant_id="tenant-2",
            model="gpt-4o",
            timestamp=datetime.now(UTC),
            outcome="rejected",
            status_code=400,
            rule_name="block-adult-content",
            rule_action="reject",
            error_message="Content policy violation",
            tags={"category": "adult"},
        )
        assert e.outcome == "rejected"
        assert e.tags == {"category": "adult"}


class TestAccountingPortIsAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            AccountingPort()  # type: ignore[abstract]

    def test_subclass_must_implement_all_methods(self):
        class Partial(AccountingPort):
            async def record(self, record):
                pass

        with pytest.raises(TypeError):
            Partial()  # type: ignore[abstract]


class TestAuditPortIsAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            AuditPort()  # type: ignore[abstract]

    def test_subclass_must_implement_all_methods(self):
        class Partial(AuditPort):
            async def log(self, event):
                pass

        with pytest.raises(TypeError):
            Partial()  # type: ignore[abstract]
