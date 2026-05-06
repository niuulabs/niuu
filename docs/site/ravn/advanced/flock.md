# Flock / Mesh Networking

The Flock system enables multiple Ravn instances to discover each other, form
clusters, and communicate. The architecture separates two concerns:

- **Discovery** (WHO) — finding peers and verifying their identity
- **Mesh** (HOW) — transporting messages between verified peers

Both layers support multiple adapters running **simultaneously**. This enables
hybrid deployments where some peers are reachable via one transport and others
via another.

## Architecture Overview

```
                        ┌─────────────────────────────────────────┐
                        │              Ravn Agent                 │
                        └─────────────────────────────────────────┘
                                          │
                    ┌─────────────────────┴─────────────────────┐
                    │                                           │
              ┌─────▼─────┐                               ┌─────▼─────┐
              │ Discovery │                               │   Mesh    │
              │   Port    │                               │   Port    │
              └─────┬─────┘                               └─────┬─────┘
                    │                                           │
         ┌──────────┼──────────┐                     ┌──────────┼──────────┐
         │          │          │                     │          │          │
    ┌────▼───┐ ┌────▼───┐ ┌────▼────┐          ┌────▼────┐ ┌────▼────┐ ┌───▼───┐
    │  mDNS  │ │ Static │ │Sleipnir │          │Sleipnir │ │ Webhook │ │  ...  │
    └────────┘ └────────┘ └─────────┘          └─────────┘ └─────────┘ └───────┘
         │          │          │                     │          │
         └──────────┴──────────┘                     └──────────┘
                    │                                      │
            CompositeDiscovery                      CompositeMesh
            (all run simultaneously)              (all run simultaneously)
```

## Discovery Layer

Discovery finds and verifies other Ravn instances in the network.

| Method | Description |
|--------|-------------|
| `announce()` | Advertise presence to the network |
| `scan()` | Search for candidate peers |
| `watch(on_join, on_leave)` | Receive notifications when peers join/leave |
| `handshake(candidate)` | Verify a candidate peer's identity |
| `peers()` | Get current verified peer table |

### Discovery Adapters

| Adapter | Use Case | Mechanism |
|---------|----------|-----------|
| `mdns` | Local network (LAN, Pi mode) | Zeroconf multicast + HMAC handshake |
| `static` | Cross-network, known peers | YAML file (`cluster.yaml`) |
| `k8s` | Kubernetes in-cluster | Pod label selector via K8s API |
| `sleipnir` | Infrastructure mode | RabbitMQ pub/sub + SPIFFE JWT |

### mDNS Discovery

Uses Zeroconf to advertise `_ravn._tcp.local.` services on the local network.
Peers authenticate via HMAC handshake using a shared realm key.

Works in any environment with multicast — local networks, Docker bridge
networks, and Kubernetes with host networking or Cilium.

### Static Discovery

Reads peer definitions from a YAML file. Useful for:

- Cross-VPC / cross-cluster deployments
- Cloud functions / serverless
- Air-gapped networks
- Known peer lists

**File location**: `~/.ravn/cluster.yaml` (configurable)

**Example `cluster.yaml`**:

```yaml
peers:
  - peer_id: ravn-eu-west-1
    host: 10.0.1.50
    persona: coding-agent
    capabilities:
      - bash
      - file
      - git
    permission_mode: workspace_write
    rep_address: tcp://10.0.1.50:7481
    pub_address: tcp://10.0.1.50:7480
    webhook_url: http://10.0.1.50:7490/ravn/mesh
    consumes_event_types:
      - code.changed

  - peer_id: ravn-us-east-1
    host: 10.0.2.30
    persona: security-reviewer
    capabilities:
      - file
      - grep
    permission_mode: read_only
    webhook_url: http://10.0.2.30:7490/ravn/mesh
```

**Trust model**: Peers in the file are implicitly trusted. The file author is
the trust anchor — no handshake is performed. Secure the file appropriately.

**Hot-reload**: The adapter polls the file for changes (default: every 30s).
When the file changes, join/leave callbacks fire as needed.

### Kubernetes Discovery

Discovers peers via Kubernetes API — queries pods matching a label selector.
Trust is delegated to K8s RBAC and SPIFFE for subsequent communication.

### Sleipnir Discovery

Peers announce themselves via RabbitMQ topic exchange. Authentication uses
SPIFFE JWT-SVIDs for identity verification. Best for infrastructure deployments
with centralized message brokers.

## Mesh Layer

Mesh provides transport for inter-agent communication between verified peers.

| Method | Description |
|--------|-------------|
| `publish(event, topic)` | Broadcast to all peers subscribed to a topic |
| `subscribe(topic, handler)` | Listen for messages on a topic |
| `send(peer_id, message)` | RPC-style direct message to a specific peer |

### Mesh Adapters

| Adapter | Use Case | Protocol |
|---------|----------|----------|
| `sleipnir` | Flexible transport | nng, RabbitMQ, NATS, or Redis via Sleipnir |
| `webhook` | Cross-network / serverless | HTTP POST with HMAC signing |

### Sleipnir Mesh

Transport-agnostic adapter that delegates to Sleipnir's transport layer.
The underlying protocol is configured separately:

