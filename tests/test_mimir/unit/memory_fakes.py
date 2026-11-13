"""A fake gbrain for the memory gateway's tests, and helpers to build a gateway on it.

The fake follows the documented shapes of the gbrain ops the GBrainBackend calls
(``put_page`` with ``expected_revision`` / ``force`` and write receipts, ``get_page`` with
``revision`` and ``include_content``, ``add_timeline_entry``, ``get_timeline``,
``list_pages`` with ``offset`` and its 100-row remote cap, ``delete_page``, ``query``,
``get_backlinks``, ``delta`` with its keyset cursor, the memory verbs, and the source
ops ``sources_add`` / ``sources_remove``), and gbrain's owner API and OAuth token
endpoint that the backend uses to give each layer a source and a client.

It models what makes a layer a gbrain source, and refuses what gbrain refuses:

* a client is bound to one source; a write goes to that source and a read sees only it;
  a per-call ``source_id`` outside the client's grant is ``permission_denied``;
* an op needs its scope (``read``, ``write``, ``sources_admin``);
* ``sources_remove`` lets a client remove only its own source, and the client of a removed
  source is left bound to nothing, which gbrain reads as the ``default`` source;
* ``register-client`` refuses a name already in use (409 naming the client), a source that
  does not exist, and a client's secret comes back from ``recover-client``;
* the owner API needs the admin session ``POST /admin/login`` gives for the bootstrap token.

It is not a live gbrain: the real brain was not run for these tests.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from urllib.parse import parse_qs

import httpx
import jwt
import yaml

from mimir.adapters.gbrain_layers import source_id_of
from mimir.backends.gbrain import GBrainBackend
from mimir.memory.models import SessionMemoryContext
from mimir.ports.deployment import (
    DeploymentRequest,
    KnowledgeDeploymentPort,
    MemoryProvisioningError,
    MemorySystemPhase,
    MemorySystemRecord,
    memory_system_name,
)
from mimir.ports.session_directory import SessionDirectory, SessionNotConfirmedError

BASE = "https://brain.test"
MCP = f"{BASE}/mcp"
ADMIN_TOKEN = "admin-bootstrap-test-token-0123456789"
SIGNING_KEY = "memory-test-key"
SENTINEL = "<!-- timeline -->"
_EPOCH = datetime(2026, 11, 1, tzinfo=UTC)
_SOURCE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
_READ = frozenset(
    {
        "query",
        "get_page",
        "list_pages",
        "get_timeline",
        "get_backlinks",
        "recall",
        "entity",
        "delta",
    }
)
_WRITE = frozenset({"put_page", "delete_page", "add_timeline_entry", "remember", "forget"})
_SOURCE_ADMIN = frozenset({"sources_add", "sources_remove"})


class FakeGBrain:
    """One org's brain: sources of pages with revisions and timelines, facts, clients."""

    def __init__(self, base: str = BASE, admin_token: str = ADMIN_TOKEN) -> None:
        self.base = base
        self.admin_token = admin_token
        self.sources: dict[str, dict[str, Any]] = {
            "default": {"id": "default", "name": "default", "pages": {}}
        }
        self.clients: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, str] = {}
        self.admin_sessions: set[str] = set()
        self.facts: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.calls_by_client: list[tuple[str, str, dict[str, Any]]] = []
        self.requests: list[tuple[str, str]] = []
        self.token_requests = 0
        self.token_ttl = 3600
        self.list_cap = 100
        self.recall_cap = 100
        self.delta_fetch = 3
        self.search_limit = 20
        self.fail_with: int | None = None
        self.fail_tools: set[str] = set()
        self.fail_paths: set[str] = set()
        self.refuse_source_add = False
        self.lose_journal = False
        self.canonical: dict[str, str] = {}
        self.cards: dict[tuple[str, str], dict[str, Any]] = {}
        self._tick = 0
        self._serial = 0

    # -- plumbing --------------------------------------------------------

    def mount(self, router: Any) -> None:
        """Route every request to this brain's server root to the fake."""
        router.route(url__startswith=self.base).mock(side_effect=self.handle)

    def now(self) -> str:
        self._tick += 1
        return (_EPOCH + timedelta(seconds=self._tick)).isoformat()

    def _next(self) -> int:
        self._serial += 1
        return self._serial

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.fail_with:
            return httpx.Response(self.fail_with, text="boom")
        path = request.url.path
        self.requests.append((request.method, path))
        if path in self.fail_paths:
            return httpx.Response(500, json={"error": "service_unavailable"})
        if path == "/mcp":
            return self._mcp(request)
        if path == "/token":
            return self._token(request)
        if path == "/admin/login":
            return self._login(request)
        if path.startswith("/admin/api/"):
            return self._admin(request)
        return httpx.Response(404, text="no such route")

    # -- the MCP endpoint ------------------------------------------------

    def _mcp(self, request: httpx.Request) -> httpx.Response:
        client = self._authenticated(request.headers.get("authorization", ""))
        if client is None:
            return httpx.Response(401, json={"error": "invalid_token"})
        body = json.loads(request.content)
        name, args = body["params"]["name"], body["params"]["arguments"]
        self.calls.append((name, args))
        self.calls_by_client.append((client["name"], name, args))
        if name in self.fail_tools:
            payload: Any = {"error": "internal", "message": f"{name} broke", "protocol_version": 1}
        else:
            payload = self._dispatch(client, name, args)
        result: dict[str, Any] = {"content": [{"type": "text", "text": json.dumps(payload)}]}
        if isinstance(payload, dict) and "error" in payload:
            result["isError"] = True
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    def _authenticated(self, header: str) -> dict[str, Any] | None:
        token = header.removeprefix("Bearer ").strip()
        client = self.clients.get(self.tokens.get(token, ""))
        if client is None or client["revoked"]:
            return None
        return client

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {"error": code, "message": message, "suggestion": "fix it", "protocol_version": 1}

    def _dispatch(self, client: dict[str, Any], name: str, args: dict[str, Any]) -> Any:
        scope = (
            "read"
            if name in _READ
            else "write"
            if name in _WRITE
            else "sources_admin"
            if name in _SOURCE_ADMIN
            else None
        )
        if scope is None:
            return self._error("unknown_operation", f"no such tool: {name}")
        if scope not in client["scopes"]:
            return self._error("permission_denied", f"Tool requires {scope} scope")
        # A client whose source was removed is bound to nothing, which reads as `default`.
        bound = client["source_id"] or "default"
        requested = args.get("source_id")
        if requested is not None and requested != bound:
            return self._error(
                "permission_denied", "Requested source is outside your granted sources"
            )
        if name in _SOURCE_ADMIN:
            return getattr(self, f"_{name}")(client, bound, args)
        if bound not in self.sources:
            return self._error("unknown_source", f"source '{bound}' does not exist")
        return getattr(self, f"_{name}")(bound, args)

    def _pages(self, source_id: str) -> dict[str, dict[str, Any]]:
        return self.sources[source_id]["pages"]

    def _live(self, source_id: str, slug: str) -> dict[str, Any] | None:
        page = self._pages(source_id).get(slug)
        return None if page is None or page["deleted"] else page

    # -- sources ---------------------------------------------------------

    def ensure_source(self, namespace: str) -> str:
        """The source of a layer, made as the operator would have (not through a client)."""
        source_id = source_id_of(namespace)
        self.sources.setdefault(source_id, {"id": source_id, "name": namespace, "pages": {}})
        return source_id

    def _sources_add(self, client: dict[str, Any], bound: str, args: dict[str, Any]) -> Any:
        if self.refuse_source_add:
            return self._error(
                "writer_coordinator_required", "Managed source lifecycle requires the owner CLI."
            )
        source_id = args["id"]
        if not _SOURCE_ID.fullmatch(source_id):
            return self._error("invalid_params", f"Invalid source_id: {source_id!r}")
        if source_id in self.sources:
            return self._error("collision", f"Source {source_id} already exists")
        self.sources[source_id] = {
            "id": source_id,
            "name": args.get("name") or source_id,
            "pages": {},
        }
        return {"id": source_id, "name": self.sources[source_id]["name"]}

    def _sources_remove(self, client: dict[str, Any], bound: str, args: dict[str, Any]) -> Any:
        source_id = args["id"]
        if source_id != bound or source_id not in self.sources:
            return self._error("not_found", f"Unknown source: {source_id}")
        if source_id == "default":
            return self._error("protected_id", "Cannot remove the default source")
        pages = [p for p in self._pages(source_id).values() if not p["deleted"]]
        if pages and not args.get("confirm_destructive"):
            return self._error("protected_id", f"Refusing to remove {source_id} with data")
        del self.sources[source_id]
        for other in self.clients.values():
            if other["source_id"] == source_id:
                other["source_id"] = None  # ON DELETE SET NULL
        return {"id": source_id, "pages_deleted": len(pages), "dryRun": False}

    # -- pages -----------------------------------------------------------

    def seed_page(self, key: str, compiled_truth: str, **front: Any) -> dict[str, Any]:
        """A page written straight into gbrain (``<namespace>/<slug>``), not through the gateway."""
        namespace, _, slug = key.partition("/")
        source_id = self.ensure_source(namespace)
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
        self._pages(source_id)[slug] = page
        return page

    @property
    def pages(self) -> dict[str, dict[str, Any]]:
        """Every page, keyed ``<layer namespace>/<slug in its source>``."""
        return {
            f"{source['name']}/{slug}": page
            for source in self.sources.values()
            for slug, page in source["pages"].items()
        }

    def page(self, namespace: str, slug: str) -> dict[str, Any] | None:
        source = self.sources.get(source_id_of(namespace))
        return None if source is None else source["pages"].get(slug)

    def _put_page(self, source_id: str, args: dict[str, Any]) -> dict[str, Any]:
        slug = args["slug"]
        existing = self._live(source_id, slug)
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
        self._pages(source_id)[slug] = page
        return {"request_id": "r", "state": "committed", "revision": str(page["revision"])}

    def _serialized(self, page: dict[str, Any]) -> str:
        header = yaml.safe_dump(page["frontmatter"], sort_keys=False)
        rows = "\n".join(f"- **{r['date']}** | {r['summary']}" for r in page["timeline"])
        return f"---\n{header}---\n\n{page['compiled_truth']}\n\n{SENTINEL}\n{rows}\n"

    def _get_page(self, source_id: str, args: dict[str, Any]) -> dict[str, Any]:
        page = self._live(source_id, args["slug"])
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

    def _add_timeline_entry(self, source_id: str, args: dict[str, Any]) -> dict[str, Any]:
        page = self._live(source_id, args["slug"])
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

    def _get_timeline(self, source_id: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        page = self._live(source_id, args["slug"])
        rows = page["timeline"] if page else []
        return rows[: args.get("limit", len(rows))]

    def _list_pages(self, source_id: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        live = sorted(
            (p for p in self._pages(source_id).values() if not p["deleted"]),
            key=lambda p: p["slug"],
        )
        offset = args.get("offset", 0)
        limit = min(args.get("limit", 50), self.list_cap)
        return [
            {
                "slug": p["slug"],
                "title": p["title"],
                "type": p["type"],
                "updated_at": p["updated_at"],
                "source_id": source_id,
            }
            for p in live[offset : offset + limit]
        ]

    def _delete_page(self, source_id: str, args: dict[str, Any]) -> dict[str, Any]:
        page = self._live(source_id, args["slug"])
        if page is None:
            return self._error("page_not_found", args["slug"])
        page["deleted"] = True
        return {"request_id": "r", "state": "committed"}

    def _query(self, source_id: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        words = [w.lower() for w in args["query"].split()]
        live = sorted(
            (p for p in self._pages(source_id).values() if not p["deleted"]),
            key=lambda p: p["slug"],
        )
        hits = [
            p
            for p in live
            if any(w in (p["compiled_truth"] + " " + p["title"]).lower() for w in words)
        ]
        offset = args.get("offset", 0)
        limit = args.get("limit") or self.search_limit
        return [
            {
                "slug": p["slug"],
                "title": p["title"],
                "chunk_text": p["compiled_truth"][:80],
                "source_id": source_id,
            }
            for p in hits[offset : offset + limit]
        ]

    def _get_backlinks(self, source_id: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        target = args["slug"]
        return [
            {"from_slug": p["slug"], "to_slug": target, "title": p["title"]}
            for p in self._pages(source_id).values()
            if not p["deleted"] and f"[[{target}]]" in p["compiled_truth"]
        ]

    def _delta(self, source_id: str, args: dict[str, Any]) -> dict[str, Any]:
        since, after = args["since"], args.get("since_slug", "")
        changed = sorted(
            (
                p
                for p in self._pages(source_id).values()
                if not p["deleted"]
                and (p["updated_at"] > since or (p["updated_at"] == since and p["slug"] > after))
            ),
            key=lambda p: (p["updated_at"], p["slug"]),
        )
        batch = changed[: self.delta_fetch]
        last = batch[-1] if batch else None
        facts = [
            self._fact_row(f)
            for f in self.facts
            if f["created_at"] > since and f["source_id"] == source_id
        ]
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

    def seed_fact(
        self,
        fact: str,
        *,
        entity: str,
        provenance: str,
        layer: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """A fact written straight into gbrain, into *layer*'s source (``default`` if None)."""
        source_id = self.ensure_source(layer) if layer else "default"
        row = {
            "id": self._next(),
            "source_id": source_id,
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

    def _remember(self, source_id: str, args: dict[str, Any]) -> dict[str, Any]:
        entity = self.canonical.get(args["entity"], args["entity"])
        for row in self.facts:
            if (
                row["source_id"] == source_id
                and row["entity_slug"] == entity
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
            stored["source_id"] = source_id
            status = "inserted"
        return {
            "id": str(stored["id"]),
            "status": status,
            "entity_slug": entity,
            "valid_until": stored["valid_until"],
            "protocol_version": 1,
        }

    def _recall(self, source_id: str, args: dict[str, Any]) -> dict[str, Any]:
        rows = [
            r
            for r in self.facts
            if r["source_id"] == source_id
            and r["entity_slug"] == args.get("entity", r["entity_slug"])
        ]
        if args.get("supersessions"):
            rows = [r for r in rows if r["superseded_by"] is not None]
        elif not args.get("include_expired"):
            rows = [r for r in rows if r["expired_at"] is None]
        if args.get("grep"):
            rows = [r for r in rows if args["grep"].lower() in r["fact"].lower()]
        rows = rows[: min(args["limit"], self.recall_cap)]
        facts = [self._fact_row(r) for r in rows]
        return {"facts": facts, "total": len(facts), "protocol_version": 1}

    def _forget(self, source_id: str, args: dict[str, Any]) -> dict[str, Any]:
        row = next(
            (r for r in self.facts if str(r["id"]) == args["id"] and r["source_id"] == source_id),
            None,
        )
        if row is None:
            return self._error("not_found", "no such fact")
        already = row["expired_at"] is not None
        if not already:
            row["expired_at"] = self.now()
            row["context"] = args.get("reason")
        return {"id": args["id"], "expired": not already, "reason": args.get("reason")}

    def set_card(self, namespace: str, name: str, card: dict[str, Any]) -> None:
        """The entity card gbrain answers for *name* in *namespace*'s source."""
        self.cards[(source_id_of(namespace), name)] = card

    def _entity(self, source_id: str, args: dict[str, Any]) -> dict[str, Any]:
        name = args["name"]
        if (source_id, name) in self.cards:
            return {"found": True, "card": self.cards[(source_id, name)], "protocol_version": 1}
        known = any(
            r["entity_slug"] == name and r["source_id"] == source_id for r in self.facts
        ) or self._live(source_id, name)
        if not known:
            return {"found": False, "protocol_version": 1}
        card = {"entity": {"slug": name, "title": name, "type": "entity"}, "aka": [], "edges": []}
        return {"found": True, "card": card, "protocol_version": 1}

    # -- the OAuth token endpoint ----------------------------------------

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        client = self.clients.get(form.get("client_id", ""))
        if (
            form.get("grant_type") != "client_credentials"
            or client is None
            or client["revoked"]
            or client["secret"] != form.get("client_secret")
        ):
            return httpx.Response(401, json={"error": "invalid_client"})
        self.token_requests += 1
        token = f"gbrain_at_{self._next()}"
        self.tokens[token] = client["client_id"]
        return httpx.Response(
            200,
            json={
                "access_token": token,
                "token_type": "bearer",
                "expires_in": self.token_ttl,
                "scope": " ".join(client["scopes"]),
            },
        )

    # -- the owner API ---------------------------------------------------

    def _login(self, request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("token") != self.admin_token:
            return httpx.Response(401, json={"error": "Owner credential rejected."})
        session = f"session-{self._next()}"
        self.admin_sessions.add(session)
        return httpx.Response(
            200,
            json={"status": "authenticated"},
            headers={"Set-Cookie": f"gbrain_admin={session}; Path=/admin; HttpOnly"},
        )

    def _admin(self, request: httpx.Request) -> httpx.Response:
        cookie = request.headers.get("cookie", "")
        if cookie.removeprefix("gbrain_admin=") not in self.admin_sessions:
            return httpx.Response(401, json={"error": "Admin authentication required"})
        path = request.url.path.removeprefix("/admin/api/")
        body = json.loads(request.content) if request.content else {}
        if path == "sources":
            return httpx.Response(
                200,
                json=[
                    {"id": s["id"], "name": s["name"], "federated": False}
                    for s in self.sources.values()
                ],
            )
        if path == "clients":
            return httpx.Response(
                200, json={"clients": [self._client_json(c) for c in self.clients.values()]}
            )
        if path.startswith("clients/"):
            found = self.clients.get(path.removeprefix("clients/"))
            if found is None:
                return httpx.Response(404, json={"error": "client_not_found"})
            return httpx.Response(
                200,
                json={
                    "client": self._client_json(found),
                    "grant": self._client_json(found)["grant"],
                },
            )
        handler = {
            "register-client": self._register_client,
            "recover-client": self._recover_client,
            "revoke-client": self._revoke_client,
        }.get(path)
        if handler is None:
            return httpx.Response(404, json={"error": "no such route"})
        return handler(body)

    @staticmethod
    def _client_json(client: dict[str, Any]) -> dict[str, Any]:
        return {
            "client_id": client["client_id"],
            "client_name": client["name"],
            "status": "revoked" if client["revoked"] else "active",
            "grant": {
                "clientId": client["client_id"],
                "scopes": client["scopes"],
                "sourceId": client["source_id"],
                "revoked": client["revoked"],
            },
        }

    def _register_client(self, body: dict[str, Any]) -> httpx.Response:
        name = body["name"]
        for client in self.clients.values():
            if client["name"] == name and not client["revoked"]:
                return httpx.Response(
                    409, json={"error": "duplicate_name", "client_id": client["client_id"]}
                )
        source_id = body.get("source", "default")
        if source_id not in self.sources:
            return httpx.Response(400, json={"error": "unknown_source", "message": source_id})
        client_id = f"gbrain_cl_{self._next()}"
        self.clients[client_id] = {
            "client_id": client_id,
            "name": name,
            "secret": f"gbrain_cs_{self._next()}",
            "scopes": body["scopes"].split(),
            "source_id": source_id,
            "revoked": False,
            "grant_types": body["grantTypes"],
            "auth_method": body["tokenEndpointAuthMethod"],
        }
        return httpx.Response(
            200,
            json={
                "clientId": client_id,
                "clientSecret": self.clients[client_id]["secret"],
                "status": "registered",
            },
        )

    def _recover_client(self, body: dict[str, Any]) -> httpx.Response:
        client = self.clients.get(body["clientId"])
        if client is None or client["revoked"]:
            return httpx.Response(404, json={"error": "client_not_found"})
        if self.lose_journal:
            return httpx.Response(
                409, json={"error": "client_secret_delivery_unavailable", "message": "no journal"}
            )
        return httpx.Response(
            200,
            json={"clientId": client["client_id"], "clientSecret": client["secret"]},
        )

    def _revoke_client(self, body: dict[str, Any]) -> httpx.Response:
        client = self.clients.get(body["clientId"])
        if client is not None:
            client["revoked"] = True
            self.tokens = {t: c for t, c in self.tokens.items() if c != client["client_id"]}
        return httpx.Response(200, json={"revoked": True})

    # -- test levers -----------------------------------------------------

    def expire_admin_sessions(self) -> None:
        """The brain restarted: every admin session is gone."""
        self.admin_sessions.clear()

    def live_clients(self) -> list[dict[str, Any]]:
        return [c for c in self.clients.values() if not c["revoked"]]


def gbrain_backend(mcp_url: str = MCP, **kwargs: Any) -> GBrainBackend:
    return GBrainBackend(mcp_url, admin_token=ADMIN_TOKEN, signing_key=SIGNING_KEY, **kwargs)


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


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


class FakeMemoryDeployment(KnowledgeDeploymentPort):
    """A deployment target that provisions memory systems in memory.

    Built by config (``memory.provisioning.deployment``) too: each instance registers
    under its ``instance`` name so a test reaches the one the app built.
    """

    provisions_memory_systems = True
    instances: ClassVar[dict[str, FakeMemoryDeployment]] = {}

    def __init__(self, *, instance: str = "default", mcp_url: str = MCP) -> None:
        self.mcp_url = mcp_url
        self.records: dict[str, MemorySystemRecord] = {}
        self.provision_calls: list[str] = []
        self.read_calls: list[str] = []
        self.created = 0
        self.error = ""
        self.gate: asyncio.Event | None = None
        self.closed = False
        FakeMemoryDeployment.instances[instance] = self

    async def list_deployments(self, *, tenant_id: str = "") -> dict[str, Any]:
        return {"releases": []}

    async def deploy(self, request: DeploymentRequest) -> dict[str, Any]:
        raise NotImplementedError

    async def provision_memory_system(self, tenant_id: str) -> MemorySystemRecord:
        self.provision_calls.append(tenant_id)
        if self.gate is not None:
            await self.gate.wait()
        if self.error:
            raise MemoryProvisioningError(self.error)
        if tenant_id not in self.records:
            self.created += 1
            self.records[tenant_id] = MemorySystemRecord(
                tenant_id=tenant_id,
                name=memory_system_name(tenant_id),
                phase=MemorySystemPhase.PREPARING,
                message="installing",
            )
            return replace(self.records[tenant_id], started=True)
        return self.records[tenant_id]

    async def memory_system(self, tenant_id: str) -> MemorySystemRecord | None:
        self.read_calls.append(tenant_id)
        if self.error:
            raise MemoryProvisioningError(self.error)
        return self.records.get(tenant_id)

    async def memory_systems(self) -> list[MemorySystemRecord]:
        if self.error:
            raise MemoryProvisioningError(self.error)
        return list(self.records.values())

    def make_ready(self, tenant_id: str, *, name: str = "") -> None:
        self.records[tenant_id] = MemorySystemRecord(
            tenant_id=tenant_id,
            name=name or memory_system_name(tenant_id),
            phase=MemorySystemPhase.READY,
            message="ready",
            mcp_url=self.mcp_url,
            admin_token=ADMIN_TOKEN,
            signing_key=SIGNING_KEY,
        )

    def make_failed(self, tenant_id: str, message: str) -> None:
        self.records[tenant_id] = MemorySystemRecord(
            tenant_id=tenant_id,
            name=memory_system_name(tenant_id),
            phase=MemorySystemPhase.FAILED,
            message=message,
        )

    async def close(self) -> None:
        self.closed = True
