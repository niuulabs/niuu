#!/usr/bin/env python
"""Export any resident's working-state history as a self-contained visual.

Reads durable turn records through ``ResidentStatePort`` and writes both the
timeline JSON and a standalone HTML page with the data embedded, so the result
opens from ``file://`` with no server and no network.

From a local state directory:

    python scripts/export_resident_timeline.py \
        --state-root ~/.ravn/resident-state --resident ivaldi \
        --out /tmp/ivaldi

From a resident running in Kubernetes (state is copied out read-only):

    python scripts/export_resident_timeline.py \
        --pod ivaldi-bdbb8fb56-lt49x --namespace nats \
        --resident ivaldi --out /tmp/ivaldi

Add ``--watch 30`` to re-export on an interval. Serve the output directory
(``python -m http.server`` inside it) and the page reloads itself as new turns
land, which is what makes it a live dashboard rather than a snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ravn.adapters.resident_state.mimir import LocalResidentState  # noqa: E402
from ravn.resident_timeline import build_resident_timeline  # noqa: E402

_TEMPLATE = REPO_ROOT / "docs" / "demo" / "resident-timeline" / "index.html"
_MARKER_START = '<script id="data" type="application/json">'
_MARKER_END = "</script>"
_DEFAULT_POD_STATE = "/home/niuu/.ravn/resident-state"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--state-root", type=Path, help="Directory holding resident/continuation/.")
    source.add_argument("--pod", help="Kubernetes pod to copy resident state out of.")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--context", default="", help="kubectl context.")
    parser.add_argument("--container", default="", help="Container within the pod.")
    parser.add_argument("--pod-state-path", default=_DEFAULT_POD_STATE)
    parser.add_argument("--resident", default="resident")
    parser.add_argument("--charter", default="")
    parser.add_argument("--environment-name", default="")
    parser.add_argument("--environment-type", default="")
    parser.add_argument("--prefix", default="", help="Limit to one case prefix.")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--note", default="", help="Provenance label shown in the page footer.")
    parser.add_argument("--watch", type=float, default=0.0, help="Re-export every N seconds.")
    parser.add_argument("--template", type=Path, default=_TEMPLATE)
    return parser.parse_args()


def _copy_state_from_pod(args: argparse.Namespace, destination: Path) -> Path:
    """Stream the resident state directory out of a pod, read-only."""
    state_path = args.pod_state_path.rstrip("/")
    parent, _, leaf = state_path.rpartition("/")
    command = ["kubectl"]
    if args.context:
        command += ["--context", args.context]
    command += ["-n", args.namespace, "exec", args.pod]
    if args.container:
        command += ["-c", args.container]
    command += ["--", "tar", "cf", "-", "-C", parent or "/", leaf]

    archive = destination / "state.tar"
    with archive.open("wb") as handle:
        result = subprocess.run(command, stdout=handle, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"could not read state from {args.namespace}/{args.pod}: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter="data")
    return destination / leaf


async def _export_once(args: argparse.Namespace, state_root: Path) -> int:
    timeline = await build_resident_timeline(
        LocalResidentState(state_root),
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
            f"no durable resident turns found under {state_root}. Has this resident run yet?",
            file=sys.stderr,
        )
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(payload, indent=2, ensure_ascii=False)
    (args.out / "timeline.json").write_text(json_text, encoding="utf-8")

    template_html = args.template.read_text(encoding="utf-8")
    start = template_html.index(_MARKER_START) + len(_MARKER_START)
    end = template_html.index(_MARKER_END, start)
    (args.out / "index.html").write_text(
        template_html[:start] + json_text + template_html[end:],
        encoding="utf-8",
    )
    return len(payload["turns"])


async def _run(args: argparse.Namespace) -> int:
    if args.state_root is not None and not args.state_root.is_dir():
        print(f"state root does not exist: {args.state_root}", file=sys.stderr)
        return 2

    while True:
        with tempfile.TemporaryDirectory() as tmp:
            if args.pod:
                try:
                    state_root = _copy_state_from_pod(args, Path(tmp))
                except RuntimeError as exc:
                    print(exc, file=sys.stderr)
                    if not args.watch:
                        return 2
                    time.sleep(args.watch)
                    continue
            else:
                state_root = args.state_root
            turns = await _export_once(args, state_root)
        print(f"[{time.strftime('%H:%M:%S')}] {turns} turns -> {args.out / 'index.html'}")
        if not args.watch:
            return 0 if turns else 1
        time.sleep(args.watch)


def main() -> int:
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
