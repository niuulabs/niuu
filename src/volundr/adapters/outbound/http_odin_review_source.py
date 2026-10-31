"""Read resident questions from the ODIN review queue over ``niuu-ravn``'s REST API.

``niuu-ravn`` serves the queue (``GET /api/v1/ravn/odin/reviews``). This adapter calls
it with whatever credential the configured auth adapter supplies, the same
``HttpAuthPort`` adapters the other service-to-service clients use: for a Forge in
the cluster that is ``WorkloadIdentityBearerTokenAuthAdapter``, which exchanges the
pod's projected ServiceAccount token for a short-lived workload JWT that the
``niuu-ravn`` Envoy accepts. No credential is created or kept here.

Example ``notifications.odin_review_relay.source``::

    adapter: volundr.adapters.outbound.http_odin_review_source.HttpOdinReviewSource
    kwargs:
      base_url: https://yggdrasil.niuu.world
      auth_adapter: niuu.adapters.outbound.http_auth.WorkloadIdentityBearerTokenAuthAdapter
      auth_kwargs:
        exchange_url: https://yggdrasil.niuu.world/api/v1/tokens/workload/exchange
        audiences: [niuu-ravn]
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from niuu.ports.http_auth import HttpAuthPort
from niuu.utils import import_class
from volundr.domain.odin_review import OPERATOR_QUESTION_ACTION, OperatorQuestion, PendingQuestions
from volundr.domain.odin_review_ports import OdinReviewSource, OdinReviewSourceError

logger = logging.getLogger(__name__)

REVIEWS_PATH = "/api/v1/ravn/odin/reviews"

#: Items asked for per request. The queue holds hundreds of non-question items, so
#: questions are selected server-side (kind + query) and read page by page until a
#: page comes back short, which is the only thing that ends the read.
DEFAULT_PAGE_SIZE = 100

#: How long one request may take before the poll fails (and is retried next poll).
DEFAULT_TIMEOUT_SECONDS = 15.0

#: The ODIN review kind that carries a resident's operator question.
_QUESTION_KIND = "court_escalation"

_PENDING = "pending"


class HttpOdinReviewSource(OdinReviewSource):
    """Follows the review queue through ``niuu-ravn``'s REST API."""

    def __init__(
        self,
        *,
        base_url: str,
        auth_adapter: str = "niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter",
        auth_kwargs: Mapping[str, Any] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        page_size: int = DEFAULT_PAGE_SIZE,
        transport: httpx.AsyncBaseTransport | None = None,
        auth: HttpAuthPort | None = None,
    ) -> None:
        parsed = urlsplit(base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                "odin_review_relay source needs kwargs.base_url, the http(s) origin serving "
                f"{REVIEWS_PATH} (got {base_url!r})"
            )
        if parsed.username or parsed.password:
            raise ValueError("kwargs.base_url must not embed credentials; configure auth_adapter")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if page_size < 1:
            raise ValueError("page_size must be at least 1")
        self._base = base_url.strip().rstrip("/")
        self._page_size = page_size
        self._auth: HttpAuthPort = (
            auth if auth is not None else _build_auth(auth_adapter, auth_kwargs or {})
        )
        self._client = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)

    async def pending_questions(self) -> PendingQuestions:
        questions: dict[str, OperatorQuestion] = {}
        malformed: list[str] = []
        offset = 0
        while True:
            page = await self._get_json(
                REVIEWS_PATH,
                params={
                    "status": _PENDING,
                    "kind": _QUESTION_KIND,
                    "q": OPERATOR_QUESTION_ACTION,
                    "limit": self._page_size,
                    "offset": offset,
                },
            )
            if not isinstance(page, list):
                raise OdinReviewSourceError(
                    f"{self._base}{REVIEWS_PATH} answered with {type(page).__name__}, "
                    "not a list of review items; is base_url the niuu-ravn API?"
                )
            for row in page:
                self._read_row(row, questions, malformed)
            if len(page) < self._page_size:
                return PendingQuestions(questions=list(questions.values()), malformed=malformed)
            offset += self._page_size

    async def is_pending(self, review_id: str) -> bool:
        item = await self._get_json(f"{REVIEWS_PATH}/{quote(review_id, safe='')}", missing_ok=True)
        if item is None:
            return False
        if not isinstance(item, dict):
            raise OdinReviewSourceError(
                f"review item {review_id} answered with {type(item).__name__}, not an object"
            )
        return item.get("status") == _PENDING

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _read_row(
        row: object, questions: dict[str, OperatorQuestion], malformed: list[str]
    ) -> None:
        if not isinstance(row, dict) or row.get("requested_action") != OPERATOR_QUESTION_ACTION:
            return
        review_id = str(row.get("item_id") or "")
        evidence = row.get("evidence")
        asked = evidence.get("operator_question") if isinstance(evidence, dict) else None
        question = str(asked.get("question") or "").strip() if isinstance(asked, dict) else ""
        try:
            requested_at = datetime.fromisoformat(str(row.get("requested_at") or ""))
        except ValueError:
            requested_at = None
        if not review_id or not question or requested_at is None or requested_at.tzinfo is None:
            logger.error(
                "ODIN review item %r is an operator question without a readable question "
                "or timezone-aware requested_at; it is not announced",
                review_id,
            )
            malformed.append(review_id or "<no item_id>")
            return
        questions[review_id] = OperatorQuestion(
            review_id=review_id,
            valkyrie_id=str(row.get("valkyrie_id") or ""),
            environment_id=str(row.get("environment_id") or ""),
            question=question,
            requested_at=requested_at,
        )

    async def _get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        missing_ok: bool = False,
    ) -> Any:
        url = f"{self._base}{path}"
        response = await self._send(url, params)
        if response.status_code == httpx.codes.UNAUTHORIZED and self._auth.invalidate():
            # The credential may simply have expired: take a fresh one, once.
            response = await self._send(url, params)
        if response.status_code == httpx.codes.NOT_FOUND and missing_ok:
            return None
        if response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            raise OdinReviewSourceError(
                f"{url} refused the credential (HTTP {response.status_code}); check "
                "notifications.odin_review_relay.source auth_adapter/auth_kwargs and that "
                "niuu-ravn's gateway admits this workload identity"
            )
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise OdinReviewSourceError(f"{url} answered HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise OdinReviewSourceError(f"{url} did not answer JSON") from exc

    async def _send(self, url: str, params: Mapping[str, Any] | None) -> httpx.Response:
        try:
            headers = await asyncio.to_thread(self._auth.headers)
        except (httpx.HTTPError, RuntimeError, OSError, ValueError) as exc:
            # The auth adapter could not produce a credential (missing token file,
            # failed exchange): configuration, not a transient fault to wave through.
            raise OdinReviewSourceError(f"cannot authenticate to {url}: {exc}") from exc
        try:
            return await self._client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise OdinReviewSourceError(
                f"cannot reach {url} ({type(exc).__name__}); check base_url and that "
                "niuu-ravn is running"
            ) from exc


def _build_auth(adapter: str, kwargs: Mapping[str, Any]) -> HttpAuthPort:
    return import_class(adapter)(**kwargs)
