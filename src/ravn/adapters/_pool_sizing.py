"""Shared connection-pool sizing for Ravn's auxiliary Postgres stores.

asyncpg's ``create_pool`` defaults to ``min_size=10``, and it opens those
connections eagerly. Ravn creates a pool per auxiliary store — checkpoints,
valkyrie history, the review queue — so leaving the size implicit meant one
Ravn process took ~70 connections and held them idle. On 2026-08-08 that was
69 of the shared Postgres's 100 slots, and a Ting rollout could not start:
``remaining connection slots are reserved for roles with the SUPERUSER
attribute``.

These stores are low-traffic and write-on-event, so a small pool is ample.
They are sized to match ``PostgresMemoryStore``'s existing defaults rather
than invent a second number.
"""

from __future__ import annotations

#: Opened eagerly per pool, so keep it at one connection.
AUX_POOL_MIN_SIZE = 1
#: Ceiling per auxiliary store; several of these share one Postgres.
AUX_POOL_MAX_SIZE = 5
