"""cp-model reference semantics (LEX-1387, round 8).

Pure, dependency-free reference implementations of the model contracts, so their acceptance cases run:
  - identity.md   : entity / occurrence keys
  - questions.md  : questions (agents' requests to Damien) and session questions (native prompts):
                    resolution, visibility, escalation every 24 h
  - observed.md   : acknowledgement, since/recent window, one headline per project
  - bursts.md     : burst identity and membership (shared with Slice 0 / F1n)

These are references, not the production reducers. The owning lanes (S0/C1 keys, C3a asks, C2v
Activities, F1n bursts) bind their implementations to the same case files. Times are integer minutes.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

# ───────────────────────────── identity.md ─────────────────────────────

def _h(*parts, n=32):
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:n]


def dispatch_session_uuid(owner, tenant, dispatch_id):
    """Forge's own derivation (N/src/volundr/domain/services/projects.py:378-390): no host in it."""
    scope = json.dumps([owner, tenant], separators=(",", ":"))
    key = uuid.uuid5(uuid.NAMESPACE_URL, f"forge-dispatch:{scope}:{dispatch_id}")
    return str(uuid.uuid5(key, "session"))


def session_key(host, sid):
    return f"session:{host}:{sid}"


def resolve_session(sid, index, host=None):
    """index: {uuid: [hosts]}. By UUID alone only when exactly one host has it."""
    hosts = index.get(sid, [])
    if host is not None:
        return {"status": 200, "key": session_key(host, sid)} if host in hosts else {"status": 404}
    if len(hosts) == 1:
        return {"status": 200, "key": session_key(hosts[0], sid)}
    if not hosts:
        return {"status": 404}
    return {"status": 409, "candidates": sorted(session_key(h, sid) for h in hosts)}


def resolve_parent(recording_host, parent, host_map, index):
    """host_map: {recording_host: {instance_id_or_slug: host}}. Unknown → None (No coordinator)."""
    host = host_map.get(recording_host, {}).get(parent["instance_id"])
    if host is not None:
        return session_key(host, parent["session_id"])
    r = resolve_session(parent["session_id"], index)
    return r["key"] if r["status"] == 200 else None


def legacy_feature_id(repo, slug, title, steps):
    """Pre-R9 fragments (no minted feature_id): the same slug with the same title and steps is one feature."""
    if steps is None:
        return None  # unverified import: no identity claimed, occurrence kept with steps_source=unverified
    body = json.dumps({"title": title, "test": steps}, sort_keys=True, separators=(",", ":"))
    return f"legacy:{repo}:{slug}:{hashlib.sha256(body.encode()).hexdigest()[:12]}"


def feature_id(fragment, repo="lexi-ios"):
    """R9 on: the id minted by `just fragment` is canonical. Before: legacy content id."""
    if fragment.get("feature_id"):
        return fragment["feature_id"]
    return legacy_feature_id(repo, fragment["slug"], fragment["title"], fragment.get("test"))


def step_id(fid, index, text):
    return f"{fid}#{index}:{hashlib.sha256(text.encode()).hexdigest()[:8]}"


def event_id(occurrence):
    """occurrence: a list of source coordinates that includes a sequence, history id, version or attempt."""
    return "evt_" + _h(*occurrence)


# ───────────────────────────── questions.md ─────────────────────────────

ASK_TITLE = re.compile(r"(your decision|decisions? for you|\bpick\b|\bapprove\b|\?\s*$)", re.I)
ESCALATE_MIN = 24 * 60   # Damien, plan/06: unanswered never leaves the badge; it escalates every 24 h


