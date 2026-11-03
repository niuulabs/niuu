"""One meaning of "working" (G10, judge R5-5; design §V9.2). Reference implementation.

CP computes this for every Session it returns (`Session.derived`); counts, ordering and the row pill all use it,
and the app's row adapter (U1) renders it instead of re-deriving from Forge's raw fields. The Swift port in
ForgeKit must pass the same goldens (`fixtures/derived-state.json`).

Inputs come from Forge (lifecycle `status`, `activity_state`, `activity_state_since`, `last_active`) and from
the session's durable log rows, read by their own source timestamps (never CP's observation time, the log
head, a heartbeat or `last_active`).
"""
from __future__ import annotations
from datetime import datetime, timedelta

QUIET_AFTER = timedelta(minutes=15)       # no work row for this long → quiet, unless a tool is open
QUIET_AFTER_TOOL = timedelta(minutes=60)  # while a tool call is open

# Rows that prove the agent is doing turn work. Everything else (system/capabilities handshakes on reconnect,
# delivery states user_*, terminal frames and snapshots, Notification hooks, compaction) is excluded.
WORK_KINDS = {"assistant", "result", "content_block_start", "content_block_delta", "content_block_stop", "message_delta"}
WORK_HOOKS = {"UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
              "MessageDisplay", "Stop", "SubagentStart", "SubagentStop"}
TOOL_OPEN_HOOK, TOOL_CLOSE_HOOKS = "PreToolUse", {"PostToolUse", "PostToolUseFailure"}


def ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def is_work(row: dict) -> bool:
    return row["kind"] in WORK_KINDS or (row["kind"] == "claude_hook" and row.get("hook") in WORK_HOOKS)


# ForgeKit's lifecycle map (UiBucket.swift:34-48); an unknown status is an error there too.
LIFECYCLE = {"created": "active", "starting": "active", "provisioning": "active", "running": "active", "idle": "active",
             "ready": "active", "stopping": "stopped", "stopped": "stopped", "terminated": "stopped", "completed": "stopped",
             "error": "error", "failed": "error", "crashed": "error", "errored": "error", "archived": "archived"}


def work_facts(rows: list[dict]) -> tuple[datetime | None, bool]:
    """(last_work_at, tool_open) from log rows in seq order. last_work_at is the NEWEST qualifying source time, not the
    last row's: Forge stores a producer timestamp separately from seq (rest_session_log.py:138-148, judge R6-3)."""
    last, open_tools = None, 0
    for r in rows:
        if not is_work(r):
            continue
        t = ts(r["ts"])
        last = t if last is None or t > last else last
        if r.get("hook") == TOOL_OPEN_HOOK:
            open_tools += 1
        elif r.get("hook") in TOOL_CLOSE_HOOKS and open_tools:
            open_tools -= 1
        elif r["kind"] == "result" or r.get("hook") == "Stop":
            open_tools = 0
    return last, open_tools > 0


def derive(s: dict, rows: list[dict] | None, open_native_asks: list[str] | None, now: datetime) -> dict:
    """s: Forge session fields. rows: the session's log rows, or None when the caller has only F12d's fields
    (`last_work_at`, `tool_open` on the session: the Code tab's path). open_native_asks: opened_at of the open native
    controls (CP's ask store, or F12d's `open_controls`); None means unknown and is never read as "none"."""
    status, act = (s.get("status") or "").lower(), s.get("activity_state")
    if rows is None:
        last_work, tool_open = ts(s.get("last_work_at")), bool(s.get("tool_open"))
    else:
        last_work, tool_open = work_facts(rows)
    tool_open = tool_open or act == "tool_executing"
    since = s.get("activity_state_since")
    out = lambda state, basis, kind, at: {"state": state, "basis": basis, "tool_open": tool_open,
                                         "last_work_at": last_work.isoformat() if last_work else None,
                                         "time": {"kind": kind, "at": at}}
    # 1. Lifecycle wins over stale activity (ForgeKit FKSession.swift:273-304, UiBucket.swift:34-48).
    bucket = LIFECYCLE.get(status, "error")
    if bucket == "archived":
        return out("archived", "forge", "stopped_at", s.get("archived_at") or since)
    if bucket == "stopped":
        return out("stopped", "forge", "stopped_at", s.get("updated_at") or since)
    if bucket == "error" or act == "error":
        return out("error", "forge", "stopped_at", since or s.get("updated_at"))
    # 2. Waiting on a person: an open native control (exact by request_id). Two pending controls keep the session
    #    waiting until both resolve. Unknown control state with Forge saying awaiting_input stays awaiting_input.
    if open_native_asks:
        return out("awaiting_input", "forge", "waiting_since", min(open_native_asks))
    if open_native_asks is None and act == "awaiting_input":
        return out("awaiting_input", "forge", "waiting_since", since)
    # 3. Forge says busy: believe it only while work rows keep arriving.
    if act in ("active", "tool_executing", "provisioning", "awaiting_input"):  # awaiting_input with no open control: busy
        window = QUIET_AFTER_TOOL if tool_open else QUIET_AFTER
        anchor = last_work or ts(since)
        if anchor and now - anchor <= window:
            return out("working", "forge", "running_since", since)
        return out("idle", "quiet_inferred", "last_output", (last_work.isoformat() if last_work else since))
    # 4. Forge says idle: idle, whatever the recent output (a completed turn is not working). Never last_active
    #    (the heartbeat, session.py:518-520): with no known work time the time is "unknown" (judge R6-3).
    if last_work:
        return out("idle", "forge", "used", last_work.isoformat())
    return out("idle", "forge", "unknown", None)
