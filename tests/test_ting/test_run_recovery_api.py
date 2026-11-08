"""The operator recovery routes: retry, reassign and abort, their auth and their refusals."""

from __future__ import annotations

from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.services.token_scope import VALKYRIE_BUILD_TOKEN_USE
from ting.api.research import resolve_workflow_campaign_repo
from ting.api.run_recovery import create_run_recovery_router
from ting.config import AuthConfig, Settings
from ting.domain.workflow_steps import (
    EVENT_RECOVERY,
    EVENT_RUN_CANCELED,
    StepState,
)

from .stuck_support import SLUG, STEP_DEADLINE_SECONDS, StuckHarness

OWNER_HEADERS = {"x-auth-user-id": "user-1", "x-auth-tenant": "tenant-1"}
OTHER_HEADERS = {"x-auth-user-id": "someone-else", "x-auth-tenant": "tenant-1"}
KEY = "test-key-for-run-recovery-routes-32-bytes"


def _scoped(scopes: list[str]) -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": "user-1",
            "tenant_id": "tenant-1",
            "token_use": VALKYRIE_BUILD_TOKEN_USE,
            "scopes": scopes,
        },
        KEY,
        algorithm="HS256",
    )
    return {**OWNER_HEADERS, "authorization": f"Bearer {token}"}


class Harness:
    def __init__(self, run: StuckHarness) -> None:
        self.run = run
        app = FastAPI()
        app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
        app.state.workflow_runs = run.runs
        app.include_router(create_run_recovery_router())
        app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: run.campaigns
        self.client = TestClient(app)

    def retry(self, activation_id, headers=OWNER_HEADERS, slug=SLUG, **body):
        return self.post(f"{slug}/steps/{activation_id}/retry", headers, body)

    def reassign(self, activation_id, headers=OWNER_HEADERS, slug=SLUG, **body):
        return self.post(f"{slug}/steps/{activation_id}/reassign", headers, body)

    def abort(self, headers=OWNER_HEADERS, slug=SLUG, **body):
        return self.post(f"{slug}/abort", headers, body)

    def post(self, path, headers, body):
        body = {"reason": "the session is wedged", **body}
        return self.client.post(f"/api/v1/ting/work/runs/{path}", headers=headers, json=body)


@pytest.fixture
async def stuck() -> StuckHarness:
    run = StuckHarness()
    await run.launch()
    run.advance(STEP_DEADLINE_SECONDS + 1)
    await run.sweep()
    return run


@pytest.fixture
def api(stuck: StuckHarness) -> Harness:
    return Harness(stuck)


async def test_a_reassign_moves_the_step_and_answers_with_the_new_attempt(
    api: Harness, stuck: StuckHarness
) -> None:
    old = stuck.step("alpha")

    response = api.reassign(old.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "reassigned" and body["attempt"] == 2 and body["state"] == "active"
    assert body["activation_id"] == str(stuck.step("alpha", attempt=2).id)
    assert stuck.step("alpha", attempt=1).state is StepState.CANCELED
    [recovery] = [e.payload for e in stuck.events(EVENT_RECOVERY)]
    assert recovery["by"] == {"kind": "person", "id": "user-1"}
    assert recovery["reason"] == "the session is wedged"


async def test_a_retry_of_a_failed_step_answers_with_the_new_attempt(
    api: Harness, stuck: StuckHarness
) -> None:
    alpha = stuck.step("alpha")
    await stuck.runs.record_step_closed(
        run_id=stuck.campaign.id,
        activation_id=alpha.id,
        session_id=alpha.session_id,
        attempt=1,
        report={"kind": "failed", "failure_reason": "could not build"},
    )
    stuck.forge.step_states[(alpha.session_id, str(alpha.id))] = {
        "state": "closed",
        "attempt": 1,
        "ending": {"kind": "failed", "failure_reason": "could not build"},
    }

    response = api.retry(alpha.id, reason="retry after the outage")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "retried" and response.json()["attempt"] == 2


async def test_a_retry_the_session_cannot_take_is_a_conflict_that_says_why(
    api: Harness, stuck: StuckHarness
) -> None:
    response = api.retry(stuck.step("alpha").id)

    assert response.status_code == 409
    assert "reassign the step to a fresh session" in response.json()["detail"]
    assert stuck.step("alpha").state is StepState.ACTIVE


async def test_a_step_that_is_not_stuck_is_a_conflict(api: Harness, stuck: StuckHarness) -> None:
    fresh = StuckHarness()
    await fresh.launch()
    client = Harness(fresh)

    response = client.reassign(fresh.step("alpha").id)

    assert response.status_code == 409
    assert "not flagged stuck" in response.json()["detail"]


async def test_a_step_the_run_does_not_have_is_not_found(api: Harness) -> None:
    response = api.retry(uuid4())

    assert response.status_code == 404
    assert "has no step" in response.json()["detail"]


async def test_a_recovery_that_cannot_reach_forge_is_a_bad_gateway(
    api: Harness, stuck: StuckHarness
) -> None:
    old = stuck.step("alpha")
    stuck.forge.fail_stop.add(old.session_id)

    response = api.reassign(old.id)

    assert response.status_code == 502
    assert "could not be stopped" in response.json()["detail"]
    assert stuck.step("alpha").state is StepState.ACTIVE


async def test_an_attempt_that_could_not_start_is_a_bad_gateway_naming_why(
    api: Harness, stuck: StuckHarness
) -> None:
    stuck.forge.fail_deliver = ConnectionError("forge is down")

    response = api.reassign(stuck.step("alpha").id)

    assert response.status_code == 502
    assert "could not be started" in response.json()["detail"]
    assert "forge is down" in response.json()["detail"]


async def test_abort_cancels_the_run(api: Harness, stuck: StuckHarness) -> None:
    response = api.abort(reason="wrong ticket")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "aborted", "run_status": "failed"}
    assert stuck.state.finished
    [canceled] = stuck.events(EVENT_RUN_CANCELED)
    assert canceled.payload["reason"] == "wrong ticket"
    [recovery] = [e.payload for e in stuck.events(EVENT_RECOVERY)]
    assert recovery == {
        "action": "abort",
        "by": {"kind": "person", "id": "user-1"},
        "reason": "wrong ticket",
    }


