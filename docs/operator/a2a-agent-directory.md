# A2A Agent Directory

The Agent Directory makes A2A-addressable entities in the existing Observatory topology
searchable without creating a second inventory. Local Observatory instances project their
discovery records; Guild fans out across the registered Observatory instances visible to the
caller.

## Protocol compatibility

Compatibility was rechecked on 2026-07-14 against the official A2A materials. The current
stable protocol release is 1.0.1, and `a2a-sdk` 1.1 implements the 1.0 protocol. The A2A
discovery documentation explicitly leaves curated registries to deployments, so these REST
directory endpoints are Niuu-specific. Agent Cards, supported interfaces, cache validators,
security declarations, and JWS signatures retain their official A2A semantics.

- [A2A discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [A2A specification](https://a2a-protocol.org/latest/specification/)
- [Protocol releases](https://github.com/a2aproject/A2A/releases)
- [Python SDK releases](https://github.com/a2aproject/a2a-python/releases)

## Endpoints

Local Observatory:

- `GET /api/v1/observatory/agents`
- `GET /api/v1/observatory/agents/{agentId}`

Guild aggregate:

- `GET /api/v1/niuu/observatory/agents`
- `GET /api/v1/niuu/observatory/agents/{agentId}`

The list routes accept repeatable `skill`, `tag`, `kind`, `status`, `environmentId`,
`cluster`, and `instance` query parameters. Multiple values within one field are ANDed for
skills and tags and treated as allowed alternatives for the placement/status fields.

Responses contain `items`, source-scoped `warnings`, `sources`, `partial`, and `revision`.
An unavailable or invalid source degrades the response instead of hiding healthy sources.
Aggregate IDs are collision-safe. Identically named agents remain distinct unless both cards
have verified signatures, the same card hash, and the same verified public-key fingerprints;
merged entries retain every provenance coordinate.

## Principal isolation and card safety

Observatory filters discovery records by owner, tenant, Environment membership, and explicit
visibility before fetching a card. Guild independently rechecks owner, tenant, and visibility
after fan-out; Environment membership remains enforced by each principal-aware Observatory source.
An Environment-scoped entry without authoritative membership metadata fails closed for everyone
except its explicit owner.
Authentication headers are forwarded to registered Observatory instances. Agent Card endpoints
receive caller authentication only when their origin is explicitly trusted through
`authenticatedCardOrigins`; public and untrusted card origins receive no caller credentials.
Missing and inaccessible detail records both return the same `404` response.

Card caches are keyed by caller identity and card URL, preventing one principal's authenticated
card from being served to another. The resolver honors `Cache-Control: max-age`, `ETag`, and
`If-None-Match`; a configured TTL is used only when the card owner supplies no cache lifetime.
Cards are parsed by the official SDK. Credential-bearing card URLs, malformed interfaces,
invalid cards, and unverifiable signatures are excluded with a warning. Unsigned cards can be
listed but cannot be merged across sources.

## Publishing a workflow session

Volundr is the first production discovery source. A session is addressable only when its real
workload configuration explicitly contains an HTTP(S) Agent Card URL:

```yaml
workloadConfig:
  a2aCardUrl: https://agent.example.com/.well-known/agent-card.json
  a2aEndpointUrl: https://agent.example.com/a2a
  environmentId: environment-production
  a2aVisibility: user
```

Snake-case equivalents are accepted. Volundr exposes only those four allowlisted values; raw
workload configuration and credentials are never returned. Observatory adds `a2a` and
`a2aCard` endpoints to the session's existing topology entity, so clients associate directory
entries through `topologyNodeId` rather than duplicating the node.

## Configuration

The Observatory and Guild Helm charts expose the same `directory` settings:

```yaml
directory:
  instanceId: observatory-noatun
  cardTimeoutSeconds: 4.0
  cardCacheTtlSeconds: 300.0
  localMaxConcurrency: 8
  guildTimeoutSeconds: 5.0
  guildMaxConcurrency: 8
  signatureAlgorithms: [ES256, ES384, RS256, RS384, PS256, EdDSA]
  authenticatedCardOrigins: [https://agents.internal.example]
```

`global.niuu.cluster` supplies the deployment cluster. Guild only queries enabled,
principal-visible registered instances whose kind is `observatory`. Observatory discovery
adapters remain dynamically configured under `observatory.discovery`; no seeded agents or
special demo discovery path is used. Keep `authenticatedCardOrigins` empty unless a card service
requires caller authentication, and list only origins controlled by the deployment operator.
