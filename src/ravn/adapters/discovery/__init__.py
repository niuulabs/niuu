"""Discovery adapters — flock peer detection (NIU-538).

- ``MdnsDiscoveryAdapter``      — zeroconf multicast for LAN environments
- ``K8sDiscoveryAdapter``       — Kubernetes pod labels for in-cluster discovery
- ``SleipnirDiscoveryAdapter``  — RabbitMQ announce events for infra mode
- ``StaticDiscoveryAdapter``    — JSON file (cluster.json) for cross-network
- ``CompositeDiscoveryAdapter`` — merges multiple backends, all run simultaneously
"""
