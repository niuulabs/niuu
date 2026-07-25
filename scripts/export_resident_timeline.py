#!/usr/bin/env python
"""Export a resident's working-state history as a self-contained visual.

Reads durable turn records through ``ResidentStatePort`` and writes both the
timeline JSON and a standalone HTML page with the data embedded, so the result
opens from ``file://`` with no server and no network.

    python scripts/export_resident_timeline.py \
        --state-root ~/.ravn/state \
        --resident ivaldi \
        --charter "Steward this repository..." \
        --out docs/demo/resident-timeline

Point ``--state-root`` at the directory holding ``resident/continuation/``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ravn.adapters.resident_state.mimir import LocalResidentState  # noqa: E402
from ravn.resident_timeline import build_resident_timeline  # noqa: E402

_TEMPLATE = REPO_ROOT / "docs" / "demo" / "resident-timeline" / "index.html"
_MARKER_START = '<script id="data" type="application/json">'
_MARKER_END = "</script>"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--resident", default="resident")
    parser.add_argument("--charter", default="")
    parser.add_argument("--environment-name", default="")
    parser.add_argument("--environment-type", default="")
    parser.add_argument("--prefix", default="", help="Limit to one case prefix.")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--note", default="", help="Provenance label shown in the page footer.")
    parser.add_argument(
        "--template",
        type=Path,
        default=_TEMPLATE,
        help="HTML renderer to embed the timeline into.",
    )
    return parser.parse_args()


def _embed(template_html: str, payload: str) -> str:
    start = template_html.index(_MARKER_START) + len(_MARKER_START)
    end = template_html.index(_MARKER_END, start)
    return template_html[:start] + payload + template_html[end:]


async def _run(args: argparse.Namespace) -> int:
    if not args.state_root.is_dir():
        print(f"state root does not exist: {args.state_root}", file=sys.stderr)
        return 2
    timeline = await build_resident_timeline(
        LocalResidentState(args.state_root),
        resident_id=args.resident,
        charter=args.charter,
        environment_name=args.environment_name or args.resident,
        environment_type=args.environment_type,
        prefix=args.prefix,
    )
    payload = timeline.as_dict()
    # A live export must not inherit the template's "illustrative" provenance note.
    payload["note"] = args.note or f"Live export · {args.resident} · {len(timeline.turns)} turns"
    if not payload["turns"]:
        print(
            f"no durable resident turns found under {args.state_root}. Has this resident run yet?",
            file=sys.stderr,
        )
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(payload, indent=2, ensure_ascii=False)
    (args.out / "timeline.json").write_text(json_text, encoding="utf-8")

    template_html = args.template.read_text(encoding="utf-8")
    page = args.out / "index.html"
    page.write_text(_embed(template_html, json_text), encoding="utf-8")
    print(f"{len(payload['turns'])} turns -> {page}")
    return 0


def main() -> int:
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
