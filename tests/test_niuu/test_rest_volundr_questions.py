"""The Guild facade for LEX-1932: answers, question events and the batch read."""

from __future__ import annotations

import json

import respx
from httpx import Response

from tests.test_niuu.test_rest_volundr import _client, _headers
from tests.test_niuu.test_rest_volundr_notifications import LOCAL, REMOTE, URL, _embedded

QUESTION = "6789e341-27a2-45d8-bf03-04dadbec78b2"
FIRST = {
    "ref": f"question:remote:{QUESTION}",
    "notification_id": QUESTION,
    "state": "answered",
    "answered_by": {"device_id": "install-mac", "device_label": "Mac"},
    "at": "2026-11-05T14:05:00+00:00",
    "answer": {"content": "Release", "answers": None},
}
BODY = {
    "request_id": "answer-2",
    "device_id": "install-phone",
    "device_label": "iPhone",
    "content": "QA",
}


@respx.mock
def test_an_answer_goes_to_the_node_that_holds_the_question():
    remote = respx.post(f"http://remote/api/v1/forge/notifications/{QUESTION}/answer").mock(
        return_value=Response(200, json={"status": "delivered", "question": FIRST})
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())

    response = client.post(
        f"{URL}/{QUESTION}/answer",
        params={"instance_id": "remote"},
        json=BODY,
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {"status": "delivered", "question": FIRST, "instance_id": "remote"}
    assert json.loads(remote.calls.last.request.content) == BODY


@respx.mock
def test_a_refused_second_answer_passes_through_with_the_first():
    refusal = {
        "detail": {
            "code": "question_answered",
            "message": "This question is already answered; the first answer stands",
            "question": FIRST,
        }
    }
    respx.post(f"http://remote/api/v1/forge/notifications/{QUESTION}/answer").mock(
        return_value=Response(409, json=refusal)
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())

    response = client.post(
        f"{URL}/{QUESTION}/answer", params={"instance_id": "remote"}, json=BODY, headers=_headers()
    )

    assert response.status_code == 409
    assert response.json() == {**refusal, "instance_id": "remote"}


@respx.mock
def test_any_other_node_error_is_reported_as_an_error():
    respx.post(f"http://remote/api/v1/forge/notifications/{QUESTION}/answer").mock(
        return_value=Response(422, json={"detail": "not a question"})
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())

    response = client.post(
        f"{URL}/{QUESTION}/answer", params={"instance_id": "remote"}, json=BODY, headers=_headers()
    )

    assert response.status_code == 422
    assert "not a question" in response.text


@respx.mock
def test_question_events_are_one_nodes_and_tagged():
    events = {"items": [{"seq": 3, "ref": FIRST["ref"]}], "next_after": 3, "head_seq": 3}
    remote = respx.get("http://remote/api/v1/forge/notifications/questions/events").mock(
        return_value=Response(200, json=events)
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())

    page = client.get(
        f"{URL}/questions/events",
        params={"instance_id": "remote", "after": 2, "limit": 10},
        headers=_headers(),
    )

    assert page.status_code == 200
    assert page.json() == {**events, "instance_id": "remote"}
    assert dict(remote.calls.last.request.url.params) == {"after": "2", "limit": "10"}


@respx.mock
def test_an_older_node_without_question_events_answers_404():
    respx.get("http://remote/api/v1/forge/notifications/questions/events").mock(
        return_value=Response(404, json={"detail": "Not Found"})
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())

    page = client.get(
        f"{URL}/questions/events", params={"instance_id": "remote"}, headers=_headers()
    )

    assert page.status_code == 404


def test_the_batch_read_passes_its_ids_to_the_node():
    calls: list = []
    client = _client([LOCAL], embedded_forge_app=_embedded(calls))

    client.get(URL, params={"ids": "a,b", "limit": 5}, headers=_headers())
    client.get(URL, params={"ids": "a,b", "all_instances": "true"}, headers=_headers())

    assert [call.get("ids") for call in calls] == ["a,b", "a,b"]


@respx.mock
def test_an_undelivered_answer_passes_through_with_its_code():
    detail = {
        "code": "answer_not_delivered",
        "message": "The answer is recorded but not delivered (x); retry with the same request_id",
        "question": FIRST,
    }
    respx.post(f"http://remote/api/v1/forge/notifications/{QUESTION}/answer").mock(
        return_value=Response(502, json={"detail": detail})
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())

    response = client.post(
        f"{URL}/{QUESTION}/answer", params={"instance_id": "remote"}, json=BODY, headers=_headers()
    )

    assert response.status_code == 502
    assert response.json() == {"detail": detail, "instance_id": "remote"}
