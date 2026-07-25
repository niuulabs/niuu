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

_TEMPLATE = REPO_ROOT / "src" / "ravn" / "static" / "resident-hud.html"
_MARKER_START = '<script id="data" type="application/json">'
_MARKER_END = "</script>"
_DEFAULT_POD_STATE = "/home/niuu/.ravn/resident-state"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--state-root", type=Path, help="Directory holding resident/continuation/.")
    source.add_argument("--pod", help="Kubernetes pod to read the resident timeline from.")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--context", default="", help="kubectl context.")
    parser.add_argument("--container", default="", help="Container within the pod.")
    parser.add_argument("--pod-state-path", default=_DEFAULT_POD_STATE)
    parser.add_argument(
        "--via",
        choices=("api", "files"),
        default="api",
        help=(
            "How to read a pod's timeline. 'api' calls the resident's own "
            "/resident/timeline endpoint from inside the pod and is authoritative; "
            "'files' copies the state directory out and only sees filesystem-backed state."
        ),
    )
    parser.add_argument("--api-port", type=int, default=8080)
    parser.add_argument(
        "--token-env",
        default="RAVN_OPERATOR_TOKEN",
        help="Env var inside the pod holding the operator bearer token.",
    )
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


def _kubectl(args: argparse.Namespace) -> list[str]:
    command = ["kubectl"]
    if args.context:
        command += ["--context", args.context]
    command += ["-n", args.namespace, "exec", args.pod]
    if args.container:
        command += ["-c", args.container]
    return command


def _timeline_from_pod_api(args: argparse.Namespace) -> dict:
    """Ask the resident for its own timeline over its HTTP gateway.

    Run from inside the pod so the operator token never leaves it and no
    port-forward is needed. This is authoritative: it reads through whichever
    resident-state adapter the resident is actually configured with, rather
    than assuming state sits on the filesystem.
    """
    url = f"http://127.0.0.1:{args.api_port}/resident/timeline"
    if args.prefix:
        url = f"{url}?prefix={args.prefix}"
    auth = f'-H "Authorization: Bearer ${args.token_env}"'
    script = f'curl -sS -f {auth} "{url}"'
    result = subprocess.run(
        [*_kubectl(args), "--", "sh", "-lc", script],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"resident timeline API failed on {args.namespace}/{args.pod}: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    try:
        return json.loads(result.stdout.decode())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"resident timeline API returned non-JSON: {exc}") from exc


def _copy_state_from_pod(args: argparse.Namespace, destination: Path) -> Path:
    """Stream the resident state directory out of a pod, read-only."""
    state_path = args.pod_state_path.rstrip("/")
    parent, _, leaf = state_path.rpartition("/")
    command = [*_kubectl(args), "--", "tar", "cf", "-", "-C", parent or "/", leaf]

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


async def _payload_from_files(args: argparse.Namespace, state_root: Path) -> dict:
    timeline = await build_resident_timeline(
        LocalResidentState(state_root),
        resident_id=args.resident,
        charter=args.charter,
        environment_name=args.environment_name or args.resident,
        environment_type=args.environment_type,
        prefix=args.prefix,
    )
    return timeline.as_dict()


def _write(args: argparse.Namespace, payload: dict) -> int:
    # The resident names itself over the API; only fill gaps from the CLI.
    payload.setdefault("environment", {})
    if args.environment_name:
        payload["environment"]["name"] = args.environment_name
    if args.environment_type:
        payload["environment"]["type"] = args.environment_type
    payload["environment"].setdefault("name", payload.get("resident_id", "resident"))
    payload["environment"].setdefault("type", "")
    if args.charter and not payload.get("charter"):
        payload["charter"] = args.charter
    turns = payload.get("turns") or []
    # A live export must not inherit the template's "illustrative" provenance note.
    payload["note"] = (
        args.note or f"Live · {payload.get('resident_id', 'resident')} · {len(turns)} turns"
    )
    if not turns:
        print("no durable resident turns yet. Has this resident run?", file=sys.stderr)
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
    return len(turns)


async def _run(args: argparse.Namespace) -> int:
    if args.state_root is not None and not args.state_root.is_dir():
        print(f"state root does not exist: {args.state_root}", file=sys.stderr)
        return 2

    while True:
        try:
            if args.pod and args.via == "api":
                payload = _timeline_from_pod_api(args)
            elif args.pod:
                with tempfile.TemporaryDirectory() as tmp:
                    payload = await _payload_from_files(args, _copy_state_from_pod(args, Path(tmp)))
            else:
                payload = await _payload_from_files(args, args.state_root)
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            if not args.watch:
                return 2
            time.sleep(args.watch)
            continue
        turns = _write(args, payload)
        print(f"[{time.strftime('%H:%M:%S')}] {turns} turns -> {args.out / 'index.html'}")
        if not args.watch:
            return 0 if turns else 1
        time.sleep(args.watch)


def main() -> int:
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