- `nng` — Zero-broker local mesh (Pi mode, same-host)
- `rabbitmq` — RabbitMQ topic exchanges
- `nats` — NATS pub/sub
- `redis` — Redis Streams

### Webhook Mesh

HTTP-based transport for environments where socket-based protocols don't work:

- Serverless / cloud functions
- Strict firewalls (only HTTP/443 allowed)
- Cross-cloud deployments

**Security**: All requests are signed with HMAC-SHA256. The signature covers
timestamp and path, with a 5-minute clock skew tolerance.

## Composite Adapters (All-Active Mode)

When multiple adapters are configured, they run **simultaneously** — not as a
failover chain. This is critical for hybrid deployments:

**Discovery**: `CompositeDiscoveryAdapter`
- `announce()` — announces on ALL backends
- `scan()` — returns union of all candidates (deduplicated)
- `peers()` — merges peer tables from all backends
- Callbacks fire once per unique peer (first backend to see a peer triggers join)

**Mesh**: `CompositeMeshAdapter`
- `publish()` — fans out to ALL transports concurrently
- `subscribe()` — registers handler on ALL transports
- `send()` — tries transports in config order until one succeeds

## Configuration

### List-Based Config (Recommended)

The modern config uses `adapters: list[dict]` for both discovery and mesh.
Each entry specifies an adapter class and its kwargs:

```yaml
discovery:
  adapters:
    # mDNS for local network peers
    - adapter: mdns
      handshake_port: 7482
      service_type: "_ravn._tcp.local."

    # Static file for known remote peers
    - adapter: static
      cluster_file: ~/.ravn/cluster.yaml
      poll_interval_s: 30

  # Common settings (passed to all adapters)
  heartbeat_interval_s: 30.0
  peer_ttl_s: 90.0

mesh:
  enabled: true
  adapters:
    # Sleipnir with nng for local peers
    - adapter: sleipnir
      transport: nng

    # Webhook for remote peers behind firewalls
    - adapter: webhook
      listen_port: 7490
      secret: "${RAVN_MESH_SECRET}"

  # Common settings
  rpc_timeout_s: 10.0
  own_peer_id: ""  # auto: hostname
```

### Adapter Aliases

Short names are supported for convenience:

| Alias | Full Class Path |
|-------|-----------------|
| `mdns` | `ravn.adapters.discovery.mdns.MdnsDiscoveryAdapter` |
| `static` | `ravn.adapters.discovery.static.StaticDiscoveryAdapter` |
| `k8s` | `ravn.adapters.discovery.k8s.K8sDiscoveryAdapter` |
| `sleipnir` | `ravn.adapters.discovery.sleipnir.SleipnirDiscoveryAdapter` |
| `webhook` | `ravn.adapters.mesh.webhook.WebhookMeshAdapter` |

### Custom Adapters

To add a custom adapter, specify the full class path:

```yaml
discovery:
  adapters:
    - adapter: mycompany.discovery.ConsulDiscoveryAdapter
      consul_url: http://consul.service:8500
      datacenter: us-east-1
```

The adapter class must implement the `DiscoveryPort` or `MeshPort` protocol
and accept `**kwargs` in its constructor.

### Legacy Config (Deprecated)

Single-adapter config is still supported for backward compatibility:

```yaml
discovery:
  adapter: mdns  # single adapter name
  mdns:
    handshake_port: 7482

mesh:
  adapter: nng   # single adapter name
  nng:
    pub_sub_address: "ipc:///tmp/ravn-mesh.ipc"
```

## Security

| Discovery | Authentication |
|-----------|---------------|
| mDNS | HMAC handshake (shared `realm_key`) |
| Static | Implicit trust (file-based) |
| K8s | Service account + namespace isolation |
| Sleipnir | SPIFFE JWT verification |

| Mesh | Security |
|------|----------|
| Sleipnir/nng | Realm key HMAC |
| Sleipnir/RabbitMQ | TLS + AMQP auth |
| Webhook | HMAC-SHA256 request signing |

## CLI Commands

Inspect the flock with `ravn peers`:

```bash
# Quick peer list
ravn peers

# Detailed info including addresses and latency
ravn peers --verbose

# Force a fresh network scan
ravn peers --scan

# Combined
ravn peers -v --scan
```

## When to Use What

| Scenario | Discovery | Mesh |
|----------|-----------|------|
| Local dev (same machine) | mdns | sleipnir/nng |
| LAN / Pi cluster | mdns | sleipnir/nng |
| Kubernetes (same cluster) | k8s + sleipnir | sleipnir/rabbitmq |
| Cross-cluster / cross-VPC | static | webhook |
| Hybrid (local + remote) | mdns + static | sleipnir + webhook |
| Serverless / Lambda | static | webhook |

## Related

- [Configuration Reference](../configuration/reference.md#mesh) — full `mesh.*` and `discovery.*` fields
- [Sleipnir Transports](../operations/sleipnir-transports.md) — transport layer details
- [NIU-517](https://linear.app/niuulabs/issue/NIU-517) — Mesh implementation
- [NIU-538](https://linear.app/niuulabs/issue/NIU-538) — Discovery implementation
