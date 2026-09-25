"""Unit tests for the two ResidentBudgetPort adapters.

``LocalBudgetReporter`` (mini mode, explicit ravn_id/tenant_id) and
``PlatformBudgetReporter`` (production, identity derived server-side from the
workload-authenticated caller) are each dynamic adapters in their own right
and get no coverage from the DriveLoop or Ravn-API integration tests, which
exercise fakes/real HTTP round trips respectively rather than these classes
directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from ravn.adapters.resident_budget.local import LocalBudgetReporter
from ravn.adapters.resident_budget.platform import PlatformBudgetReporter
from ravn.domain.budget_totals import BudgetTotals


@dataclass(frozen=True)
class _FakeResponse:
    status_code: int
    body: Any


class _FakeClient:
    """Records calls and returns pre-scripted responses, in order."""

    def __init__(
        self, *, get_response: _FakeResponse, post_response: _FakeResponse | None = None
    ) -> None:
        self._get_response = get_response
        self._post_response = post_response
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, dict]] = []

    async def get(self, url: str, *, headers: dict[str, str] | None = None) -> _FakeResponse:
        del headers
        self.get_calls.append(url)
        return self._get_response

    async def post(
        self, url: str, json_body: dict[str, Any], *, headers: dict[str, str] | None = None
    ) -> _FakeResponse:
        del headers
        self.post_calls.append((url, json_body))
        assert self._post_response is not None
        return self._post_response


class TestLocalBudgetReporter:
    def test_requires_ravn_id_and_tenant_id(self) -> None:
        with pytest.raises(ValueError, match="ravn_id and tenant_id"):
            LocalBudgetReporter(ravn_id="", tenant_id="default")
        with pytest.raises(ValueError, match="ravn_id and tenant_id"):
            LocalBudgetReporter(ravn_id="resident-1", tenant_id="")

    async def test_seed_today_is_zero_for_a_fresh_ledger(self, tmp_path) -> None:
        reporter = LocalBudgetReporter(
            ravn_id="resident-1",
            tenant_id="default",
            ledger_kwargs={"path": str(tmp_path / "ledger.json")},
        )
        assert await reporter.seed_today() == 0.0

    async def test_record_spend_then_seed_today_round_trip(self, tmp_path) -> None:
        reporter = LocalBudgetReporter(
            ravn_id="resident-1",
            tenant_id="default",
            ledger_kwargs={"path": str(tmp_path / "ledger.json")},
        )
        totals = await reporter.record_spend(
            cost_usd=0.42, model="claude-sonnet-4-6", cap_usd=1.0, warn_at=0.8
        )
        assert totals == BudgetTotals(spent_usd=0.42, cap_usd=1.0, warn_at=0.8)

        # A second reporter instance (simulating a restart) reads the same
        # durable file and must see the prior spend, not start from zero.
        restarted = LocalBudgetReporter(
            ravn_id="resident-1",
            tenant_id="default",
            ledger_kwargs={"path": str(tmp_path / "ledger.json")},
        )
        assert await restarted.seed_today() == pytest.approx(0.42)

    async def test_two_ravn_ids_do_not_share_a_ledger_row(self, tmp_path) -> None:
        ledger_kwargs = {"path": str(tmp_path / "ledger.json")}
        reporter_a = LocalBudgetReporter(
            ravn_id="resident-a", tenant_id="default", ledger_kwargs=ledger_kwargs
        )
        reporter_b = LocalBudgetReporter(
            ravn_id="resident-b", tenant_id="default", ledger_kwargs=ledger_kwargs
        )
        await reporter_a.record_spend(cost_usd=5.0, model="m", cap_usd=10.0, warn_at=0.8)
        assert await reporter_b.seed_today() == 0.0


class TestPlatformBudgetReporter:
    async def test_seed_today_returns_spent_usd_when_response_is_owner_scoped(self) -> None:
        client = _FakeClient(
            get_response=_FakeResponse(
                status_code=200,
                body={"spent_usd": 0.42, "cap_usd": 1.0, "warn_at": 0.8, "owner_scoped": True},
            )
        )
        reporter = PlatformBudgetReporter(base_url="https://volundr.internal", client=client)

        result = await reporter.seed_today()

        assert result == 0.42
        assert client.get_calls == ["https://volundr.internal/api/v1/ravn/budget/me"]

    async def test_seed_today_raises_when_not_owner_scoped(self) -> None:
        """The gap this round closes: a caller matched by a workload-identity
        mapping without owner_id_claim shares its owner_id with every other
        caller of that mapping — /budget/me would read that shared row, not
        this resident's own. Refuse to seed (and thus refuse to start) rather
        than silently report someone else's numbers."""
        client = _FakeClient(
            get_response=_FakeResponse(
                status_code=200,
                body={"spent_usd": 999.0, "cap_usd": 1.0, "warn_at": 0.8, "owner_scoped": False},
            )
        )
        reporter = PlatformBudgetReporter(base_url="https://volundr.internal", client=client)

        with pytest.raises(ValueError, match="not resident-scoped"):
            await reporter.seed_today()

    async def test_seed_today_raises_when_owner_scoped_key_is_missing(self) -> None:
        """An older/misconfigured server that never sets the claim must fail
        loud, not be treated as scoped by a permissive default."""
        client = _FakeClient(
            get_response=_FakeResponse(
                status_code=200,
                body={"spent_usd": 0.0, "cap_usd": 1.0, "warn_at": 0.8},
            )
        )
        reporter = PlatformBudgetReporter(base_url="https://volundr.internal", client=client)

        with pytest.raises(ValueError, match="not resident-scoped"):
            await reporter.seed_today()

    async def test_seed_today_raises_on_non_200(self) -> None:
        client = _FakeClient(get_response=_FakeResponse(status_code=503, body={}))
        reporter = PlatformBudgetReporter(base_url="https://volundr.internal", client=client)

        with pytest.raises(ValueError, match="HTTP 503"):
            await reporter.seed_today()

    async def test_seed_today_raises_on_unexpected_body(self) -> None:
        client = _FakeClient(get_response=_FakeResponse(status_code=200, body={"unexpected": True}))
        reporter = PlatformBudgetReporter(base_url="https://volundr.internal", client=client)

        with pytest.raises(ValueError, match="unexpected body"):
            await reporter.seed_today()

    async def test_record_spend_returns_totals_from_the_response(self) -> None:
        client = _FakeClient(
            get_response=_FakeResponse(status_code=200, body={}),
            post_response=_FakeResponse(
                status_code=200, body={"spent_usd": 0.3, "cap_usd": 1.0, "warn_at": 0.8}
            ),
        )
        reporter = PlatformBudgetReporter(base_url="https://volundr.internal", client=client)

        totals = await reporter.record_spend(
            cost_usd=0.1, model="claude-sonnet-4-6", cap_usd=1.0, warn_at=0.8
        )

        assert totals == BudgetTotals(spent_usd=0.3, cap_usd=1.0, warn_at=0.8)
        [(url, body)] = client.post_calls
        assert url == "https://volundr.internal/api/v1/ravn/budget/spend"
        assert body == {
            "cost_usd": 0.1,
            "model": "claude-sonnet-4-6",
            "cap_usd": 1.0,
            "warn_at": 0.8,
        }

    async def test_record_spend_raises_on_non_200(self) -> None:
        client = _FakeClient(
            get_response=_FakeResponse(status_code=200, body={}),
            post_response=_FakeResponse(status_code=500, body={}),
        )
        reporter = PlatformBudgetReporter(base_url="https://volundr.internal", client=client)

        with pytest.raises(ValueError, match="not durably recorded"):
            await reporter.record_spend(cost_usd=0.1, model="m", cap_usd=1.0, warn_at=0.8)

    async def test_record_spend_raises_on_non_object_body(self) -> None:
        client = _FakeClient(
            get_response=_FakeResponse(status_code=200, body={}),
            post_response=_FakeResponse(status_code=200, body="not-a-dict"),
        )
        reporter = PlatformBudgetReporter(base_url="https://volundr.internal", client=client)

        with pytest.raises(ValueError, match="non-object body"):
            await reporter.record_spend(cost_usd=0.1, model="m", cap_usd=1.0, warn_at=0.8)

    def test_base_url_trailing_slash_is_stripped(self) -> None:
        client = _FakeClient(get_response=_FakeResponse(status_code=200, body={}))
        reporter = PlatformBudgetReporter(base_url="https://volundr.internal/", client=client)
        assert reporter._base_url == "https://volundr.internal"  # noqa: SLF001
