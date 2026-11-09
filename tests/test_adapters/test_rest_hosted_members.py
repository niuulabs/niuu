"""REST: a run's members are created hosted by its unit root and start and stop with it."""

from __future__ import annotations

from uuid import UUID, uuid4

from tests.support.forge_session import PREFIX, ForgeApp, build_forge_app
from volundr.domain.models import SessionStatus

RUN = str(uuid4())


def _root_body(name: str = "wf-run") -> dict:
    return {
        "name": name,
        "hosts_members": True,
        "workflow_run_id": RUN,
        "workflow_run_room": True,
    }


def _member_body(root_id: str, name: str, member: str = "coder") -> dict:
    return {
        "name": name,
        "hosted_by": root_id,
        "workflow_run_id": RUN,
        "workflow_member": member,
        "definition": "skuldClaude",
    }


async def _create(forge: ForgeApp, body: dict):
    # TestClient is synchronous; the app and the repository share the test's event loop.
    return forge.client.post(f"{PREFIX}/sessions", json=body)


async def _unit(forge: ForgeApp, members: int = 2) -> tuple[str, list[str]]:
    root = (await _create(forge, _root_body())).json()
    ids = []
    for index in range(members):
        response = await _create(forge, _member_body(root["id"], f"member-{index}", f"m{index}"))
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    return root["id"], ids


async def _run(forge: ForgeApp, root_id: str) -> None:
    task = forge.session_service._provisioning_tasks.get(UUID(root_id))
    if task is not None:
        await task
    await forge.set_status(UUID(root_id), SessionStatus.RUNNING)


async def test_create_holds_the_root_and_its_members_without_starting_them():
    forge = build_forge_app()

    root_id, member_ids = await _unit(forge)

    assert forge.client.get(f"{PREFIX}/sessions/{root_id}").json()["status"] == "created"
    for member_id in member_ids:
        assert forge.client.get(f"{PREFIX}/sessions/{member_id}").json()["status"] == "created"
    assert forge.pod_manager.start_calls == []


async def test_a_member_is_refused_with_409_once_the_unit_has_started():
    forge = build_forge_app()
    root_id, member_ids = await _unit(forge, members=1)
    started = forge.client.post(f"{PREFIX}/sessions/{root_id}/start")
    assert started.status_code == 200, started.text

    late = await _create(forge, _member_body(root_id, "late-member", "late"))

    assert late.status_code == 409
    assert "fixed when its unit starts" in late.json()["detail"]
    names = [s.name for s in await forge.sessions.list()]
    assert "late-member" not in names
    assert len(await forge.sessions.list_hosted(UUID(root_id))) == len(member_ids)


async def test_a_member_of_a_session_that_is_not_a_unit_is_refused_with_409():
    forge = build_forge_app()
    plain = forge.client.post(f"{PREFIX}/sessions", json={"name": "plain"}).json()

    refused = await _create(forge, _member_body(plain["id"], "member-1"))

    assert refused.status_code == 409
    assert "not a unit" in refused.json()["detail"]


async def test_a_member_of_a_unit_that_does_not_exist_is_refused_with_404():
    forge = build_forge_app()

    refused = await _create(forge, _member_body(str(uuid4()), "member-1"))

    assert refused.status_code == 404
    assert "does not exist" in refused.json()["detail"]


async def test_a_member_cannot_name_a_unit_and_be_a_unit():
    forge = build_forge_app()
    root_id, _ = await _unit(forge, members=0)

    body = {**_member_body(root_id, "member-1"), "hosts_members": True}

    assert (await _create(forge, body)).status_code == 422


async def test_starting_the_root_starts_the_unit_with_all_members_and_one_pod():
    forge = build_forge_app()
    root_id, member_ids = await _unit(forge)

    started = forge.client.post(f"{PREFIX}/sessions/{root_id}/start")
    await _run(forge, root_id)

    assert started.status_code == 200
    assert [str(s.id) for s, _ in forge.pod_manager.start_calls] == [root_id]
    for member_id in member_ids:
        member = forge.client.get(f"{PREFIX}/sessions/{member_id}").json()
        assert member["status"] == "running"


