"""What this resident has heard its flock peers run.

Peers report their installed tools as ``valkyrie.evolution.skill_inventory``
events, which cross the flock on change and on a slow sweep. Every crossing event
names the sender's whole inventory, so a tool a peer retired drops out here at
its next crossing. Nothing expires by age: a peer that is down keeps its claims,
and adopting one re-verifies it on this resident regardless.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from ravn.ports.peer_tools import PeerTool


class PeerToolError(ValueError):
    """A peer's inventory record cannot be understood."""


class PeerToolCatalogError(RuntimeError):
    """The catalog on disk cannot be read, which never reads as "no peers"."""


def peer_tool_from_inventory(payload: Mapping[str, Any]) -> PeerTool | None:
    """The tool a peer's inventory event reports, or ``None`` when it is not eligible.

    Only a tool the peer verified is eligible: an unverified one cannot run
    there either. A record missing what any tool needs raises.
    """
    manifest = payload.get("learned_tool_manifest")
    if not isinstance(manifest, dict) or not manifest:
        return None
    name = str(payload.get("skill_name") or manifest.get("name") or "").strip()
    artifact_id = str(payload.get("learning_id") or "").strip()
    valkyrie_id = str(payload.get("valkyrie_id") or "").strip()
    tool_code = str(payload.get("tool_code") or "")
    if not (name and artifact_id and valkyrie_id and tool_code.strip()):
        raise PeerToolError(
            f"peer inventory record for {name or 'an unnamed tool'!r} needs a name, a "
            f"learning_id, a valkyrie_id and tool_code; {valkyrie_id or 'a peer'} sent "
            f"{sorted(payload)}"
        )
    if payload.get("verified") is not True:
        return None
    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    canary = payload.get("canary_sample")
    return PeerTool(
        name=name,
        artifact_id=artifact_id,
        valkyrie_id=valkyrie_id,
        environment_id=str(payload.get("environment_id") or ""),
        manifest=dict(manifest),
        tool_code=tool_code,
        test_code=str(payload.get("test_code") or ""),
        requirements=tuple(str(item) for item in payload.get("requirements") or ()),
        capability_id=str(payload.get("capability_id") or ""),
        canary_sample=dict(canary) if isinstance(canary, dict) else {},
        runs=int(usage.get("runs") or 0),
        successes=int(usage.get("successes") or 0),
        reported_at=str(payload.get("reported_at") or ""),
    )


class PeerToolCatalog:
    """Durable JSON store of peers' tools, one record per (peer, tool)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._tools: dict[tuple[str, str], PeerTool] = self._load()

    def tools(self) -> list[PeerTool]:
        return sorted(self._tools.values(), key=lambda tool: (tool.name, tool.valkyrie_id))

    def record(self, tool: PeerTool, *, inventory: Iterable[str] = ()) -> None:
        """Hold *tool*, and forget whatever else *tool*'s peer no longer reports."""
        key = (tool.valkyrie_id, tool.name)
        known = self._tools.get(key)
        names = set(inventory)
        stale = [
            peer_key
            for peer_key in self._tools
            if names and peer_key[0] == tool.valkyrie_id and peer_key[1] not in names | {tool.name}
        ]
        self._tools[key] = tool
        for peer_key in stale:
            del self._tools[peer_key]
        # Usage and timestamps move on every crossing; the file only has to
        # survive a restart until the next one.
        if stale or known is None or _material(known) != _material(tool):
            self._save()

    def forget(self, valkyrie_id: str, name: str) -> None:
        """Drop a tool its peer now reports as no longer eligible."""
        if self._tools.pop((valkyrie_id, name), None) is not None:
            self._save()

    def _load(self) -> dict[tuple[str, str], PeerTool]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            rows = json.loads(raw)["peer_tools"]
            tools = [
                PeerTool(**{**row, "requirements": tuple(row.get("requirements") or ())})
                for row in rows
            ]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise PeerToolCatalogError(
                f"peer tool catalog {self._path} is unreadable: {exc}. Delete it and the "
                "next flock sweep rebuilds it."
            ) from exc
        return {(tool.valkyrie_id, tool.name): tool for tool in tools}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"peer_tools": [asdict(tool) for tool in self.tools()]}
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self._path)


def _material(tool: PeerTool) -> PeerTool:
    return replace(tool, runs=0, successes=0, reported_at="")


def distinct_peer_tools(
    tools: Sequence[PeerTool],
    *,
    exclude: Iterable[str] = (),
) -> list[PeerTool]:
    """One copy of each tool name, the one with the most successful runs.

    Adoption copies a tool across residents, and judging every copy would ask
    the same question once per peer.
    """
    excluded = {name.strip().lower() for name in exclude}
    best: dict[str, PeerTool] = {}
    for tool in tools:
        key = tool.name.strip().lower()
        if key in excluded:
            continue
        held = best.get(key)
        if held is None or (tool.successes, tool.runs) > (held.successes, held.runs):
            best[key] = tool
    return list(best.values())
