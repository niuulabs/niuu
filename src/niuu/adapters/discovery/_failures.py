"""How a discovery adapter reports what went wrong, loudly and counted.

Discovery is how a session is seen: a failed announce means peers stop seeing it, a
failed scan means a peer table that is wrong without saying so. Nothing here is logged at
debug and dropped. The rules each adapter follows, by kind of failure:

* **Configured but impossible** (a missing library, a transport that will not connect,
  a configured file that is absent) raises :class:`DiscoveryError` from ``start()``, with the
  remedy. A session never runs with discovery it was told to have and does not.
* **A background loop's iteration fails** (a heartbeat that could not be published, a scan
  that could not list) cannot raise into nothing, and stopping the loop would be worse than
  one failed turn: it is reported with :func:`report_failure` (error log, counted on
  ``ravn.degraded`` under ``niuu.discovery.<adapter>.<operation>``) and the loop tries
  again at the next interval. A failed list is never read as "nobody is there".
* **A callback of a consumer raises** (``on_join`` / ``on_leave``): the peer table is already
  updated and the other callbacks still run; the failure is reported, not swallowed.
* **A message from another peer is bad** (malformed, an identity it cannot prove, a handshake
  that fails after its realm matched): it is refused, logged at error and counted on
  ``niuu.discovery.refusals`` by adapter and reason (:func:`refuse_message`). It does not
  raise: a deterministic refusal would fail the same on redelivery, and one peer's bad
  message must not stop the consumer for every other peer.
* **Expected steady states** are not failures and stay quiet: an announcement from another
  realm, our own announcement echoed back.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from threading import Lock
from typing import Any

from niuu.cap_telemetry import record_degraded
from niuu.observability import get_observability

logger = logging.getLogger("niuu.discovery")

COUNTER_REFUSALS = "niuu.discovery.refusals"

_lock = Lock()
_refusals: Counter[str] = Counter()


class DiscoveryError(RuntimeError):
    """Configured discovery cannot run; the message says why and what to change."""


def report_failure(adapter: str, operation: str, message: str) -> None:
    """A failure of a step that has to carry on: error log and a count, never a debug line."""
    record_degraded(f"niuu.discovery.{adapter}.{operation}", message)


def refuse_message(adapter: str, reason: str, sender: str, message: str) -> None:
    """A message from another peer is refused: logged at error and counted by reason."""
    with _lock:
        _refusals[f"{adapter}.{reason}"] += 1
    logger.error(
        "%s discovery refused a message from %r (%s): %s", adapter, sender, reason, message
    )
    get_observability().count(
        COUNTER_REFUSALS,
        attributes={"adapter": adapter, "reason": reason, "sender": sender or "unknown"},
        description="Discovery messages refused because they were malformed or unverifiable.",
    )


def refusal_counts() -> dict[str, int]:
    """Refusals so far, keyed ``<adapter>.<reason>`` (for health surfaces and tests)."""
    with _lock:
        return dict(_refusals)


def reset_refusal_counts() -> None:
    with _lock:
        _refusals.clear()


def notify(adapter: str, event: str, callbacks: Iterable[Any], peer: Any) -> None:
    """Call every ``on_join`` / ``on_leave`` callback; one that raises is reported, the rest run."""
    for callback in callbacks:
        try:
            callback(peer)
        except Exception as exc:  # a consumer's callback: its failure is its own, but not silent
            report_failure(
                adapter,
                event,
                f"the {event} callback for peer {getattr(peer, 'peer_id', '?')} raised "
                f"{type(exc).__name__}: {exc}",
            )