async def test_aborting_a_finished_run_is_a_conflict(api: Harness) -> None:
    assert api.abort().status_code == 200

    second = api.abort()

    assert second.status_code == 409
    assert "already finished" in second.json()["detail"]


@pytest.mark.parametrize("reason", ["", "   "])
async def test_every_action_needs_a_reason(api: Harness, stuck: StuckHarness, reason: str) -> None:
    alpha = stuck.step("alpha").id

    assert api.retry(alpha, reason=reason).status_code == 422
    assert api.reassign(alpha, reason=reason).status_code == 422
    assert api.abort(reason=reason).status_code == 422
    assert stuck.step("alpha").state is StepState.ACTIVE
    assert not stuck.state.finished


# --- auth -------------------------------------------------------------------------


async def test_a_caller_with_no_identity_is_unauthorized(api: Harness, stuck: StuckHarness) -> None:
    alpha = stuck.step("alpha").id

    for response in (
        api.retry(alpha, headers={}),
        api.reassign(alpha, headers={}),
        api.abort(headers={}),
    ):
        assert response.status_code == 401

    assert not stuck.events(EVENT_RECOVERY)


async def test_someone_elses_run_is_not_found_for_every_action(
    api: Harness, stuck: StuckHarness
) -> None:
    alpha = stuck.step("alpha").id

    responses = (
        api.retry(alpha, headers=OTHER_HEADERS),
        api.reassign(alpha, headers=OTHER_HEADERS),
        api.abort(headers=OTHER_HEADERS),
    )

    assert [r.status_code for r in responses] == [404, 404, 404]
    assert not stuck.events(EVENT_RECOVERY)
    assert not stuck.state.finished


async def test_a_scoped_workload_token_without_the_coordinate_scope_is_forbidden(
    api: Harness, stuck: StuckHarness
) -> None:
    alpha = stuck.step("alpha").id
    headers = _scoped([])

    responses = (api.retry(alpha, headers), api.reassign(alpha, headers), api.abort(headers))

    assert [r.status_code for r in responses] == [403, 403, 403]
    assert all("missing the required scope" in r.json()["detail"] for r in responses)
    assert not stuck.events(EVENT_RECOVERY)


async def test_a_scoped_token_with_the_coordinate_scope_acts_as_its_owner(
    api: Harness, stuck: StuckHarness
) -> None:
    response = api.reassign(stuck.step("alpha").id, headers=_scoped(["ting:workflow:coordinate"]))

    assert response.status_code == 200, response.text


async def test_a_ting_without_the_step_engine_answers_unavailable(stuck: StuckHarness) -> None:
    app = FastAPI()
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
    app.include_router(create_run_recovery_router())
    app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: stuck.campaigns

    response = TestClient(app).post(
        f"/api/v1/ting/work/runs/{SLUG}/abort", headers=OWNER_HEADERS, json={"reason": "x"}
    )

    assert response.status_code == 503
