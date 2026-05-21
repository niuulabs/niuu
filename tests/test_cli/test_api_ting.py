"""Tests for cli.api.ting — Ting REST API methods."""

from __future__ import annotations

import httpx
import pytest
import respx

from cli.api.client import APIClient
from cli.api.ting import TingAPI

BASE = "http://ting.test"
V1 = "/api/v1/ting"


@pytest.fixture
def api() -> TingAPI:
    return TingAPI(APIClient(base_url=BASE, access_token="t"))


class TestListSagas:
    async def test_returns_sagas(self, api: TingAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{V1}/sagas").mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {"id": "sg1", "name": "deploy", "status": "active"},
                    ],
                )
            )
            sagas = await api.list_sagas()
        assert len(sagas) == 1
        assert sagas[0].name == "deploy"

    async def test_empty_list(self, api: TingAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{V1}/sagas").mock(return_value=httpx.Response(200, json=[]))
            sagas = await api.list_sagas()
        assert sagas == []


class TestGetSaga:
    async def test_returns_saga(self, api: TingAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{V1}/sagas/sg1").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "id": "sg1",
                        "name": "deploy",
                        "status": "active",
                        "description": "deployment saga",
                    },
                )
            )
            saga = await api.get_saga("sg1")
        assert saga is not None
        assert saga.description == "deployment saga"

    async def test_returns_none_for_404(self, api: TingAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{V1}/sagas/missing").mock(return_value=httpx.Response(404))
            saga = await api.get_saga("missing")
        assert saga is None


class TestCreateSaga:
    async def test_creates_saga(self, api: TingAPI) -> None:
        with respx.mock:
            respx.post(f"{BASE}{V1}/sagas").mock(
                return_value=httpx.Response(
                    201,
                    json={
                        "id": "sg2",
                        "name": "new-saga",
                        "status": "pending",
                    },
                )
            )
            saga = await api.create_saga("new-saga", description="test")
        assert saga.id == "sg2"


class TestListRuns:
    async def test_returns_runs(self, api: TingAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{V1}/sagas/sg1/runs").mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {"id": "r1", "saga_id": "sg1", "status": "running", "session_ids": ["s1"]},
                    ],
                )
            )
            runs = await api.list_runs("sg1")
        assert len(runs) == 1
        assert runs[0].session_ids == ["s1"]


class TestDispatch:
    async def test_dispatches_saga(self, api: TingAPI) -> None:
        with respx.mock:
            respx.post(f"{BASE}{V1}/sagas/sg1/dispatch").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "run_id": "r2",
                        "session_ids": ["s1", "s2"],
                    },
                )
            )
            result = await api.dispatch("sg1", params={"target": "prod"})
        assert result.run_id == "r2"
        assert len(result.session_ids) == 2
