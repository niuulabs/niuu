"""The HTTP source that reads resident questions from niuu-ravn's review API."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from niuu.ports.http_auth import HttpAuthPort
from volundr.adapters.outbound.http_odin_review_source import (
    REVIEWS_PATH,
    HttpOdinReviewSource,
)
from volundr.domain.odin_review_ports import OdinReviewSourceError

BASE = "https://ravn.example"
REQUESTED = "2026-10-27T10:00:00+00:00"


def _row(review_id: str = "review:court_escalation:operator:a1", **overrides) -> dict:
    row = {
        "item_id": review_id,
        "kind": "court_escalation",
        "requested_action": "answer_operator_question",
        "environment_id": "ymir",
        "valkyrie_id": "muninn",
        "status": "pending",
        "requested_at": REQUESTED,
        "evidence": {"operator_question": {"case_id": "c1", "question": " Which window? "}},
    }
    row.update(overrides)
    return row


class Auth(HttpAuthPort):
    def __init__(self, *, refreshable: bool = False) -> None:
        self.tokens = ["t1", "t2"]
        self.refreshable = refreshable
        self.invalidated = 0

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[min(self.invalidated, 1)]}"}

    def invalidate(self) -> bool:
        self.invalidated += 1
        return self.refreshable


def _source(handler, *, auth: HttpAuthPort | None = None, page_size: int = 100):
    return HttpOdinReviewSource(
        base_url=BASE,
        transport=httpx.MockTransport(handler),
        auth=auth if auth is not None else Auth(),
        page_size=page_size,
    )


async def test_reads_only_pending_questions_with_the_auth_headers():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[_row()])

    source = _source(handler)
    result = await source.pending_questions()
    await source.close()

    [request] = seen
    assert request.url.path == REVIEWS_PATH
    assert dict(request.url.params) == {
        "status": "pending",
        "kind": "court_escalation",
        "q": "answer_operator_question",
        "limit": "100",
        "offset": "0",
    }
    assert request.headers["authorization"] == "Bearer t1"
    [question] = result.questions
    assert (question.review_id, question.valkyrie_id, question.environment_id) == (
        "review:court_escalation:operator:a1",
        "muninn",
        "ymir",
    )
    assert question.question == "Which window?"
    assert question.requested_at == datetime(2026, 10, 27, 10, tzinfo=UTC)
    assert result.malformed == []


async def test_pages_until_a_short_page_and_dedupes():
    pages = {
        "0": [_row("r1"), _row("r2")],
        "2": [_row("r2"), _row("r3")],
        "4": [_row("r4")],
    }
    offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = request.url.params["offset"]
        offsets.append(offset)
        return httpx.Response(200, json=pages[offset])

    result = await _source(handler, page_size=2).pending_questions()

    assert offsets == ["0", "2", "4"]
    assert [q.review_id for q in result.questions] == ["r1", "r2", "r3", "r4"]


async def test_other_actions_are_ignored_and_unreadable_questions_are_reported(caplog):
    rows = [
        _row("brief", requested_action="acknowledge"),
        _row("no-question", evidence={}),
        _row("no-time", requested_at="soon"),
        _row("naive-time", requested_at="2026-10-27T10:00:00"),
        _row("fine"),
    ]
    caplog.set_level("ERROR")

    result = await _source(lambda request: httpx.Response(200, json=rows)).pending_questions()

    assert [q.review_id for q in result.questions] == ["fine"]
    assert result.malformed == ["no-question", "no-time", "naive-time"]
    assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 3


async def test_a_401_with_a_refreshable_credential_retries_once():
    auth = Auth(refreshable=True)
    tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tokens.append(request.headers["authorization"])
        return httpx.Response(401) if len(tokens) == 1 else httpx.Response(200, json=[])

    result = await _source(handler, auth=auth).pending_questions()

    assert tokens == ["Bearer t1", "Bearer t2"]
    assert result.questions == []


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refused_credential_names_the_remedy(status):
    with pytest.raises(OdinReviewSourceError, match="auth_adapter"):
        await _source(lambda request: httpx.Response(status)).pending_questions()


async def test_server_errors_and_bad_bodies_raise():
    with pytest.raises(OdinReviewSourceError, match="HTTP 503"):
        await _source(lambda request: httpx.Response(503)).pending_questions()
    with pytest.raises(OdinReviewSourceError, match="not a list"):
        await _source(lambda request: httpx.Response(200, json={"items": []})).pending_questions()
    with pytest.raises(OdinReviewSourceError, match="JSON"):
        await _source(lambda request: httpx.Response(200, content=b"<html>")).pending_questions()


async def test_an_unreachable_queue_raises_with_the_remedy():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(OdinReviewSourceError, match="cannot reach .*base_url"):
        await _source(handler).pending_questions()


async def test_a_credential_that_cannot_be_made_raises_instead_of_sending_unsigned():
    class Broken(Auth):
        def headers(self) -> dict[str, str]:
            raise RuntimeError("workload identity proof token not found")

    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=[])

    with pytest.raises(OdinReviewSourceError, match="cannot authenticate.*proof token"):
        await _source(handler, auth=Broken()).pending_questions()
    assert called is False


async def test_is_pending_reads_the_item_itself():
    statuses = {"r-open": "pending", "r-done": "applied"}
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.raw_path.decode())
        review_id = request.url.path.rsplit("/", 1)[1]
        if review_id not in statuses:
            return httpx.Response(404, json={"detail": "Review item not found"})
        return httpx.Response(200, json={"item_id": review_id, "status": statuses[review_id]})

    source = _source(handler)

    assert await source.is_pending("r-open") is True
    assert await source.is_pending("r-done") is False
    assert await source.is_pending("r-gone") is False
    assert requested[0] == f"{REVIEWS_PATH}/r-open"


async def test_the_review_id_is_url_quoted():
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"status": "pending"})

    await _source(handler).is_pending("review:court_escalation:operator:a/b")

    assert requested == [f"{REVIEWS_PATH}/review%3Acourt_escalation%3Aoperator%3Aa%2Fb"]


async def test_is_pending_other_errors_still_raise():
    with pytest.raises(OdinReviewSourceError, match="HTTP 500"):
        await _source(lambda request: httpx.Response(500)).is_pending("r1")
    with pytest.raises(OdinReviewSourceError, match="not an object"):
        await _source(lambda request: httpx.Response(200, json=[])).is_pending("r1")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"base_url": ""}, "base_url"),
        ({"base_url": "niuu-ravn:8080"}, "base_url"),
        ({"base_url": "https://user:pw@ravn.example"}, "credentials"),
        ({"base_url": BASE, "timeout_seconds": 0}, "timeout_seconds"),
        ({"base_url": BASE, "page_size": 0}, "page_size"),
    ],
)
def test_bad_configuration_is_refused_at_construction(kwargs, message):
    with pytest.raises(ValueError, match=message):
        HttpOdinReviewSource(**kwargs)


def test_the_auth_adapter_is_selected_dynamically_from_config():
    source = HttpOdinReviewSource(
        base_url=BASE,
        auth_adapter="niuu.adapters.outbound.http_auth.StaticBearerTokenAuthAdapter",
        auth_kwargs={"token": "abc"},
    )

    assert source._auth.headers() == {"Authorization": "Bearer abc"}


def test_the_default_auth_sends_no_credential():
    assert HttpOdinReviewSource(base_url=BASE)._auth.headers() == {}


def test_a_missing_auth_adapter_class_fails_at_construction():
    with pytest.raises(ModuleNotFoundError):
        HttpOdinReviewSource(base_url=BASE, auth_adapter="nowhere.Missing")
