"""A fake gbrain for the memory gateway's tests, and helpers to build a gateway on it.

The fake follows the documented shapes of the gbrain ops the GBrainBackend calls
(``put_page`` with ``expected_revision`` / ``force`` and write receipts,
``get_page`` with ``revision`` and ``include_content``, ``add_timeline_entry``,
``get_timeline``, ``list_pages`` with ``offset`` and its 100-row remote cap,
``delete_page``, ``query``, ``get_backlinks``, ``delta`` with its keyset cursor,
and the memory verbs). It is not a live gbrain: the real brain was not run for
these tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import yaml

from mimir.backends.gbrain import GBrainBackend
from mimir.memory.models import SessionMemoryContext
from mimir.ports.session_directory import SessionDirectory, SessionNotConfirmedError

MCP = "https://brain.test/mcp"
SIGNING_KEY = "memory-test-key"
SENTINEL = "<!-- timeline -->"
_EPOCH = datetime(2026, 11, 1, tzinfo=UTC)


class FakeGBrain:
    """One org's brain: pages with revisions and timelines, facts, a clock."""

    def __init__(self) -> None:
        self.pages: dict[str, dict[str, Any]] = {}
        self.facts: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.list_cap = 100
        self.recall_cap = 100
        self.delta_fetch = 3
        self.fail_with: int | None = None
        self.fail_tools: set[str] = set()
        self.canonical: dict[str, str] = {}
        self.cards: dict[str, dict[str, Any]] = {}
        self._tick = 0

    # -- plumbing --------------------------------------------------------

    def now(self) -> str:
        self._tick += 1
        return (_EPOCH + timedelta(seconds=self._tick)).isoformat()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.fail_with:
            return httpx.Response(self.fail_with, text="boom")
        body = json.loads(request.content)
        name, args = body["params"]["name"], body["params"]["arguments"]
        self.calls.append((name, args))
        if name in self.fail_tools:
            payload: Any = {"error": "internal", "message": f"{name} broke", "protocol_version": 1}
        else:
            payload = getattr(self, f"_{name}")(args)
        result: dict[str, Any] = {"content": [{"type": "text", "text": json.dumps(payload)}]}
        if isinstance(payload, dict) and "error" in payload:
            result["isError"] = True
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {"error": code, "message": message, "suggestion": "fix it", "protocol_version": 1}

    def _live(self, slug: str) -> dict[str, Any] | None:
        page = self.pages.get(slug)
        return None if page is None or page["deleted"] else page

    # -- pages -----------------------------------------------------------

    def seed_page(self, slug: str, compiled_truth: str, **front: Any) -> dict[str, Any]:
        """A page written straight into gbrain, not through the gateway."""
        page = {
            "slug": slug,
            "title": front.pop("title", slug),
            "type": front.get("type", "note"),
            "frontmatter": front,
            "compiled_truth": compiled_truth,
            "timeline": [],
            "revision": 1,
            "updated_at": self.now(),
            "deleted": False,
        }
        self.pages[slug] = page
        return page

    def _put_page(self, args: dict[str, Any]) -> dict[str, Any]:
        slug = args["slug"]
        existing = self._live(slug)
        expected = args.get("expected_revision")
        if existing is not None and not args.get("force"):
            if expected is None:
                return self._error("revision_required", f"{slug} exists; pass expected_revision")
            if str(existing["revision"]) != str(expected):
                return self._error("revision_conflict", f"{slug} moved on")
        content: str = args["content"]
        front: dict[str, Any] = {}
        body = content
        if content.startswith("---\n"):
            _, header, body = content.split("---", 2)
            front = yaml.safe_load(header) or {}
        compiled = body.split(SENTINEL, 1)[0].strip()
        page = existing or {"timeline": [], "revision": 0}
        page.update(
            slug=slug,
            title=front.get("title", slug),
            type=front.get("type", "note"),
            frontmatter=front,
            compiled_truth=compiled,
            revision=page["revision"] + 1,
            updated_at=self.now(),
            deleted=False,
        )
        self.pages[slug] = page
        return {"request_id": "r", "state": "committed", "revision": str(page["revision"])}

    def _serialized(self, page: dict[str, Any]) -> str:
        header = yaml.safe_dump(page["frontmatter"], sort_keys=False)
        rows = "\n".join(f"- **{r['date']}** | {r['summary']}" for r in page["timeline"])
        return f"---\n{header}---\n\n{page['compiled_truth']}\n\n{SENTINEL}\n{rows}\n"

    def _get_page(self, args: dict[str, Any]) -> dict[str, Any]:
        page = self._live(args["slug"])
        if page is None:
            return self._error("page_not_found", f"Page not found: {args['slug']}")
        answer = {
            "slug": page["slug"],
            "title": page["title"],
            "type": page["type"],
            "frontmatter": page["frontmatter"],
            "compiled_truth": page["compiled_truth"],
            "timeline": "\n".join(r["summary"] for r in page["timeline"]),
            "revision": str(page["revision"]),
            "updated_at": page["updated_at"],
        }
        if args.get("include_content"):
            answer["content"] = self._serialized(page)
        return answer

    def _add_timeline_entry(self, args: dict[str, Any]) -> dict[str, Any]:
        page = self._live(args["slug"])
        if page is None:
            return self._error("page_not_found", f"Page not found: {args['slug']}")
        page["timeline"].append(
            {
                "date": args["date"],
                "summary": args["summary"],
                "detail": args.get("detail", ""),
                "source": args.get("source"),
                "created_at": self.now(),
            }
        )
        page["revision"] += 1
        page["updated_at"] = self.now()
        return {"request_id": "r", "state": "committed", "revision": str(page["revision"])}

    def _get_timeline(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        page = self._live(args["slug"])
        rows = page["timeline"] if page else []
        return rows[: args.get("limit", len(rows))]

    def _list_pages(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        live = sorted((p for p in self.pages.values() if not p["deleted"]), key=lambda p: p["slug"])
        offset = args.get("offset", 0)
        limit = min(args.get("limit", 50), self.list_cap)
        return [
            {
                "slug": p["slug"],
                "title": p["title"],
                "type": p["type"],
                "updated_at": p["updated_at"],
            }
            for p in live[offset : offset + limit]
        ]

    def _delete_page(self, args: dict[str, Any]) -> dict[str, Any]:
        page = self._live(args["slug"])
        if page is None:
            return self._error("page_not_found", args["slug"])
        page["deleted"] = True
        return {"request_id": "r", "state": "committed"}

    def _query(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        words = [w.lower() for w in args["query"].split()]
        live = sorted((p for p in self.pages.values() if not p["deleted"]), key=lambda p: p["slug"])
        hits = [
            p
            for p in live
            if any(w in (p["compiled_truth"] + " " + p["title"]).lower() for w in words)
        ]
        offset = args.get("offset", 0)
        limit = min(args.get("limit", 20), self.list_cap)
        return [
            {"slug": p["slug"], "title": p["title"], "chunk_text": p["compiled_truth"][:80]}
            for p in hits[offset : offset + limit]
        ]

    def _get_backlinks(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        target = args["slug"]
        return [
            {"from_slug": p["slug"], "to_slug": target, "title": p["title"]}
            for p in self.pages.values()
            if not p["deleted"] and f"[[{target}]]" in p["compiled_truth"]
        ]

    def _delta(self, args: dict[str, Any]) -> dict[str, Any]:
        since, after = args["since"], args.get("since_slug", "")
        changed = sorted(
            (
                p
                for p in self.pages.values()
                if not p["deleted"]
                and (p["updated_at"] > since or (p["updated_at"] == since and p["slug"] > after))
            ),
            key=lambda p: (p["updated_at"], p["slug"]),
        )
        batch = changed[: self.delta_fetch]
        last = batch[-1] if batch else None
        facts = [self._fact_row(f) for f in self.facts if f["created_at"] > since]
        return {
            "protocol_version": 1,
            "since": since,
            "pages": [
                {"slug": p["slug"], "title": p["title"], "updated_at": p["updated_at"]}
                for p in batch
            ],
            "facts": facts,
            "threads": [],
            "has_more": len(changed) > len(batch),
            "next_cursor": {
                "since": last["updated_at"] if last else since,
                "slug": last["slug"] if last else after,
            },
        }

    # -- facts -----------------------------------------------------------

    def _fact_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {**row, "fact_id": str(row["id"]), "provenance": row["source"]}

    def seed_fact(self, fact: str, *, entity: str, provenance: str, **extra: Any) -> dict[str, Any]:
        row = {
            "id": len(self.facts) + 1,
            "fact": fact,
            "kind": "fact",
            "entity_slug": entity,
            "source": provenance,
            "valid_from": _EPOCH.isoformat(),
            "valid_until": None,
            "expired_at": None,
            "superseded_by": None,
            "context": None,
            "created_at": self.now(),
            **extra,
        }
        self.facts.append(row)
        return row

    def _remember(self, args: dict[str, Any]) -> dict[str, Any]:
        entity = self.canonical.get(args["entity"], args["entity"])
        for row in self.facts:
            if (
                row["entity_slug"] == entity
                and row["fact"] == args["fact"]
                and not row["expired_at"]
            ):
                status, stored = "duplicate", row
                break
        else:
            stored = self.seed_fact(
                args["fact"],
                entity=entity,
                provenance=args["provenance"],
                kind=args.get("kind", "fact"),
                valid_until=args.get("ttl"),
            )
            status = "inserted"
        return {
            "id": str(stored["id"]),
            "status": status,
            "entity_slug": entity,
            "valid_until": stored["valid_until"],
            "protocol_version": 1,
        }

    def _recall(self, args: dict[str, Any]) -> dict[str, Any]:
        rows = [r for r in self.facts if r["entity_slug"] == args.get("entity", r["entity_slug"])]
        if args.get("supersessions"):
            rows = [r for r in rows if r["superseded_by"] is not None]
        elif not args.get("include_expired"):
            rows = [r for r in rows if r["expired_at"] is None]
        if args.get("grep"):
            rows = [r for r in rows if args["grep"].lower() in r["fact"].lower()]
        rows = rows[: min(args["limit"], self.recall_cap)]
        facts = [self._fact_row(r) for r in rows]
        return {"facts": facts, "total": len(facts), "protocol_version": 1}

    def _forget(self, args: dict[str, Any]) -> dict[str, Any]:
        row = next((r for r in self.facts if str(r["id"]) == args["id"]), None)
        if row is None:
            return self._error("not_found", "no such fact")
        already = row["expired_at"] is not None
        if not already:
            row["expired_at"] = self.now()
            row["context"] = args.get("reason")
        return {"id": args["id"], "expired": not already, "reason": args.get("reason")}

    def _entity(self, args: dict[str, Any]) -> dict[str, Any]:
        name = args["name"]
        if name in self.cards:
            return {"found": True, "card": self.cards[name], "protocol_version": 1}
        known = any(r["entity_slug"] == name for r in self.facts) or self._live(name)
        if not known:
            return {"found": False, "protocol_version": 1}
        card = {"entity": {"slug": name, "title": name, "type": "entity"}, "aka": [], "edges": []}
        return {"found": True, "card": card, "protocol_version": 1}


def gbrain_backend(**kwargs: Any) -> GBrainBackend:
    return GBrainBackend(MCP, "gbrain_tok", signing_key=SIGNING_KEY, **kwargs)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

SESSION_ID = "5e551011-0000-4000-8000-000000000001"
OTHER_SESSION_ID = "5e551011-0000-4000-8000-000000000002"
PROJECT_ID = "9a0e0000-0000-4000-8000-0000000000aa"
TENANT = "acme"


def context(
    session_id: str = SESSION_ID,
    *,
    project_id: str | None = PROJECT_ID,
    prefixes: tuple[str, ...] = ("decisions/", "runbooks/"),
    tenant: str = TENANT,
    owner: str = "ines",
) -> SessionMemoryContext:
    return SessionMemoryContext(
        session_id=session_id,
        launch_id="launch-1",
        owner_id=owner,
        tenant_id=tenant,
        project_id=project_id,
        workflow_run_id=None,
        project_write_prefixes=prefixes if project_id else (),
    )


def session_token(session_id: str = SESSION_ID, **claims: Any) -> str:
    """A forge_session-shaped JWT. Its signature is Forge's business, not the gateway's."""
    body = {
        "sub": "ines",
        "token_use": "forge_session",
        "workload_session_id": session_id,
        "scopes": ["forge:memory"],
        **claims,
    }
    return jwt.encode(body, "not-the-gateways-key-to-check-0123456789", algorithm="HS256")


class FakeDirectory(SessionDirectory):
    """Forge's memory-context answers, by session id."""

    def __init__(self, *contexts: SessionMemoryContext) -> None:
        self.contexts = {c.session_id: c for c in contexts}
        self.asked: list[tuple[str, str]] = []

    async def memory_context(self, session_id: str, credential: str) -> SessionMemoryContext:
        self.asked.append((session_id, credential))
        found = self.contexts.get(session_id)
        if found is None:
            raise SessionNotConfirmedError(f"Forge answered 404 for session {session_id}")
        return found
