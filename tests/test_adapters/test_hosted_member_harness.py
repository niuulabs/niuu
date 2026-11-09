"""Asking a member's Skuld to stop or restart its harness (see skuld.harness_control)."""

from __future__ import annotations

import httpx
import pytest

from niuu.cap_telemetry import health_counts
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.hosted_member_harness import (
    RESTART_WAIT_KNOB,
    MemberHarnessControl,
)
from volundr.domain.models import Session
from volundr.domain.ports import HostedUnitError

TARGET = SessionProxyTarget(
    service_url="http://unit.ns.svc:8080/m/abc/", connect_host="unit.ns.svc", connect_port=8080
)
MEMBER = Session(name="coder-1")


def control(handler, *, wait: float = 5, poll: float = 0.001) -> MemberHarnessControl:
    return MemberHarnessControl(
        restart_wait_seconds=wait,
        poll_seconds=poll,
        request_seconds=5,
        transport=httpx.MockTransport(handler),
    )


def stale() -> httpx.Response:
    return httpx.Response(
        409, json={"detail": {"error": "credential_stale", "message": "file holds launch 'a'"}}
    )


class TestStop:
    async def test_stop_posts_to_the_members_harness_route(self):
        seen = []

        def handler(request):
            seen.append((request.method, request.url.path, request.headers["host"]))
            return httpx.Response(200, json={"status": "stopped"})

        await control(handler).stop(MEMBER, TARGET)

        assert seen == [("POST", "/m/abc/api/harness/stop", "unit.ns.svc:8080")]

    async def test_a_refusal_is_an_error_with_the_skulds_reason(self):
        def handler(request):
            return httpx.Response(
                409, json={"detail": {"error": "not_hosted_member", "message": "not a member"}}
            )

        with pytest.raises(HostedUnitError, match="refused to stop.*409: not a member"):
            await control(handler).stop(MEMBER, TARGET)

    async def test_an_unreachable_skuld_is_an_error_saying_the_process_may_still_run(self):
        def handler(request):
            raise httpx.ConnectError("no route")

        with pytest.raises(HostedUnitError, match="may still be running"):
            await control(handler).stop(MEMBER, TARGET)

    async def test_a_member_with_no_broker_target_is_an_error(self):
        with pytest.raises(HostedUnitError, match="no broker target"):
            await control(lambda r: httpx.Response(200)).stop(MEMBER, None)


class TestRestart:
    async def test_it_asks_for_the_launch_then_waits_until_the_skuld_is_ready(self):
        calls = []
        ready = iter([503, 503, 200])

        def handler(request):
            calls.append((request.method, request.url.path))
            if request.method == "POST":
                assert request.read() == b'{"launch_id":"L2"}'
                return httpx.Response(200, json={"status": "restarting"})
            code = next(ready)
            return httpx.Response(code)

        await control(handler).restart(MEMBER, TARGET, "L2")

        assert calls[0] == ("POST", "/m/abc/api/harness/restart")
        assert calls[1:] == [("GET", "/m/abc/ready")] * 3

    async def test_a_credential_the_volume_has_not_delivered_is_asked_again(self):
        answers = iter([stale(), stale(), httpx.Response(200, json={"status": "restarting"})])
        posts = []

        def handler(request):
            if request.method == "POST":
                posts.append(1)
                return next(answers)
            return httpx.Response(200)

        await control(handler).restart(MEMBER, TARGET, "L2")

        assert len(posts) == 3

    async def test_a_credential_that_never_arrives_is_a_counted_error_naming_the_knob(self):
        before = health_counts().get(f"cap_hits.{RESTART_WAIT_KNOB}", 0)

        with pytest.raises(HostedUnitError, match="did not restart within") as raised:
            await control(lambda r: stale(), wait=0.05).restart(MEMBER, TARGET, "L2")

        assert RESTART_WAIT_KNOB in str(raised.value)
        assert "the new credential did not reach it" in str(raised.value)
        assert health_counts()[f"cap_hits.{RESTART_WAIT_KNOB}"] == before + 1

    async def test_a_skuld_that_never_comes_back_ready_is_a_counted_error(self):
        def handler(request):
            if request.method == "POST":
                return httpx.Response(200, json={"status": "restarting"})
            raise httpx.ConnectError("process is being replaced")

        with pytest.raises(HostedUnitError, match="it did not come back ready"):
            await control(handler, wait=0.05).restart(MEMBER, TARGET, "L2")

    async def test_any_other_refusal_is_final(self):
        posts = []

        def handler(request):
            posts.append(1)
            return httpx.Response(
                409, json={"detail": {"error": "not_hosted_member", "message": "not a member"}}
            )

        with pytest.raises(HostedUnitError, match="refused to restart"):
            await control(handler).restart(MEMBER, TARGET, "L2")

        assert len(posts) == 1

    async def test_a_connection_failure_asking_is_an_error(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        with pytest.raises(HostedUnitError, match="could not be asked to restart"):
            await control(handler).restart(MEMBER, TARGET, "L2")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"restart_wait_seconds": 0, "poll_seconds": 1, "request_seconds": 1},
        {"restart_wait_seconds": 1, "poll_seconds": 0, "request_seconds": 1},
        {"restart_wait_seconds": 1, "poll_seconds": 1, "request_seconds": -1},
    ],
)
def test_the_bounds_must_be_positive(kwargs):
    with pytest.raises(ValueError, match="must be positive"):
        MemberHarnessControl(**kwargs)
