/**
 * Deterministic synthetic memory graph for the mock Mímir adapter.
 *
 * The hand-written `MOCK_PAGES` in `mock.ts` are a handful of hero pages —
 * enough to exercise pages/search/lint views, not enough to make the 3D
 * memory scene look like a real knowledge base with hundreds of pages and
 * thousands of links. This module fills that in: a seeded, reproducible
 * population of pages spread across the mock mounts and the full memory-kind
 * vocabulary, linked into loose clusters with typed edges.
 *
 * Pure and deterministic — same seed, same `now`, same graph every time —
 * so adapter tests and the layout/scene tests that consume it can assert on
 * shape without re-running the generator.
 */

import type { GraphEdge, GraphNode, LiveActivity } from '../domain/api-types';

/**
 * The memory-kind leaf vocabulary `domain/memoryKinds.ts`'s `kindGroup()`
 * recognises. Not exported by that module (it only exports the grouping
 * function and legend metadata), so this generator keeps its own copy of
 * the leaf values it needs to produce a representative spread.
 */
export type MemoryKind =
  | 'topic'
  | 'person'
  | 'project'
  | 'concept'
  | 'technology'
  | 'organization'
  | 'strategy'
  | 'decision'
  | 'directive'
  | 'preference'
  | 'goal'
  | 'observation'
  | 'thread';

export const ENTITY_MEMORY_KINDS: readonly MemoryKind[] = [
  'person',
  'project',
  'concept',
  'technology',
  'organization',
  'strategy',
];

export const MEMORY_KINDS: readonly MemoryKind[] = [
  'topic',
  ...ENTITY_MEMORY_KINDS,
  'decision',
  'directive',
  'preference',
  'goal',
  'observation',
  'thread',
];

// ---------------------------------------------------------------------------
// Seeded PRNG — mulberry32. Small, fast, and deterministic for a given seed.
// ---------------------------------------------------------------------------

export type Rng = () => number;

