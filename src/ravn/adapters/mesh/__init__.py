"""Mesh transport adapters (NIU-517).

- ``SleipnirMeshAdapter``  — transport-agnostic via Sleipnir (nng, NATS)
- ``WebhookMeshAdapter``   — HTTP-based for cross-network/serverless environments
- ``CompositeMeshAdapter`` — all-active: fans out to all transports simultaneously
"""