async def test_a_members_endpoint_is_the_one_its_runtime_names():
    forge = build_forge_app()
    root_id, member_ids = await _unit(forge)
    forge.client.post(f"{PREFIX}/sessions/{root_id}/start")
    await _run(forge, root_id)
    forge.pod_manager.initial_chat_endpoint = lambda s: (
        f"wss://gw.example.com/s/{s.id}/session" if s.hosted_by else None
    )

    root = forge.client.get(f"{PREFIX}/sessions/{root_id}").json()
    endpoints = [
        forge.client.get(f"{PREFIX}/sessions/{member_id}").json()["chat_endpoint"]
        for member_id in member_ids
    ]

    assert root["chat_endpoint"] == "wss://chat.example.com/session"
    assert endpoints == [f"wss://gw.example.com/s/{member_id}/session" for member_id in member_ids]


async def test_a_members_status_follows_the_unit_and_a_listing_agrees():
    forge = build_forge_app()
    root_id, member_ids = await _unit(forge, members=1)
    forge.client.post(f"{PREFIX}/sessions/{root_id}/start")
    await _run(forge, root_id)
    await forge.set_status(UUID(root_id), SessionStatus.FAILED)

    member = forge.client.get(f"{PREFIX}/sessions/{member_ids[0]}").json()
    listed = {s["id"]: s for s in forge.client.get(f"{PREFIX}/sessions").json()}

    assert member["status"] == "failed"
    assert listed[member_ids[0]]["status"] == "failed"


async def test_stopping_the_root_stops_the_unit_and_every_member():
    forge = build_forge_app()
    root_id, member_ids = await _unit(forge)
    forge.client.post(f"{PREFIX}/sessions/{root_id}/start")
    await _run(forge, root_id)

    stopped = forge.client.post(f"{PREFIX}/sessions/{root_id}/stop")

    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert [str(s.id) for s in forge.pod_manager.stop_calls] == [root_id]
    for member_id in member_ids:
        member = forge.client.get(f"{PREFIX}/sessions/{member_id}").json()
        assert member["status"] == "stopped"
        assert member["chat_endpoint"] is None


async def test_stopping_one_member_leaves_the_unit_and_the_other_member_running():
    forge = build_forge_app()
    root_id, (first, second) = await _unit(forge)
    forge.client.post(f"{PREFIX}/sessions/{root_id}/start")
    await _run(forge, root_id)

    stopped = forge.client.post(f"{PREFIX}/sessions/{first}/stop")

    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert forge.client.get(f"{PREFIX}/sessions/{root_id}").json()["status"] == "running"
    assert forge.client.get(f"{PREFIX}/sessions/{second}").json()["status"] == "running"
    assert forge.client.post(f"{PREFIX}/sessions/{first}/stop").status_code == 409


async def test_a_member_cannot_be_started_before_its_unit():
    forge = build_forge_app()
    _, (member_id,) = await _unit(forge, members=1)

    refused = forge.client.post(f"{PREFIX}/sessions/{member_id}/start")

    assert refused.status_code == 409
    assert "start the unit" in refused.json()["detail"]


async def test_each_member_keeps_its_own_credential_and_launch():
    forge = build_forge_app()
    root_id, (first, second) = await _unit(forge)
    forge.client.post(f"{PREFIX}/sessions/{root_id}/start")
    await _run(forge, root_id)

    credentials = {
        sid: await forge.session_service.issue_forge_session(UUID(sid))
        for sid in (root_id, first, second)
    }

    assert all(credentials.values())
    assert {forge.tokens.verify(c.token).session_id for c in credentials.values()} == {
        root_id,
        first,
        second,
    }
    assert len({c.launch_id for c in credentials.values()}) == 3


async def test_a_unit_with_members_cannot_be_deleted_until_they_are():
    forge = build_forge_app()
    root_id, (member_id,) = await _unit(forge, members=1)

    refused = forge.client.delete(f"{PREFIX}/sessions/{root_id}")

    assert refused.status_code == 409
    assert "delete the members first" in refused.json()["detail"]
    assert forge.client.delete(f"{PREFIX}/sessions/{member_id}").status_code in (200, 204)
    assert forge.client.delete(f"{PREFIX}/sessions/{root_id}").status_code in (200, 204)