export function mulberry32(seed: number): Rng {
  let a = seed >>> 0;
  return function next(): number {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pick<T>(rng: Rng, items: readonly T[]): T {
  const value = items[Math.floor(rng() * items.length)];
  if (value === undefined) throw new Error('pick from empty array');
  return value;
}

function intBetween(rng: Rng, min: number, max: number): number {
  return Math.floor(min + rng() * (max - min + 1));
}

// ---------------------------------------------------------------------------
// Word banks — small, combinatorial, deterministic titles per kind.
// ---------------------------------------------------------------------------

const ADJ = [
  'Nimbus',
  'Ember',
  'Quartz',
  'Halcyon',
  'Rune',
  'Drift',
  'Ochre',
  'Solace',
  'Vantage',
  'Briar',
  'Cinder',
  'Wraith',
  'Tundra',
  'Marrow',
  'Glint',
  'Thistle',
  'Vesper',
  'Amber',
  'Fallow',
  'Cobalt',
];

const NOUN = [
  'Gateway',
  'Ledger',
  'Pipeline',
  'Beacon',
  'Archive',
  'Signal',
  'Anchor',
  'Conduit',
  'Mesh',
  'Vault',
  'Circuit',
  'Harbor',
  'Relay',
  'Compass',
  'Foundry',
  'Lattice',
  'Current',
  'Spindle',
  'Wharf',
  'Cipher',
];

const PERSON_FIRST = [
  'Astrid',
  'Kenji',
  'Priya',
  'Mateo',
  'Soren',
  'Naledi',
  'Ilse',
  'Oskar',
  'Lior',
  'Wren',
  'Dmitri',
  'Amara',
  'Tobias',
  'Yuki',
  'Marisol',
  'Finn',
  'Zara',
  'Hassan',
  'Elin',
  'Bram',
];

const PERSON_LAST = [
  'Voss',
  'Adeyemi',
  'Larsson',
  'Kowalski',
  'Okafor',
  'Reyes',
  'Lindqvist',
  'Tanaka',
  'Murtagh',
  'Silveira',
  'Berger',
  'Nakagawa',
  'Osei',
  'Falk',
  'Iversen',
];

const ORG_WORD = [
  'Northwind',
  'Arcadia',
  'Meridian',
  'Solstice',
  'Granite',
  'Ironwood',
  'Harborlight',
  'Fenwick',
  'Cairnmoor',
  'Duskvale',
];

const ORG_SUFFIX = [
  'Labs',
  'Collective',
  'Systems',
  'Guild',
  'Works',
  'Union',
  'Foundry',
  'Alliance',
];

const TECH_WORD = [
  'Postgres',
  'Envoy',
  'Kubernetes',
  'Sleipnir',
  'Bifrost',
  'React',
  'Vite',
  'asyncpg',
  'Kafka',
  'Redis',
  'Terraform',
  'OpenTelemetry',
  'gRPC',
  'WebSockets',
  'TanStack',
  'Tailwind',
  'Docker',
  'Helm',
  'NATS',
  'DuckDB',
];

const TOPIC_CATEGORY = [
  'arch',
  'api',
  'infra',
  'observability',
  'deploy',
  'security',
  'runbooks',
  'onboarding',
  'testing',
  'release',
];

function titleFor(kind: MemoryKind, rng: Rng): string {
  switch (kind) {
    case 'person':
      return `${pick(rng, PERSON_FIRST)} ${pick(rng, PERSON_LAST)}`;
    case 'project':
      return `${pick(rng, ADJ)} ${pick(rng, NOUN)}`;
    case 'concept':
      return `${pick(rng, NOUN)} Theory`;
    case 'technology':
      return pick(rng, TECH_WORD);
    case 'organization':
      return `${pick(rng, ORG_WORD)} ${pick(rng, ORG_SUFFIX)}`;
    case 'strategy':
      return `${pick(rng, ADJ)} strategy for ${pick(rng, NOUN)}`;
    case 'decision':
      return `Adopt ${pick(rng, ADJ)} ${pick(rng, NOUN)}`;
    case 'directive':
      return `Always confirm ${pick(rng, NOUN)} before ${pick(rng, NOUN)}`;
    case 'preference':
      return `Prefer ${pick(rng, NOUN)} over ${pick(rng, NOUN)}`;
    case 'goal':
      return `Ship ${pick(rng, ADJ)} ${pick(rng, NOUN)} by Q${intBetween(rng, 1, 4)}`;
    case 'observation':
      return `${pick(rng, NOUN)} tends to drive ${pick(rng, NOUN)}`;
    case 'thread':
      return `Thread: ${pick(rng, NOUN)} vs ${pick(rng, NOUN)}`;
    case 'topic':
    default:
      return `${pick(rng, ADJ)} ${pick(rng, NOUN)}`;
  }
}

function categoryFor(kind: MemoryKind, rng: Rng): string {
  if ((ENTITY_MEMORY_KINDS as readonly string[]).includes(kind)) return kind;
  return pick(rng, TOPIC_CATEGORY);
}

// ---------------------------------------------------------------------------
// Edge vocabulary
// ---------------------------------------------------------------------------

/** Ordinary typed relations — the bulk of generated edges. */
export const TYPED_EDGE_KINDS = [
  'depends_on',
  'part_of',
  'explains',
  'fixed_by',
  'routes_for',
  'wikilink',
  'shared_source',
] as const;

/** Contradiction relations — dashed amber in the scene. */
export const CONTRADICTION_EDGE_KINDS = [
  'contradicts',
  'disagrees_with',
  'conflicts_with',
] as const;

const CONTRADICTION_PROBABILITY = 0.012;

const EDGES_PER_NODE_MIN = 1;
const EDGES_PER_NODE_MAX = 4;
/** Chance an edge reaches outside the node's own cluster. */
const CROSS_CLUSTER_PROBABILITY = 0.2;

// ---------------------------------------------------------------------------
// Generation
// ---------------------------------------------------------------------------

export interface GenerateMockGraphOptions {
  /** PRNG seed — same seed always produces the same graph. */
  seed: number;
  /** Target number of synthetic pages to generate. */
  count: number;
  /** Mounts to spread synthetic pages across. */
  mounts: readonly string[];
  /**
   * Existing (hand-written) nodes to weave into the same cluster/edge
   * structure, so curated pages aren't an island in the generated graph.
   */
  seedNodes: readonly GraphNode[];
  /** Reference "now" (ms since epoch) that `firstSeen`/`updatedAt` are relative to. */
  now: number;
}

export interface GeneratedMockGraph {
  /** Synthetic nodes only — callers concatenate with their own curated nodes. */
  nodes: GraphNode[];
  /** Synthetic edges, spanning both synthetic nodes and `seedNodes`. */
  edges: GraphEdge[];
}

const DAY_MS = 24 * 60 * 60 * 1000;
const FIRST_SEEN_SPREAD_DAYS = 60;

/** Generate the synthetic node population. */
function generateNodes(options: GenerateMockGraphOptions): GraphNode[] {
  const rng = mulberry32(options.seed);
  const nodes: GraphNode[] = [];

  for (let i = 0; i < options.count; i += 1) {
    const kind = pick(rng, MEMORY_KINDS);
    const mount = pick(rng, options.mounts);
    const path = `/generated/${kind}/${i.toString(36)}`;
    const firstSeenOffsetDays = rng() * FIRST_SEEN_SPREAD_DAYS;
    const firstSeenMs = options.now - firstSeenOffsetDays * DAY_MS;
    const updatedMs = firstSeenMs + rng() * Math.max(0, options.now - firstSeenMs);
    const confidenceRoll = rng();
    const confidence: GraphNode['confidence'] =
      confidenceRoll < 0.15
        ? null
        : confidenceRoll < 0.5
          ? 'high'
          : confidenceRoll < 0.85
            ? 'medium'
            : 'low';

    nodes.push({
      id: path,
      title: titleFor(kind, rng),
      category: categoryFor(kind, rng),
      path,
      mount,
      kind,
      updatedAt: new Date(updatedMs).toISOString(),
      firstSeen: new Date(firstSeenMs).toISOString(),
      confidence,
    });
  }

  return nodes;
}

/** Group node indices by `${mount}:${kind-or-category}` for clustered linking. */
function clusterKey(node: GraphNode): string {
  return `${node.mount}:${node.kind ?? node.category}`;
}

function buildClusters(allNodes: readonly GraphNode[]): Map<string, number[]> {
  const clusters = new Map<string, number[]>();
  allNodes.forEach((node, index) => {
    const key = clusterKey(node);
    const bucket = clusters.get(key);
    if (bucket) {
      bucket.push(index);
    } else {
      clusters.set(key, [index]);
    }
  });
  return clusters;
}

/** Generate clustered, typed edges across `allNodes` (synthetic + seed). */
function generateEdges(
  rng: Rng,
  allNodes: readonly GraphNode[],
  syntheticStart: number,
): GraphEdge[] {
  const clusters = buildClusters(allNodes);
  const clusterKeys = [...clusters.keys()];
  const seen = new Set<string>();
  const edges: GraphEdge[] = [];

  function edgeType(): string {
    if (rng() < CONTRADICTION_PROBABILITY) return pick(rng, CONTRADICTION_EDGE_KINDS);
    return pick(rng, TYPED_EDGE_KINDS);
  }

  function addEdge(sourceIndex: number, targetIndex: number): void {
    if (sourceIndex === targetIndex) return;
    const source = allNodes[sourceIndex]!.id;
    const target = allNodes[targetIndex]!.id;
    const key = `${source}->${target}`;
    const reverseKey = `${target}->${source}`;
    if (seen.has(key) || seen.has(reverseKey)) return;
    seen.add(key);
    edges.push({ source, target, type: edgeType() });
  }

  // Only synthetic nodes originate new edges — seed nodes may be targets,
  // keeping the curated pages' own hand-authored edges the primary source
  // of truth for their relationships while still gaining generated ones in.
  for (let i = syntheticStart; i < allNodes.length; i += 1) {
    const key = clusterKey(allNodes[i]!);
    const cluster = clusters.get(key) ?? [i];
    const edgeCount = intBetween(rng, EDGES_PER_NODE_MIN, EDGES_PER_NODE_MAX);
    for (let e = 0; e < edgeCount; e += 1) {
      const crossCluster = rng() < CROSS_CLUSTER_PROBABILITY || cluster.length < 2;
      if (crossCluster && clusterKeys.length > 1) {
        const otherKey = pick(rng, clusterKeys);
        const otherBucket = clusters.get(otherKey) ?? [i];
        addEdge(i, pick(rng, otherBucket));
      } else {
        addEdge(i, pick(rng, cluster));
      }
    }
  }

  // Guarantee at least one contradiction edge even if the random roll never
  // produced one — the scene's dashed-amber styling needs a demo case.
  if (
    !edges.some((edge) => (CONTRADICTION_EDGE_KINDS as readonly string[]).includes(edge.type ?? ''))
  ) {
    const a = syntheticStart < allNodes.length ? syntheticStart : 0;
    const b = allNodes.length > a + 1 ? a + 1 : 0;
    if (allNodes[a] && allNodes[b] && a !== b) {
      edges.push({ source: allNodes[a]!.id, target: allNodes[b]!.id, type: 'contradicts' });
    }
  }

  return edges;
}

/** Generate a deterministic, clustered synthetic memory graph. */
export function generateMockMemoryGraph(options: GenerateMockGraphOptions): GeneratedMockGraph {
  const nodes = generateNodes(options);
  const allNodes = [...options.seedNodes, ...nodes];
  const rng = mulberry32(options.seed ^ 0x9e3779b9);
  const edges = generateEdges(rng, allNodes, options.seedNodes.length);
  return { nodes, edges };
}

// ---------------------------------------------------------------------------
// Live activity
// ---------------------------------------------------------------------------

const RESIDENT_ACTORS: readonly (string | null)[] = [
  'ravn-fjolnir',
  'ravn-skald',
  'ravn-saga',
  'ravn-eir',
  'ravn-vor',
  null,
];

export interface LiveActivityTarget {
  mount: string;
  path: string;
}

/**
 * Build a few recent read/write events on real (not synthetic) mock pages,
 * relative to `now` — the mock adapter passes `Date.now()` so the scene's
 * markers look live whenever the dev app is opened.
 */
export function generateMockLiveActivity(
  targets: readonly LiveActivityTarget[],
  now: number,
  since?: string,
): LiveActivity[] {
  const rng = mulberry32(20260419);
  const sinceMs = since ? Date.parse(since) : Number.NEGATIVE_INFINITY;
  const events: LiveActivity[] = [];

  targets.forEach((target, index) => {
    const minutesAgo = intBetween(rng, 1, 45) + index * 3;
    const timestampMs = now - minutesAgo * 60_000;
    if (timestampMs < sinceMs) return;
    events.push({
      id: `mock-activity-${index}`,
      timestamp: new Date(timestampMs).toISOString(),
      kind: rng() < 0.5 ? 'read' : 'write',
      mount: target.mount,
      path: target.path,
      actor: pick(rng, RESIDENT_ACTORS),
    });
  });

  return events.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
}
