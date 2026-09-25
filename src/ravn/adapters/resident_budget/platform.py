"""Report budget spend through the Ravn platform API (production path).

Uses the same workload-authenticated HTTP boundary as the trigger poll
(``ravn.adapters.triggers.api_source``, ``ravn.adapters.realm.client``): the
server derives ``ravn_id``/``tenant_id`` from the caller's workload
principal, so this adapter never carries either — no Postgres credentials
and no per-resident identity config ship to the pod.
"""

from __future__ import annotations

from typing import Any

from ravn.domain.budget_totals import BudgetTotals

#: HTTP status meaning the request succeeded.
_HTTP_OK = 200


class PlatformBudgetReporter:
    """Reads/records this resident's own spend via ``/api/v1/ravn/budget``.

    Plain-kwargs constructible (dynamic-adapters.md) like every other
    dynamic adapter: builds its own workload-authenticated client from
    ``base_url``/``pat_token``/``workload_token_file``/etc — the same shape
    ``gateway.platform`` already uses — rather than taking a live client
    object. ``client`` is accepted only for tests that supply a fake.
    """

    def __init__(
        self,
        *,
        base_url: str,
        client: Any = None,
        pat_token: str = "",
        workload_token_file: str = "",
        workload_exchange_url: str = "",
        workload_audiences: list[str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        if client is not None:
            self._client = client
        else:
            from ravn.adapters.tool_build.http import client_from_workload_identity  # noqa: PLC0415

            self._client = client_from_workload_identity(
                base_url=self._base_url,
                external_token=pat_token,
                workload_token_file=workload_token_file,
                workload_exchange_url=workload_exchange_url,
                workload_audiences=workload_audiences,
                timeout_seconds=timeout_seconds,
            )

    async def seed_today(self) -> float:
        response = await self._client.get(f"{self._base_url}/api/v1/ravn/budget/me")
        if response.status_code != _HTTP_OK:
            raise ValueError(
                f"budget: GET /budget/me returned HTTP {response.status_code} — "
                "cannot seed today's spend"
            )
        body = response.body
        if not isinstance(body, dict) or "spent_usd" not in body:
            raise ValueError(f"budget: /budget/me returned an unexpected body: {body!r}")
        if not body.get("owner_scoped", False):
            raise ValueError(
                "budget: this resident's workload-identity principal is not "
                "resident-scoped, so /budget/me and /budget/spend would key "
                "rows by an id shared with every other caller the matched "
                "workload-identity mapping admits — not this resident's own "
                "spend. Set workloadIdentity.residentMapping.enabled=true and "
                ".namespace (charts/volundr/values.yaml) so each resident's "
                "ServiceAccount subject resolves to its own owner_id, or clear "
                "resident_budget.adapter (this resident's Ravn config) rather "
                "than run PlatformBudgetReporter against a shared identity."
            )
        return float(body["spent_usd"])

    async def record_spend(
        self,
        *,
        cost_usd: float,
        model: str,
        cap_usd: float,
        warn_at: float,
    ) -> BudgetTotals:
        response = await self._client.post(
            f"{self._base_url}/api/v1/ravn/budget/spend",
            json_body={
                "cost_usd": cost_usd,
                "model": model,
                "cap_usd": cap_usd,
                "warn_at": warn_at,
            },
        )
        if response.status_code != _HTTP_OK:
            raise ValueError(
                f"budget: POST /budget/spend returned HTTP {response.status_code} — "
                "spend was not durably recorded"
            )
        body = response.body
        if not isinstance(body, dict):
            raise ValueError(f"budget: /budget/spend returned a non-object body: {body!r}")
        return BudgetTotals(
            spent_usd=float(body["spent_usd"]),
            cap_usd=float(body["cap_usd"]),
            warn_at=float(body["warn_at"]),
        )