class Asks:
    """Reference reducer for questions.md. Feed steps with .apply(step); read with .view().

    Two entities: a Question (an agent's request to Damien, from a notice) and a Session question (a native
    AskUserQuestion or permission prompt, answered in the session). Nothing hides on a timer; an open item
    escalates once per full 24 h it stays unanswered (event key: (item key, "escalation", n))."""

    def __init__(self):
        self.now = 0
        self.asks = {}            # key -> dict
        self.declaring = set()    # (host, session) that ever sent the `ask` field
        self.ended = set()        # (host, session) stopped/archived/failed
        self.errors = []
        self.escalations = {}     # event id -> {item, n, at}   (history: every level, R7-5)
        self.item_level = {}      # item key -> highest level delivered for THAT item (R8-3: identity per item, not per row)
        self.last_sent = {}       # row key -> time of the last successful delivery (R8-3: at most one per row per 24 h)
        self.deliveries = []      # [{row, level, event_id}]: what was actually sent

    # keys
    @staticmethod
    def ntf(host, nid):
        return f"question:{host}:{nid}"

    @staticmethod
    def ctl(host, session, rid):
        return f"sessionq:{host}:{session}:{rid}"

    def _open(self, key, **kw):
        a = dict(key=key, resolution="open", closed_by=None, rev=1, group=key, members=[key], **kw)
        a.setdefault("last_at", a["opened_at"])
        self.asks[key] = a
        return a

    def _resolve(self, key, resolution, closed_by):
        a = self.asks.get(key)
        if a and a["resolution"] == "open":
            a["resolution"], a["closed_by"] = resolution, closed_by
            for m in a["members"]:
                if m != key:
                    self._resolve(m, resolution, closed_by)

    def _escalate(self):
        """Idempotent: one escalation event per item per completed 24 h, keyed (item, "escalation", n)."""
        for k, a in self.asks.items():
            if a["resolution"] != "open" or a["confidence"] != "confirmed" or a["group"] != k:
                continue
            for n in range(1, (self.now - a["opened_at"]) // ESCALATE_MIN + 1):
                eid = event_id([k, "escalation", n])
                self.escalations.setdefault(eid, {"item": k, "n": n, "at": a["opened_at"] + n * ESCALATE_MIN})
        # Delivery (R7-5, R8-3): per visible row, only the representative item's CURRENT due level; identity is the item
        # (a Question, or the waiting session's oldest open request), so a new question in the same session gets its own
        # escalation; cadence is per row: at most one delivery per 24 h since the last successful one (no boundary burst).
        for row, item, opened in self._rows():
            level = (self.now - opened) // ESCALATE_MIN
            if level < 1 or level <= self.item_level.get(item, 0):
                continue
            last = self.last_sent.get(row)
            if last is not None and self.now - last < ESCALATE_MIN:
                continue
            self.item_level[item] = level
            self.last_sent[row] = self.now
            self.deliveries.append({"row": row, "item": item, "level": level, "event_id": event_id([item, "escalation", level])})

    def _rows(self):
        """Counted Needs-you rows: one per Question group, one per waiting session (its oldest open prompt)."""
        rows = []
        waiting = {}
        for k, a in self.asks.items():
            if a["resolution"] != "open" or a["confidence"] != "confirmed":
                continue
            if a["kind"] in ("question", "permission"):
                w = waiting.get(a["session"])
                if w is None or a["opened_at"] < w[2]:
                    waiting[a["session"]] = (f"session:{a['session'][0]}:{a['session'][1]}", k, a["opened_at"])
            elif a["group"] == k:
                rows.append((k, k, a["opened_at"]))
        return rows + list(waiting.values())

    def apply(self, s):
        t = s["t"]
        self.now = max(self.now, s.get("at", self.now))
        try:
            self._apply(s, t)
        finally:
            self._escalate()

    def _apply(self, s, t):
        if t == "clock":
            return
        host = s.get("host", "thor")
        if t == "notice":
            sess = (host, s.get("session", "s"))
            key = self.ntf(host, s["id"])
            if "ask" in s:
                self.declaring.add(sess)
            about = s.get("about_ask")
            if about:
                # Wire form (judge R8 ruling): about_question.id = "question:<host>:<uuid>"; {host, id} is only internal
                if str(about["id"]).startswith("question:"):
                    _, qhost, qid = about["id"].split(":", 2)
                    about = dict(about, host=qhost, id=qid)
                target = self.ntf(about.get("host", host), about["id"])   # R7-2: host-qualified target
                act = about["action"]
                tgt = self.asks.get(target)
                if act != "relay" and (tgt is None or tgt["session"] != sess):
                    # R7-2: only the asking session may remind, replace, resolve or withdraw its question
                    self.errors.append({"notice": s["id"], "status": "rejected",
                                        "reason": "unknown_question" if tgt is None else "not_asking_session"})
                    return
                if act == "remind" and tgt:
                    tgt["rev"] += 1
                    tgt["last_at"] = self.now
                elif act == "resolved":
                    self._resolve(target, "answered", "about_question_resolved")
                elif act == "withdrawn":
                    self._resolve(target, "withdrawn", "about_question_withdrawn")
                elif act == "relay" and tgt and s.get("project") and tgt.get("project") and s["project"] != tgt["project"]:
                    self.errors.append({"notice": s["id"], "status": "rejected", "reason": "relay_other_project"})
                    return
                elif act == "relay" and tgt:
                    m = self._open(key, kind="agent", confidence="confirmed", session=sess, opened_at=self.now,
                                   title=s.get("title", ""))
                    m["group"] = tgt["group"]
                    self.asks[tgt["group"]]["members"].append(key)
                    return
                elif act == "replace" and tgt:
                    self._resolve(target, "withdrawn", "replaced")
                    n = self._open(key, kind="agent", confidence="confirmed", session=sess, opened_at=self.now,
                                   title=s.get("title", ""))
                    n["replaces"] = target
                    return
                if act in ("remind", "resolved", "withdrawn") and not s.get("ask"):
                    return
            if s.get("ask") is True:
                q = self._open(key, kind="agent", confidence="confirmed", session=sess, opened_at=self.now,
                               title=s.get("title", ""))
                q["project"] = s.get("project")
            elif "ask" not in s and sess not in self.declaring:
                kind, src, title = s.get("kind"), s.get("source", "agent"), s.get("title", "")
                if kind == "decision" or (kind == "attention" and src == "agent") or \
                        (kind == "milestone" and ASK_TITLE.search(title)):
                    self._open(key, kind="agent", confidence="inferred", session=sess, opened_at=self.now,
                               title=title)
        elif t == "log":
            sess = (host, s["session"])
            key = self.ctl(host, s["session"], s["request_id"])
            k = s["kind"]
            if k == "ask_user_question":
                self._open(key, kind="question", confidence="confirmed", session=sess, opened_at=self.now)
            elif k == "claude_permission_request":
                self._open(key, kind="permission", confidence="confirmed", session=sess, opened_at=self.now)
            elif k == "ask_user_resolved":
                reason = s.get("reason", "answered")
                res = {"answered": "answered", "completed_elsewhere": "answered_elsewhere",
                       "turn_ended": "cancelled", "transport_closed": "cancelled"}.get(reason)
                if res:
                    self._resolve(key, res, reason)
            elif k == "permission_resolved":
                self._resolve(key, "cancelled" if s.get("behavior") == "cancel" else "answered", "permission_resolved")
        elif t == "cp_answer":
            if s.get("delivered"):
                self._resolve(s["ask"], "answered", "cp_answer_delivered")
        elif t == "dismiss":
            a = self.asks.get(s["ask"])
            if a and a["kind"] != "agent":
                self.errors.append({"ask": s["ask"], "status": 409})
            else:
                self._resolve(s["ask"], "dismissed", "dismissed")
        elif t == "snapshot":
            if s.get("status") in ("stopped", "archived", "failed"):
                self.ended.add((host, s["session"]))
        elif t == "turn":
            pass  # sender unknown (G6): never resolves anything
        elif t == "restart":
            pass  # the delivery guard and escalation history are persisted: nothing is re-sent
        else:
            raise ValueError(f"unknown step {t}")

    def visibility(self, a):
        """Only confidence hides (a legacy notice that merely looks like a question). Never time, never session end."""
        if a["resolution"] != "open":
            return None, None
        if a["confidence"] == "inferred":
            return "hidden", "unconfirmed"
        return "counted", None

    def view(self):
        out = {"asks": {}, "badge": 0, "rows": 0, "possibly": [], "members": {}, "errors": self.errors,
               "escalations": sorted([e["item"], e["n"]] for e in self.escalations.values()),
               "deliveries": [[d["row"], d["level"]] for d in self.deliveries],
               "delivered_items": [[d["item"], d["level"]] for d in self.deliveries], "waiting_sessions": []}
        for k, a in self.asks.items():
            vis, reason = self.visibility(a)
            out["asks"][k] = {"resolution": a["resolution"], "closed_by": a["closed_by"], "visibility": vis,
                              "hidden_reason": reason, "confidence": a["confidence"], "rev": a["rev"],
                              "entity": "session_question" if a["kind"] in ("question", "permission") else "question",
                              "session_ended": a["session"] in self.ended,
                              "escalation_level": (self.now - a["opened_at"]) // ESCALATE_MIN
                              if vis == "counted" else 0}
            if a["group"] != k:
                continue  # relay members are drawn inside their group's row
            if len(a["members"]) > 1:
                out["members"][k] = list(a["members"])
            if vis == "counted" and out["asks"][k]["entity"] == "question":
                out["badge"] += 1
                out["rows"] += 1
            elif reason == "unconfirmed":
                out["possibly"].append(k)
        # R7-2: a waiting session is one row and counts once, however many prompts it has open
        ws = sorted({f"session:{a['session'][0]}:{a['session'][1]}" for a in self.asks.values()
                     if a["resolution"] == "open" and a["kind"] in ("question", "permission")})
        out["waiting_sessions"] = ws
        out["badge"] += len(ws)
        out["rows"] += len(ws)
        return out


# ───────────────────────────── bursts.md ─────────────────────────────

WINDOW = 30
LIVE_LAG = 10


class Bursts:
    """Reference for bursts.md: Forge-assigned, persisted ids; membership keyed by (host, provider_key, origin)."""

    def __init__(self):
        self.bursts = {}   # id -> {host, key, origin, start, last, pubs:[...]}
        self.seq = 0

    def _mint(self):
        self.seq += 1
        return f"burst-{self.seq}"

    def publish(self, p):
        """p: {id, host, artifact, at, ingested_at, session}. Returns the burst id it joined."""
        origin = "live" if p["ingested_at"] - p["at"] <= LIVE_LAG else "backfill"
        cands = [(bid, b) for bid, b in self.bursts.items()
                 if b["host"] == p["host"] and b["key"] == p["artifact"] and b["origin"] == origin
                 and b["start"] - WINDOW <= p["at"] <= b["last"] + WINDOW]
        if cands:
            bid, b = max(cands, key=lambda kv: (kv[1]["last"], kv[0]))  # the most recent; never merge two
            b["start"], b["last"] = min(b["start"], p["at"]), max(b["last"], p["at"])
        else:
            bid = self._mint()
            b = self.bursts[bid] = {"host": p["host"], "key": p["artifact"], "origin": origin,
                                    "start": p["at"], "last": p["at"], "pubs": []}
        b["pubs"].append(p["id"])
        return bid

    def announced_by(self, bid, notices):
        """An agent notice (any session) linking the artifact within [start-30, last+30]. Slice 0's own never count."""
        b = self.bursts[bid]
        for n in notices:
            if n.get("source") != "agent" or str(n.get("correlation_id", "")).startswith("art-burst:"):
                continue
            if b["key"] in n.get("links", []) and b["start"] - WINDOW <= n["at"] <= b["last"] + WINDOW:
                return n["id"]
        return None


# ───────────────────────────── observed.md ─────────────────────────────

RANK = {"release": 0, "build_new": 1, "problem": 2, "milestone": 3, "to_check": 4, "build": 5}
LATEST_N = 5         # headlines; plan/06: "recent" is count-based, never an hours window
RECENT_ARTIFACTS = 10


class Ledger:
    """Reference for observed.md: one monotonic ledger seq; per-view acks by server-issued token."""

    def __init__(self):
        self.seq = 0
        self.occ = []          # {seq, kind, project, occurred_at, key}
        self.seen_keys = set()
        self.acks = {}         # view -> {seq, displayed_at}
        self.issued = set()    # (view, seq)
        self.bursts = Bursts()
        self.pubs = {}         # burst id -> {project, last_seq}
        self.notices = []
        self.burst_q = {}      # burst id -> qualifying occurrence already emitted?
        self.last = None

    def _add(self, kind, project, occurred_at, key):
        if key in self.seen_keys:
            return
        self.seen_keys.add(key)
        self.seq += 1
        self.occ.append({"seq": self.seq, "kind": kind, "project": project, "occurred_at": occurred_at, "key": key})

    def _burst_check(self, bid):
        if self.burst_q.get(bid):
            return
        nid = self.bursts.announced_by(bid, self.notices)
        if nid:
            self.burst_q[bid] = True
            self._add("to_check", self.pubs[bid]["project"], self.bursts.bursts[bid]["last"], f"burst:{bid}")

    def apply(self, s):
        t = s["t"]
        if t == "ingest":
            k = s["kind"]
            if k == "ship":  # first successful attempt per build qualifies
                kind = "build_new" if s.get("new_feature") else "build"
                self._add(kind, s["project"], s["occurred_at"], f"build:{s['build']}")
            elif k == "ship_failed":
                self._add("problem", s["project"], s["occurred_at"], f"build-failed:{s['build']}:{s.get('attempt', 1)}")
            elif k == "release":
                if s.get("channel") in ("release", "pre-release-named"):
                    self._add("release", s["project"], s["occurred_at"], f"release:{s['name']}")
            elif k == "session_error":
                self._add("problem", s["project"], s["occurred_at"], f"err:{s['session']}:{s['occurred_at']}")
            elif k == "milestone":
                # R7-6 key milestone: a milestone notice from a coordinator of the project carrying a deliverable link
                if s.get("role") == "coordinator" and s.get("links"):
                    self._add("milestone", s["project"], s["occurred_at"], f"ms:{s['notice']}")
            elif k == "problem_reported":
                self._add("problem", s["project"], s["occurred_at"], f"prob:{s['notice']}")
            elif k == "publication":
                bid = self.bursts.publish({"id": s["id"], "host": s.get("host", "thor"), "artifact": s["artifact"],
                                           "at": s["occurred_at"], "ingested_at": s.get("ingested_at", s["occurred_at"]),
                                           "session": s.get("session")})
                self.pubs.setdefault(bid, {"project": s["project"]})
                self._burst_check(bid)
            elif k == "notice":
                self.notices.append({"id": s["id"], "source": s.get("source", "agent"), "links": s.get("links", []),
                                     "at": s["occurred_at"], "correlation_id": s.get("correlation_id")})
                for bid in list(self.bursts.bursts):
                    if bid in self.pubs:
                        self._burst_check(bid)
            else:
                raise ValueError(k)
            return None
        if t == "read":
            view, now = s.get("view", "activities"), s["now"]
            ack = self.acks.get(view)
            q = [o for o in self.occ]
            since = [o for o in q if ack and o["seq"] > ack["seq"]]
            if since:
                mode, sel = "since", since
            else:
                mode, sel = "latest", q   # the newest qualifying item per project, whatever its age
            best = {}
            for o in sel:
                cur = best.get(o["project"])
                if mode == "since":   # what mattered most since the ack: importance first, then recency
                    better = cur is None or (RANK[o["kind"]], -o["seq"]) < (RANK[cur["kind"]], -cur["seq"])
                else:                 # latest (R7-6): the newest qualifying occurrence per project, whatever its kind
                    better = cur is None or o["seq"] > cur["seq"]
                if better:
                    best[o["project"]] = o
            ranked = sorted(best.values(), key=lambda o: (RANK[o["kind"]], -o["seq"]))
            resp = {"as_of_seq": self.seq, "mode": mode,
                    "headlines": [[o["project"], o["kind"]] for o in ranked[:5]], "more": max(0, len(ranked) - 5),
                    "ack_token": {"view": view, "seq": self.seq}}
            self.issued.add((view, self.seq))
            self.last = resp
            return resp
        if t == "ack":
            view = s.get("view", "activities")
            tok = s.get("token", "last")
            if tok == "last":
                tok = self.last["ack_token"]
            if tok.get("view") != view or (tok["view"], tok["seq"]) not in self.issued:
                return {"status": 400}
            cur = self.acks.get(view)
            if cur is None or tok["seq"] > cur["seq"]:
                self.acks[view] = {"seq": tok["seq"], "displayed_at": s.get("displayed_at")}
            return {"status": 200, "ack_seq": self.acks[view]["seq"]}
        raise ValueError(t)


def recent_artifacts(artifacts, n=RECENT_ARTIFACTS):
    """plan/06: the last 10 artifacts by newest publication, whatever their age."""
    return [a["key"] for a in sorted(artifacts, key=lambda a: (a["published_at"], a["key"]), reverse=True)[:n]]


# ───────────────────────────── ownership.md (D13, R7-3) ─────────────────────────────

UI_BUCKET = {  # ForgeKit's lifecycle bucket (UiBucket.swift rawStatusToBucket); unknown → errored
    "created": "active", "starting": "active", "provisioning": "active", "running": "active", "idle": "active",
    "ready": "active", "stopping": "stopped", "stopped": "stopped", "terminated": "stopped",
    "error": "errored", "failed": "errored", "crashed": "errored", "errored": "errored", "archived": "archived"}


def ui_bucket(status):
    return UI_BUCKET.get(str(status).lower(), "errored")


def eligible(c, project):
    """One definition for every writer and reader (R7-3, round 12): a coordinator of the project whose lifecycle bucket
    is active or stopped. Errored (failed, crashed, errored, or any unknown status) and archived are not eligible.
    A stopped coordinator stays eligible (it is idle, not gone)."""
    return c["project"] == project and c["role"] == "coordinator" and not c.get("archived") \
        and ui_bucket(c.get("status", "running")) in ("active", "stopped")


def primary_coordinator(project, sessions, config=None):
    """CP config wins when it names an eligible coordinator; else the earliest-created eligible one; ties by session key."""
    cfg = (config or {}).get(project)
    coords = [c for c in sessions if eligible(c, project)]
    if cfg and any(c["key"] == cfg for c in coords):
        return cfg
    if not coords:
        return None
    return min(coords, key=lambda c: (c["created"], c["key"]))["key"]


def effective_owner(s, sessions, config=None):
    """Returns (owner, source): source ∈ recorded | ancestry | fallback | coordinator | no_coordinator.
    Walks the parent chain (Forge MCP records a session's caller, so a worker's child points at the worker) up to the
    first eligible coordinator of the session's project; otherwise the primary."""
    if s["role"] == "coordinator":
        return None, "coordinator"
    by = {x["key"]: x for x in sessions}
    cur, hops, seen = s.get("parent"), 0, set()
    while cur and cur in by and cur not in seen and hops < 32:
        seen.add(cur)
        node = by[cur]
        if eligible(node, s["project"]):
            return cur, ("recorded" if hops == 0 else "ancestry")
        cur, hops = node.get("parent"), hops + 1
    p = primary_coordinator(s["project"], sessions, config)
    return (p, "fallback") if p else (None, "no_coordinator")


def project_groups(project, sessions, config=None):
    """Coordinator groups in card order (most recent activity first); zero coordinators → one headerless group."""
    coords = sorted([c for c in sessions if eligible(c, project)], key=lambda c: (-c.get("activity", 0), c["key"]))
    members = [x for x in sessions if x["project"] == project and x["role"] not in ("coordinator", "side_quest")
               and not x.get("archived")]
    if not coords:
        return [{"coordinator": None, "sessions": sorted(m["key"] for m in members)}]
    return [{"coordinator": c["key"],
             "sessions": sorted(m["key"] for m in members if effective_owner(m, sessions, config)[0] == c["key"])}
            for c in coords]


def attach(session, project, sessions, config=None, owner=None, as_role=None):
    """The Projects-sheet attach (U5): the ONE Forge PUT and its Undo. CP/U5 validate the owner; Forge does not
    check role, and accepts a foreign parent without validating it (R7-3)."""
    has = [c for c in sessions if eligible(c, project)]
    undo = {"project_id": session.get("project"), "role": session.get("role"), "parent": session.get("parent")}
    if as_role == "coordinator":
        return {"payload": {"project_id": project, "role": "coordinator", "parent": None}, "undo": undo}
    if not has:
        return {"payload": {"project_id": project, "role": "worker", "parent": None}, "undo": undo}
    owner = owner or primary_coordinator(project, sessions, config)
    if not any(c["key"] == owner for c in has):
        return {"error": 422, "reason": "owner_not_eligible_coordinator"}
    return {"payload": {"project_id": project, "role": "worker", "parent": owner}, "undo": undo}


def backfill(sessions, evidence=None, config=None, conflicts=()):
    """G1/B1 (dry run + apply share this): for filed, non-coordinator sessions whose chain reaches no eligible
    coordinator, write parent = exact launch evidence (a coordinator session key named in the brief) if eligible,
    else the primary. Zero-coordinator projects are skipped. A Forge 409 (conflicts) is retried next run."""
    writes, skipped = [], []
    for x in sorted(sessions, key=lambda y: y["key"]):
        if not x.get("project") or x["role"] == "coordinator" or x.get("archived"):
            continue
        owner, src = effective_owner(x, sessions, config)
        if src in ("recorded", "ancestry"):
            continue
        if src == "no_coordinator":
            skipped.append(x["key"])
            continue
        ev = (evidence or {}).get(x["key"])
        if ev and any(c["key"] == ev for c in sessions if eligible(c, x["project"])):
            w = {"session": x["key"], "parent": ev, "basis": "launch_evidence"}
        else:
            w = {"session": x["key"], "parent": owner, "basis": "primary"}
        w["status"] = "retry" if x["key"] in conflicts else "applied"
        writes.append(w)
    return {"writes": writes, "skipped": skipped}


UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


def launch_evidence(session, coordinators, labels=(), first_user_turn=""):
    """B1's exact launch evidence (round 12). coordinators: {session uuid: host} of the ELIGIBLE coordinators of the
    session's project. In order: (1) a label `origin:session:<host>:<uuid>` naming one of them; (2) the session's FIRST
    user turn (durable log) containing the full UUID of exactly one of them. Names never count; two candidates → none."""
    for lab in labels:
        m = re.fullmatch(r"origin:session:([a-z0-9-]+):([0-9a-f-]{36})", lab)
        if m and coordinators.get(m.group(2)) == m.group(1):
            return {"owner": f"session:{m.group(1)}:{m.group(2)}", "basis": "origin_label"}
    found = {u for u in UUID_RE.findall(first_user_turn or "") if u in coordinators}
    if len(found) == 1:
        u = found.pop()
        return {"owner": f"session:{coordinators[u]}:{u}", "basis": "first_turn_uuid"}
    return None


def apply_writes(sessions, result):
    out = [dict(x) for x in sessions]
    for w in result["writes"]:
        if w["status"] == "applied":
            for x in out:
                if x["key"] == w["session"]:
                    x["parent"] = w["parent"]
    return out


# ───────────────────────────── Updates list (D7) ─────────────────────────────

def updates(items, scope="all", ack_seq=None, limit=50, cursor=None):
    """items: [{id, occurred_at, observed_seq, project, kind, notice_id?, linked_notice?}].
    The list = every notification + every CP entity event that no notification represents (linked_notice null),
    newest occurred_at first, ties by id (keyset cursor). 'new' = observed after the scope's ack."""
    sel = [i for i in items if (scope == "all" or i.get("project") == scope)
           and not (i["kind"] != "notification" and i.get("linked_notice"))]
    sel.sort(key=lambda i: (-i["occurred_at"], i["id"]))
    if cursor:
        sel = [i for i in sel if (-i["occurred_at"], i["id"]) > (-cursor[0], cursor[1])]
    page = sel[:limit]
    return {"ids": [i["id"] for i in page],
            "new": [i["id"] for i in page if ack_seq is not None and i["observed_seq"] > ack_seq],
            "cursor": [page[-1]["occurred_at"], page[-1]["id"]] if len(sel) > limit else None}


def join_updates(notices, occurrences):
    """R7-6: generate the All-updates items from real notice payloads and CP occurrences.
    A notice represents an occurrence when: build → correlation_id == f"build-{number}" (same project);
    escalation → correlation_id == the escalation EVENT id; burst → correlation_id == f"art-burst:{burst_id}" or the
    burst's suppressed_by names the notice. Represented occurrences are not listed separately; each notice carries
    `represents` (typed refs)."""
    items, represented = [], set()
    for n in notices:
        reps = []
        for o in occurrences:
            hit = (o["type"] == "build" and n.get("correlation_id") == f"build-{o['number']}" and n.get("project") == o.get("project")) \
                or (o["type"] == "escalation" and n.get("correlation_id") == o["event_id"]) \
                or (o["type"] == "burst" and (n.get("correlation_id") == f"art-burst:{o['burst_id']}" or o.get("suppressed_by") == n["id"]))
            if hit:
                reps.append(o["ref"])
                represented.add(o["ref"])
        items.append({"id": n["id"], "kind": "notification", "represents": reps, "occurred_at": n["at"]})
    for o in occurrences:
        if o["ref"] not in represented:
            items.append({"id": o["ref"], "kind": o["type"], "represents": [], "occurred_at": o["at"]})
    items.sort(key=lambda i: (-i["occurred_at"], i["id"]))
    return items


# ───────────────────────────── project_seed preamble (D11) ─────────────────────────────

SEED_BUDGET = 1500      # tokens (approx. chars / 4): soft when the open questions alone exceed it (R7-1)
SEED_MAX_CHARS = 32000  # the wire maximum (ProjectSeed.text): all questions always fit below it at Forge's 200-char titles


def _esc(v):
    """Entities for & " < >, and CR/LF as a space so a value can never start a forged line (R7-1d)."""
    t = str(v).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return t.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


NEEDS_YOU_HREF = "http://thor.tail737f2a.ts.net:9500/v1/cp/views/needs-you?project_id={}"


def cloud_allowed(project_private=None, default_private=False):
    """O6 (per-project policy): a project marked private never sends its card to a cloud model. Unset → the global
    default, which is False (everything allowed) until Damien flips it."""
    return not (default_private if project_private is None else project_private)


PROJECT_HREF = "http://thor.tail737f2a.ts.net:9500/v1/cp/views/project/{}"
GOAL_CAP = 2000         # escaped characters (R8-1): a Forge brief may be 8,192 raw characters, which escaping can grow ×5
VALUE_CAP = 300         # escaped characters for any other single value (names, statuses, list items)
LIST_CAPS = {"coordinators": 5, "sessions": 20, "side_quests": 20}


def _esc_cap(v, cap):
    """Escape, then bound the ESCAPED length without splitting an entity; '…' marks a cut. Returns (text, cut?)."""
    e = _esc(v)
    if len(e) <= cap:
        return e, False
    raw = str(v)
    lo, hi = 0, len(raw)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(_esc(raw[:mid])) <= cap - 1:
            lo = mid
        else:
            hi = mid - 1
    return _esc(raw[:lo]) + "…", True


def seed_owner(purpose, owner_param, current_primary):
    """R8-2: the seed query's owner selection. 'none' = the frozen ABSENCE of an owner; a ref = that session; omitted =
    the current primary, allowed only for voice. A side quest must always say which (its frozen create parent)."""
    if owner_param == "none":
        return {"owner": None}
    if owner_param:
        return {"owner": owner_param}
    if purpose == "side_quest":
        return {"error": 400, "reason": "owner_required"}
    return {"owner": current_primary}


def seed_preamble(seed):
    """Render project_seed(P) (VISION D11): name, goal, coordinators and status, the owner (the FROZEN parent of the
    side-quest envelope, or none), all open questions, working/waiting sessions + quiet ones, open side quests, last 5
    builds, last 10 artifacts. Deterministic. The text never exceeds SEED_MAX_CHARS (R8-1):
      - every value is escaped and bounded (goal 2,000, any other value 300 escaped chars, '…' marks a cut);
      - coordinators (5), sessions (20) and side quests (20) end with 'and n more <section>: <href>' beyond their cap;
      - over the ~1,500-token budget: quiet sessions, then artifacts beyond 5, then shipped beyond 3 are cut;
      - questions fill the remaining space; past the cap, 'and n more open questions: <href>' (R7-1e).
    Every omission is listed in `overflows` [{section, omitted, href}]; nothing is dropped silently."""
    s = {k: (list(v) if isinstance(v, list) else v) for k, v in seed.items()}
    s.setdefault("side_quests", [])
    pid = s.get("project_id", "00000000-0000-0000-0000-000000000000")
    phref, qhref = PROJECT_HREF.format(pid), NEEDS_YOU_HREF.format(pid)
    overflows = []
    goal, cut = _esc_cap(s["goal"], GOAL_CAP)
    if cut:
        goal += f" (full goal: {phref})"
        overflows.append({"section": "goal", "omitted": len(str(s["goal"])), "href": phref})
    name = _esc_cap(s["name"], VALUE_CAP)[0]
    owner = "no coordinator yet" if s.get("owner") is None else _esc_cap(s["owner"], VALUE_CAP)[0]

    def capped(section, items, fmt):
        cap = LIST_CAPS[section]
        out = [fmt(x) for x in items[:cap]]
        if len(items) > cap:
            out.append(f"and {len(items) - cap} more {section.replace('_', ' ')}: {phref}")
            overflows.append({"section": section, "omitted": len(items) - cap, "href": phref})
        return out

    coords = capped("coordinators", s["coordinators"],
                    lambda c: f'{_esc_cap(c["name"], VALUE_CAP)[0]} ({_esc_cap(c["status"], VALUE_CAP)[0]})')
    sessions = capped("sessions", s["sessions"], lambda x: _esc_cap(x, VALUE_CAP)[0])
    side_q = capped("side_quests", s["side_quests"], lambda x: _esc_cap(x, VALUE_CAP)[0])

    def render(questions):
        lines = [f'<project-context project="{name}" as_of_seq="{s["as_of_seq"]}">', f'Goal: {goal}',
                 "Coordinators: " + "; ".join(coords), f'You are owned by: {owner}']
        lines += ["Open questions:"] + [f"- {q}" for q in questions]
        lines += ["Sessions:"] + [f"- {x}" for x in sessions + [_esc_cap(q, VALUE_CAP)[0] for q in s["quiet"]]]
        lines += ["Side quests:"] + [f"- {x}" for x in side_q]
        lines += ["Shipped:"] + [f"- {_esc_cap(b, VALUE_CAP)[0]}" for b in s["shipped"]]
        lines += ["Artifacts:"] + [f"- {_esc_cap(a, VALUE_CAP)[0]}" for a in s["artifacts"]]
        lines.append("</project-context>")
        return "\n".join(lines)

    all_q = [_esc_cap(q, VALUE_CAP)[0] for q in s["questions"]]
    text = render(all_q)
    for key, keep in (("quiet", 0), ("artifacts", 5), ("shipped", 3)):
        while len(text) / 4 > SEED_BUDGET and len(s[key]) > keep:
            s[key].pop()
            text = render(all_q)
    overflow, kept = None, len(all_q)
    if len(text) > SEED_MAX_CHARS:
        lo, hi = 0, len(all_q)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(render(all_q[:mid] + [f"and {len(all_q) - mid} more open questions: {qhref}"])) <= SEED_MAX_CHARS:
                lo = mid
            else:
                hi = mid - 1
        kept = lo
        text = render(all_q[:lo] + [f"and {len(all_q) - lo} more open questions: {qhref}"])
        overflow = {"questions": len(all_q) - lo, "href": qhref}
        overflows.append({"section": "questions", "omitted": len(all_q) - lo, "href": qhref})
    refused = None
    if len(text) > SEED_MAX_CHARS:   # unreachable by construction (bounded sections); kept as the typed outcome
        refused = {"error": "seed_too_large", "chars": len(text)}
    allowed = cloud_allowed(seed.get("private"), seed.get("default_private", False))
    return {"overflow": overflow, "overflows": overflows, "refused": refused, "cloud_allowed": allowed, "text": text,
            "tokens": int(len(text) / 4), "chars": len(text), "fits_wire": len(text) <= SEED_MAX_CHARS,
            "questions": kept, "side_quests": len(s["side_quests"]), "quiet": len(s["quiet"]),
            "artifacts": len(s["artifacts"]), "shipped": len(s["shipped"]),
            "has_side_quests_section": "Side quests:" in text and all(x in text for x in side_q[:LIST_CAPS["side_quests"]]),
            "owner_line": f'You are owned by: {owner}', "sha": hashlib.sha256(text.encode()).hexdigest()[:12]}


# ───────────────────────────── experimental-data.md (R10-4, R11-5) ─────────────────────────────
# ONE manifest: cp-platform's platform/exp/manifest.json is the only data. This adapter is the one specified reading
# of it (experimental-data.md §0); C1, F1s, U1x and U11 port it and bind cases/exp.json. Nothing here is hardcoded.

import copy as _copy
from pathlib import Path as _Path

MANIFEST_PATH = _Path(__file__).resolve().parents[2] / "platform" / "exp" / "manifest.json"
WRITE_ACTIONS = {"attach", "undo_attach", "side_quest_create", "answer", "permission_decision", "steer", "dispatch",
                 "voice_tool_write", "b1_apply"}
LOCAL_ACTIONS = {"ack", "dismiss"}        # the exp control plane's own store; never reaches a Forge


def load_manifest(path=None, overlay=None):
    m = json.loads(_Path(path or MANIFEST_PATH).read_text())
    for k, v in (overlay or {}).items():   # test-only: e.g. hosts.<h>.instance_ids
        cur = m
        *parts, last = k.split(".")
        for part in parts:
            cur = cur[part]
        cur[last] = v
    return m


FEEDS = ("sessions", "notifications", "projects", "publications")


def resolve_transport(m, transport):
    """One lookup over BOTH live/shadow `transports` and `replay_sources` (R12-3). Returns {kind, origin, ...} or None."""
    t = m["transports"].get(transport)
    if t is not None:
        return dict(t, name=transport)
    for host, r in (m.get("replay_sources") or {}).items():
        if r.get("transport") == transport:
            return dict(r, name=transport, origin=r.get("origin", host))
    return None


def transport_active(m, spec):
    """The selected profile decides. Replay sources are files, never production, and are active in every profile;
    a live or shadow transport is active only if the profile lists it (its `enabled` flag must agree: X18)."""
    if spec["kind"] == "replay":
        return True
    return spec["name"] in m["profiles"][m["profile"]]["enabled_transports"]


def _instance_host(m, instance_id):
    for h, v in m["hosts"].items():
        if instance_id in v.get("instance_ids", []):
            return h
    return None


def _feed_identity(origin, rec):
    """Per-feed entity key and origin occurrence (manifest-semantics.md §3). Never includes the transport."""
    f = rec["feed"]
    if f == "publications":
        return f"session:{origin}:{rec['session']}", event_id([origin, "log", rec["session"], rec["seq"]])
    if f == "sessions":
        return f"session:{origin}:{rec['id']}", event_id([origin, "sessions", rec["id"], rec["updated_at"]])
    if f == "notifications":
        return f"notification:{origin}:{rec['id']}", event_id([origin, "notifications", rec["seq"]])
    if f == "projects":
        return f"project:{rec['id']}", event_id([origin, "projects", rec["id"], rec["revision"]])
    raise ValueError(f)


def exp_ingest(transport, rec, m=None):
    """Ingest one record (manifest-semantics.md §2–§4):
    1. resolve the transport over transports + replay_sources (unknown → refused);
    2. the selected profile must activate it (else transport_disabled);
    3. origin: shadow/replay transports take their declared origin (a record naming another is refused); a live
       transport takes its own origin, or, for a guild-scope row, the host its instance_id maps to (else refused);
    4. feed: one of the four, with its required fields;
    5. identity from the ORIGIN only; cursor: shadow/replay (transport, feed, transport_seq), live per Forge's own."""
    m = m or load_manifest()
    spec = resolve_transport(m, transport)
    if spec is None:
        return {"error": "unknown_transport"}
    if not transport_active(m, spec):
        return {"error": "transport_disabled"}
    feed = rec.get("feed", "publications")
    if feed not in FEEDS:
        return {"error": "feed_not_carried"}
    rec = dict(rec, feed=feed)
    if spec["kind"] in ("shadow", "replay"):
        origin = spec.get("rows_origin_host", spec["origin"])
        if rec.get("origin_host", origin) != origin:
            return {"error": "origin_not_allowed_for_transport"}
        if "transport_seq" not in rec:
            return {"error": "missing_transport_seq"}
        cursor = [transport, feed, rec["transport_seq"]]
    else:
        origin = spec["origin"]
        inst = rec.get("instance_id")
        if inst is not None:
            mapped = _instance_host(m, inst)
            if mapped is None:
                return {"error": "foreign_instance"}
            origin = mapped
        cursor = [transport, feed, rec.get("session"), rec.get("seq")] if feed == "publications" else \
                 [transport, feed, rec.get("seq")]
    if origin not in m["hosts"]:
        return {"error": "unknown_host"}
    need = {"publications": ("session", "seq"), "sessions": ("id", "updated_at"),
            "notifications": ("id", "seq"), "projects": ("id", "revision")}[feed]
    if any(k not in rec for k in need):
        return {"error": "missing_feed_fields"}
    entity, occ = _feed_identity(origin, rec)
    out = {"origin": origin, "entity": entity, "occurrence": occ, "cursor": cursor, "mode": m["hosts"][origin]["mode"]}
    if feed == "publications":
        out["session"] = entity
    return out


def build_ref(release_product, number, m=None):
    """R12-3: the release tool's product key (manifest build_product, e.g. ios-cp) is NOT the ref product. Refs use
    the app's bundle id (manifest build_ref_product, else app.bundle_id): build:com.xteo.lexi.chat.cp:<N>."""
    m = m or load_manifest()
    if release_product != m["build_product"]:
        return {"error": "unknown_release_product"}
    return {"ref": f"build:{m.get('build_ref_product') or m['app']['bundle_id']}:{number}"}


def exp_ingest_many(records, m=None):
    """Dedupe across transports by the origin occurrence (or the origin session for non-publication rows)."""
    m = m or load_manifest()
    seen, out = set(), []
    for transport, row in records:
        r = exp_ingest(transport, row, m)
        if "error" in r:
            out.append(r)
            continue
        k = r["occurrence"] or r["entity"]
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def exp_resume(rows, after):
    """A shadow page after a restart: rows strictly after the stored transport cursor, in transport order."""
    return [r for r in sorted(rows, key=lambda r: r["transport_seq"]) if r["transport_seq"] > after]


def exp_action(origin_host, action, m=None, project_home=None, owner_host=None, instance_id=None, session_id=None):
    """What the exp app may do with an entity: decided by its ORIGIN host's manifest mode, never by storage."""
    m = m or load_manifest()
    h = m["hosts"].get(origin_host)
    if h is None:
        return {"result": "deny", "reason": "unknown_host"}
    rw = origin_host in m["writes_allowed_to_hosts"] and h["mode"] == "live_rw"
    if action == "open_session":
        if rw:
            return {"result": "open_in_exp"}
        url = m["production_session_navigation"].split(": ", 1)[1].split(" ")[0]
        return {"result": "handoff_daily_app",
                "url": url.replace("<instance_id>", str(instance_id)).replace("<session_id>", str(session_id))}
    if action in LOCAL_ACTIONS:
        return {"result": "allow_local"}
    if action == "side_quest_create":
        ok = lambda x: x in m["writes_allowed_to_hosts"]
        return {"result": "allow"} if ok(project_home) and (owner_host is None or ok(owner_host)) \
            else {"result": "deny", "reason": "read_only_source"}
    if action in WRITE_ACTIONS:
        return {"result": "allow"} if rw else {"result": "deny", "reason": "read_only_source"}
    return {"result": "deny", "reason": "unknown_action"}


def exp_discard_envelope(envelope_host, target_env, m=None):
    """Exact membership from the manifest. Going to production: discard envelopes whose host is in
    envelope_discard_hosts (or unknown). Staying on exp: keep only envelopes for a writable host."""
    m = m or load_manifest()
    if envelope_host not in m["hosts"]:
        return True
    if target_env == "prod":
        return envelope_host in m["envelope_discard_hosts"]
    return envelope_host not in m["writes_allowed_to_hosts"]


def exp_admit_project(host, uuid_, known, m=None):
    """A registration on a writable exp host whose UUID already exists on a read-only host is refused."""
    m = m or load_manifest()
    if host in m["writes_allowed_to_hosts"] and any(m["hosts"].get(h, {}).get("mode") == "live_ro" for h in known.get(uuid_, [])):
        return {"admitted": False, "reason": "uuid_owned_by_read_only_host"}
    return {"admitted": True}


def exp_manifest_conforms(m=None):
    """The adapter's assumptions about the actual file, checked: every field it reads exists and is coherent."""
    m = m or load_manifest()
    problems = []
    for h, v in m["hosts"].items():
        if v.get("mode") not in ("live_rw", "live_ro"):
            problems.append(f"hosts.{h}.mode")
        if v.get("transport") not in m["transports"]:
            problems.append(f"hosts.{h}.transport")
    if m.get("profile") not in m.get("profiles", {}):
        problems.append("profile")
    else:
        enabled = set(m["profiles"][m["profile"]]["enabled_transports"])
        for t, v in m["transports"].items():
            if bool(v.get("enabled")) != (t in enabled):
                problems.append(f"transports.{t}.enabled disagrees with profile {m['profile']}")
        for t in enabled:
            if t not in m["transports"]:
                problems.append(f"profiles.{m['profile']}.enabled_transports: {t}")
    shadows = [t for t, v in m["transports"].items() if v.get("kind") == "shadow"]
    for t in shadows:
        o = m["transports"][t].get("rows_origin_host")
        if o not in m["hosts"] or m["hosts"][o]["mode"] != "live_ro":
            problems.append(f"transports.{t}.rows_origin_host")
    for h, r in (m.get("replay_sources") or {}).items():
        if r.get("origin", h) not in m["hosts"] or m["hosts"][r.get("origin", h)]["mode"] != "live_ro":
            problems.append(f"replay_sources.{h}: replay origins must be read-only hosts")
        if r.get("transport") in m["transports"]:
            problems.append(f"replay_sources.{h}.transport collides with a live transport")
    if set(m["writes_allowed_to_hosts"]) != {h for h, v in m["hosts"].items() if v["mode"] == "live_rw"}:
        problems.append("writes_allowed_to_hosts")
    if "<instance_id>" not in m.get("production_session_navigation", ""):
        problems.append("production_session_navigation")
    if not (m.get("build_ref_product") or m.get("app", {}).get("bundle_id")):
        problems.append("build ref product")
    return {"ok": not problems, "problems": problems, "shadow_transports": shadows,
            "replay_transports": sorted(r["transport"] for r in (m.get("replay_sources") or {}).values())}
