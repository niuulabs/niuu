#!/usr/bin/env python3
"""Event observer for the Valkyrie flock proof.

Subscribes to the same Sleipnir transport the resident daemons use (nng IPC
or NATS JetStream) and appends every observed event to a JSONL evidence file.
In NATS mode it also ensures the ``flock.>`` fan-out stream exists and records
which scoped flock subject each fanned-out event arrived on, proving the
flock-scoped subject path independently of the main subject.

Usage:
  uv run python scripts/valkyrie_flock_proof_observer.py \
      --transport nng --out /tmp/valkyrie-flock-proof/events.jsonl \
      --cluster-file /tmp/valkyrie-flock-proof/cluster.yaml \
      --own-address ipc:///tmp/valkyrie-flock-proof/observer.ipc

  uv run python scripts/valkyrie_flock_proof_observer.py \
      --transport nats --out /tmp/valkyrie-flock-proof/events.jsonl \
      --nats-url nats://127.0.0.1:4222
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

FLOCK_STREAM_NAME = "valkyrie_flock"
FLOCK_STREAM_SUBJECT = "flock.>"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["nng", "nats"], required=True)
    parser.add_argument("--out", required=True, help="JSONL output path")
    parser.add_argument("--cluster-file", default="", help="nng cluster yaml with peers")
    parser.add_argument("--own-address", default="", help="nng pub address for the observer")
    parser.add_argument("--nats-url", default="nats://127.0.0.1:4222")
    parser.add_argument("--stream-name", default="ravn_environment")
    parser.add_argument("--subject-prefix", default="ravn.environment")
    parser.add_argument(
        "--inject-feedback-after",
        type=float,
        default=0.0,
        help="Seconds after start to publish one operator feedback event (0 disables)",
    )
    parser.add_argument(
        "--feedback-environment",
        default="cluster-a",
        help="Environment id targeted by the injected feedback",
    )
    parser.add_argument(
        "--approve-reviews",
        action="store_true",
        help=(
            "Act as the operator: approve every odin.review.requested item by "
            "publishing the same odin.review.decided envelope the platform's "
            "decide endpoint publishes"
        ),
    )
    parser.add_argument(
        "--approve-delay",
        type=float,
        default=2.0,
        help="Seconds between seeing a review request and approving it",
    )
    return parser.parse_args()


def _make_review_approver(transport, args: argparse.Namespace):
    """Return an event handler that approves filed review items once each."""
    from ravn.odin.review import ReviewItem, review_decided_event
    from sleipnir.domain import registry

    approved: set[str] = set()

    async def _approve(item: ReviewItem) -> None:
        await asyncio.sleep(args.approve_delay)
        item.decide(
            decision="approved",
            operator_id="human:proof-operator",
            reason="Approved by the proof operator after inspecting the artifact.",
        )
        await transport.publish(review_decided_event(item, source="ravn:odin-review-proof"))
        print(f"observer: approved review item {item.item_id}", flush=True)

    async def _handle(event) -> None:
        if event.event_type != registry.ODIN_REVIEW_REQUESTED:
            return
        try:
            item = ReviewItem.from_payload(dict(event.payload))
        except (ValueError, TypeError) as exc:
            print(f"observer: ignoring malformed review request: {exc}", flush=True)
            return
        if item.item_id in approved:
            return
        approved.add(item.item_id)
        asyncio.create_task(_approve(item))

    return _handle


async def _inject_feedback(transport, args: argparse.Namespace) -> None:
    """Publish one snooze feedback event, proving the recorder round-trip.

    The resident's feedback recorder must consume it, persist an episode, and
    publish feedback.preference_updated — which the capture then contains.
    """
    await asyncio.sleep(args.inject_feedback_after)
    from sleipnir.domain.catalog import feedback_recorded

    event = feedback_recorded(
        environment_id=args.feedback_environment,
        target_event_id="proof-judgment-1",
        feedback_type="snooze",
        rating="",
        notes="Operator snoozed during the proof window.",
        source="valkyrie-proof-observer",
    )
    await transport.publish(event)
    print(f"observer: injected snooze feedback for {args.feedback_environment}", flush=True)


class _JsonlSink:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def _event_record(event, *, observed_subject: str = "") -> dict:
    record = event.to_dict()
    if observed_subject:
        record["observed_subject"] = observed_subject
    return record


async def _run_nng(args: argparse.Namespace, sink: _JsonlSink) -> None:
    import yaml

    from sleipnir.adapters.nng_transport import NngTransport

    cluster = yaml.safe_load(Path(args.cluster_file).read_text(encoding="utf-8")) or {}
    peer_addresses = [
        peer["pub_address"] for peer in cluster.get("peers", []) if peer.get("pub_address")
    ]
    if not peer_addresses:
        raise SystemExit(f"no peers with pub_address in {args.cluster_file}")

    transport = NngTransport(
        address=args.own_address,
        service_id="valkyrie-proof-observer",
        peer_addresses=peer_addresses,
    )
    await transport.start()

    async def _handle(event) -> None:
        sink.write(_event_record(event))

    await transport.subscribe(["*"], _handle)
    if args.approve_reviews:
        await transport.subscribe(["odin.review.requested"], _make_review_approver(transport, args))
        print("observer: acting as the approving operator", flush=True)
    print(f"observer: nng subscribed to {len(peer_addresses)} peers", flush=True)
    if args.inject_feedback_after > 0:
        asyncio.create_task(_inject_feedback(transport, args))
    await asyncio.Event().wait()


async def _run_nats(args: argparse.Namespace, sink: _JsonlSink) -> None:
    import nats

    from sleipnir.adapters.nats_transport import NatsTransport, _decode_nats_message

    # Ensure the flock fan-out stream exists before any daemon publishes to
    # flock-scoped subjects; JetStream publishes fail without a covering stream.
    client = await nats.connect(servers=[args.nats_url])
    jsm = client.jsm()
    try:
        await jsm.stream_info(FLOCK_STREAM_NAME)
    except Exception:
        from nats.js.api import StreamConfig

        await jsm.add_stream(StreamConfig(name=FLOCK_STREAM_NAME, subjects=[FLOCK_STREAM_SUBJECT]))
        print(f"observer: created stream {FLOCK_STREAM_NAME} ({FLOCK_STREAM_SUBJECT})", flush=True)

    js = client.jetstream()

    async def _on_flock_message(msg) -> None:
        event = _decode_nats_message(msg.data)
        if event is not None:
            sink.write(_event_record(event, observed_subject=msg.subject))
        await msg.ack()

    from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy

    await js.subscribe(
        FLOCK_STREAM_SUBJECT,
        stream=FLOCK_STREAM_NAME,
        cb=_on_flock_message,
        config=ConsumerConfig(
            deliver_policy=DeliverPolicy.NEW,
            ack_policy=AckPolicy.EXPLICIT,
        ),
    )

    transport = NatsTransport(
        servers=[args.nats_url],
        stream_name=args.stream_name,
        subject_prefix=args.subject_prefix,
    )
    await transport.start()

    async def _handle(event) -> None:
        sink.write(_event_record(event))

    await transport.subscribe(["*"], _handle)
    if args.approve_reviews:
        await transport.subscribe(["odin.review.requested"], _make_review_approver(transport, args))
        print("observer: acting as the approving operator", flush=True)
    print("observer: nats subscribed (main + flock.> streams)", flush=True)
    if args.inject_feedback_after > 0:
        asyncio.create_task(_inject_feedback(transport, args))
    await asyncio.Event().wait()


async def _main() -> None:
    args = _parse_args()
    sink = _JsonlSink(Path(args.out))

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    runner = _run_nng(args, sink) if args.transport == "nng" else _run_nats(args, sink)
    task = asyncio.create_task(runner)
    await stop.wait()
    task.cancel()


if __name__ == "__main__":
    asyncio.run(_main())
