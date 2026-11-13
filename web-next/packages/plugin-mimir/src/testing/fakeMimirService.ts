/**
 * A hand-built fake `IMimirService`, independent of `adapters/mock.ts`
 * (out of scope — an adapter, owned separately, and `getLiveActivity` is
 * not implemented there yet). Used by the Memory Explore
 * (`src/ui/memory/MemoryExploreView*`) test suite via
 * `renderWithMimir(ui, service)`.
 *
 * Fixture data is deliberately small and internally consistent — every
 * panel's tests draw on it, not per-test ad-hoc mocks, so cross-panel
 * behaviour (e.g. a node reachable from both the graph and search) stays
 * coherent.
 */
import type { IMimirService } from '../ports';
import type { Mount } from '@niuulabs/domain';
import type { Page, PageMeta, SearchResult } from '../domain/page';
import type { MimirGraph, KnowledgeGraph, GraphNode, LiveActivity } from '../domain/api-types';
import type { Source } from '../domain/source';
import type { QueryStats } from '../domain/analytics';
import { toPageMeta } from '../domain/page';
import { encodeNodeId } from '../domain/graphIndex';

export const FAKE_MOUNTS: Mount[] = [
  {
    name: 'platform',
    role: 'domain',
    host: 'platform-kb.niuu.world',
    url: 'https://platform-kb.niuu.world',
    priority: 3,
    categories: null,
    status: 'healthy',
    pages: 3,
    sources: 6,
    lastWrite: '2026-04-19T14:02:00Z',
    embedding: 'all-minilm-l6-v2',
    sizeKb: 512,
    desc: 'Platform-scoped domain knowledge',
  },
  {
    name: 'shared',
    role: 'shared',
    host: 'kb.niuu.world',
    url: 'https://kb.niuu.world',
    priority: 5,
    categories: null,
    status: 'healthy',
    pages: 2,
    sources: 3,
    lastWrite: '2026-04-18T09:00:00Z',
    embedding: 'all-mpnet-base-v2',
    sizeKb: 256,
    desc: 'Realm-wide shared knowledge base',
  },
];

export const FAKE_PAGES: Page[] = [
  {
    path: '/platform/gateway-routing',
    title: 'Gateway routing on ymir',
    summary:
      'Every route into the cluster has to be declared in the Cedar gateway table. A route nobody declared dies at the sidecar with an empty 403.',
    category: 'infra',
    type: 'topic',
    confidence: 'high',
    mounts: ['platform'],
    updatedAt: '2026-04-19T14:02:00Z',
    updatedBy: 'ravn-muninn',
    sourceIds: ['src-1', 'src-2', 'src-3'],
    related: [
      '/platform/cedar-authorization',
      '/platform/guilds-gateway',
      '/shared/openbao-policy',
    ],
    size: 1200,
    zones: [
      {
        kind: 'key-facts',
        items: [
          'The route tables live in values-cedar.yaml, which wins over values-niuu.yaml.',
          'Helm replaces lists, so the chart defaults must be repeated.',
          'Guild has no gateway of its own.',
        ],
      },
    ],
  },
  {
    path: '/platform/cedar-authorization',
    title: 'Cedar authorization',
    summary: 'Every route needs a Cedar policy entry as well as a table entry.',
    category: 'infra',
    type: 'directive',
    confidence: 'medium',
    mounts: ['platform'],
    updatedAt: '2026-04-16T09:00:00Z',
    updatedBy: 'ravn-muninn',
    sourceIds: ['src-2'],
    related: ['/platform/gateway-routing'],
    size: 600,
    zones: [
      {
        kind: 'key-facts',
        items: ['Every route needs a Cedar policy entry as well as a table entry.'],
      },
    ],
  },
  {
    path: '/platform/guilds-gateway',
    title: "Guild's gateway",
    summary: 'Guild has no gateway of its own; it routes through the shared sidecar.',
    category: 'infra',
    type: 'topic',
    confidence: 'low',
    mounts: ['platform'],
    updatedAt: '2026-03-10T09:00:00Z',
    updatedBy: 'ravn-ivaldi',
    sourceIds: ['src-4'],
    related: ['/platform/gateway-routing'],
    size: 300,
    zones: [{ kind: 'key-facts', items: ['Guild has no gateway of its own.'] }],
  },
  {
    path: '/shared/openbao-policy',
    title: 'OpenBao policy',
    summary: 'A new path the policy does not know fails the same way, at startup.',
    category: 'security',
    type: 'decision',
    confidence: 'high',
    mounts: ['shared'],
    updatedAt: '2026-04-09T09:00:00Z',
    updatedBy: 'ravn-regin',
    sourceIds: ['src-5', 'src-6'],
    related: ['/platform/gateway-routing', '/shared/volundr'],
    size: 500,
    zones: [
      {
        kind: 'key-facts',
        items: ['A new path the policy does not know fails the same way, at startup.'],
      },
    ],
  },
  {
    path: '/shared/volundr',
    title: 'Volundr',
    summary: 'Forge backend — session lifecycle, workspaces, chronicles.',
    category: 'component',
    type: 'entity',
    entityType: 'component',
    confidence: 'high',
    mounts: ['shared'],
    updatedAt: '2026-04-18T18:00:00Z',
    updatedBy: 'ravn-regin',
    sourceIds: ['src-6'],
    related: ['/shared/openbao-policy'],
    size: 400,
    zones: [],
  },
];

const FIRST_SEEN: Record<string, string> = {
  '/platform/gateway-routing': '2026-03-01T08:00:00Z',
  '/platform/cedar-authorization': '2026-03-01T08:30:00Z',
  '/platform/guilds-gateway': '2026-03-05T10:00:00Z',
  '/shared/openbao-policy': '2026-03-10T11:00:00Z',
  '/shared/volundr': '2026-03-10T11:30:00Z',
};

/**
 * The node id the fixture graph gives a (mount, path) pair — built the same
 * way the backend does (`domain/graphIndex.ts`'s `encodeNodeId`), so tests
 * that assert on "the id passed to `onFocus`" exercise the real, opaque,
 * mount-qualified id rather than a bare path.
 */
export function fakeNodeId(mount: string, path: string): string {
  return encodeNodeId(mount, path);
}

export const FAKE_GRAPH: MimirGraph = {
  nodes: FAKE_PAGES.map((p) => {
    const mount = p.mounts[0]!;
    return {
      id: fakeNodeId(mount, p.path),
      title: p.title,
      category: p.category,
      path: p.path,
      kind: p.type,
      summary: p.summary,
      mount,
      updatedAt: p.updatedAt,
      firstSeen: FIRST_SEEN[p.path]!,
      confidence: p.confidence,
    };
  }),
  edges: [
    {
      source: fakeNodeId('platform', '/platform/gateway-routing'),
      target: fakeNodeId('platform', '/platform/cedar-authorization'),
      relation: 'depends_on',
    },
    {
      source: fakeNodeId('platform', '/platform/gateway-routing'),
      target: fakeNodeId('platform', '/platform/guilds-gateway'),
      relation: 'contradicts',
    },
    {
      source: fakeNodeId('platform', '/platform/gateway-routing'),
      target: fakeNodeId('shared', '/shared/openbao-policy'),
      relation: 'same_failure',
    },
    {
      source: fakeNodeId('shared', '/shared/openbao-policy'),
      target: fakeNodeId('shared', '/shared/volundr'),
      relation: 'routes_for',
    },
  ],
};

const DAY_MS = 24 * 60 * 60 * 1000;

/** An ISO time `days` before the moment this module loads — keeps "this week" true whenever the tests run. */
function daysAgo(days: number): string {
  return new Date(Date.now() - days * DAY_MS).toISOString();
}

export const BOOKING_LAYER = 'project:booking';

function knowledgeNode(
  layer: string,
  path: string,
  title: string,
  kind: string,
  dates: { firstSeen: number; changed: number | null },
  summary = '',
): GraphNode {
  return {
    id: `${layer}/${path}`,
    title,
    category: path.split('/')[0]!,
    path,
    kind,
    summary,
    mount: layer,
    layer,
    updatedAt: daysAgo(dates.changed ?? dates.firstSeen),
    firstSeen: daysAgo(dates.firstSeen),
    changedAt: dates.changed === null ? null : daysAgo(dates.changed),
    confidence: null,
  };
}

/** Node ids of the knowledge-graph fixture, named for the tests that use them. */
export const KG = {
  cutover: `${BOOKING_LAYER}/projects/booking-cutover`,
  ines: 'org/people/ines-moreau',
  lin482: `${BOOKING_LAYER}/sources/lin-482`,
  lin501: `${BOOKING_LAYER}/sources/lin-501`,
  apiV2: `${BOOKING_LAYER}/services/booking-api-v2`,
  legacy: `${BOOKING_LAYER}/services/legacy-booking-api`,
  freeze: `${BOOKING_LAYER}/decisions/payments-freeze`,
  runbook: `${BOOKING_LAYER}/runbooks/rollback`,
  ledger: 'org/pages/payments-ledger',
  ola: 'org/people/ola-nordmann',
} as const;

/**
 * The knowledge graph as `GET /memory/graph` (contract G1) shapes it, after
 * the adapter's mapping: the booking cut-over decision with its owner,
 * tickets and services, one change this week, and a page two hops out.
 */
export const FAKE_KNOWLEDGE_GRAPH: KnowledgeGraph = {
  layers: [
    { id: 'org', name: 'Org' },
    { id: BOOKING_LAYER, name: 'Booking platform' },
  ],
  nodes: [
    knowledgeNode(
      BOOKING_LAYER,
      'projects/booking-cutover',
      'Booking cut-over',
      'decision',
      { firstSeen: 9, changed: 0 },
      'Bookings move from the legacy API to booking-api v2.',
    ),
    knowledgeNode('org', 'people/ines-moreau', 'Ines Moreau', 'person', {
      firstSeen: 60,
      changed: null,
    }),
    knowledgeNode(BOOKING_LAYER, 'sources/lin-482', 'LIN-482', 'ticket', {
      firstSeen: 9,
      changed: null,
    }),
    knowledgeNode(BOOKING_LAYER, 'sources/lin-501', 'LIN-501', 'ticket', {
      firstSeen: 0,
      changed: null,
    }),
    knowledgeNode(BOOKING_LAYER, 'services/booking-api-v2', 'booking-api v2', 'service', {
      firstSeen: 40,
      changed: 20,
    }),
    knowledgeNode(BOOKING_LAYER, 'services/legacy-booking-api', 'Legacy booking API', 'service', {
      firstSeen: 90,
      changed: 30,
    }),
    knowledgeNode(BOOKING_LAYER, 'decisions/payments-freeze', 'Payments freeze', 'decision', {
      firstSeen: 25,
      changed: 25,
    }),
    knowledgeNode(BOOKING_LAYER, 'runbooks/rollback', 'Rollback runbook', 'runbook', {
      firstSeen: 15,
      changed: 15,
    }),
    knowledgeNode('org', 'pages/payments-ledger', 'Payments ledger', 'page', {
      firstSeen: 70,
      changed: 50,
    }),
    knowledgeNode('org', 'people/ola-nordmann', 'Ola Nordmann', 'person', {
      firstSeen: 80,
      changed: null,
    }),
  ],
  edges: [
    { source: KG.ines, target: KG.cutover, relation: 'owns', since: daysAgo(30).slice(0, 10) },
    {
      source: KG.cutover,
      target: KG.lin482,
      relation: 'planned in',
      since: daysAgo(9).slice(0, 10),
    },
    { source: KG.lin501, target: KG.cutover, relation: 'moved', since: daysAgo(0).slice(0, 10) },
    { source: KG.cutover, target: KG.apiV2, relation: 'changes', since: null },
    { source: KG.cutover, target: KG.legacy, relation: 'replaces', since: null },
    { source: KG.cutover, target: KG.freeze, relation: 'waits for', since: null },
    { source: KG.cutover, target: KG.runbook, relation: 'covered by', since: null },
    { source: KG.freeze, target: KG.ledger, relation: 'mentions', since: null },
  ],
  unresolved: [{ source: KG.cutover, targetSlug: 'projects/old-plan' }],
};

export const FAKE_SOURCES: Source[] = [
  {
    id: 'src-1',
    title: 'values-cedar.yaml',
    originType: 'file',
    originPath: 'infra/values-cedar.yaml',
    ingestedAt: '2026-03-01T08:00:00Z',
    ingestAgent: 'ravn-muninn',
    compiledInto: ['/platform/gateway-routing'],
    content: '',
  },
];

export const FAKE_QUERY_STATS: QueryStats = {
  total: 42,
  zeroResultCount: 1,
  recent: [
    { ts: '2026-04-19T10:14:02Z', query: 'why do new routes 403 on ymir?', resultCount: 3 },
    { ts: '2026-04-19T09:54:31Z', query: 'what changed this week?', resultCount: 2 },
    { ts: '2026-04-19T09:22:10Z', query: 'what does Regin know about JetStream?', resultCount: 1 },
    { ts: '2026-04-18T08:12:55Z', query: "nothing on guild's own routing table", resultCount: 0 },
  ],
};

export const FAKE_LIVE_ACTIVITY: LiveActivity[] = [
  {
    id: 'live-1',
    timestamp: '2026-04-19T14:00:00Z',
    kind: 'read',
    mount: 'platform',
    path: '/platform/gateway-routing',
    actor: 'muninn',
  },
  {
    id: 'live-2',
    timestamp: '2026-04-19T13:58:00Z',
    kind: 'write',
    mount: 'shared',
    path: '/shared/openbao-policy',
    actor: null,
  },
];

export interface FakeMimirOverrides {
  pages?: Page[];
  graph?: MimirGraph;
  knowledgeGraph?: KnowledgeGraph;
  mounts?: Mount[];
  queryStats?: QueryStats;
  liveActivity?: LiveActivity[];
  /** Force ingestUrl/ingestFile to reject, to test the error path. */
  ingestShouldFail?: boolean;
}

export function createFakeMimirService(overrides: FakeMimirOverrides = {}): IMimirService {
  const pages = overrides.pages ?? FAKE_PAGES;
  const graph = overrides.graph ?? FAKE_GRAPH;
  const knowledgeGraph = overrides.knowledgeGraph ?? FAKE_KNOWLEDGE_GRAPH;
  const mounts = overrides.mounts ?? FAKE_MOUNTS;
  const queryStats = overrides.queryStats ?? FAKE_QUERY_STATS;
  const liveActivity = overrides.liveActivity ?? FAKE_LIVE_ACTIVITY;
  let sources = [...FAKE_SOURCES];

  return {
    mounts: {
      async listMounts() {
        return mounts;
      },
      async getRecentWrites() {
        return [];
      },
      async getEvalReport() {
        return null;
      },
      async getQueryStats() {
        return queryStats;
      },
    },
    pages: {
      async getStats() {
        return {
          pageCount: pages.length,
          categories: [...new Set(pages.map((p) => p.category))],
          healthy: true,
        };
      },
      async listPages(options): Promise<PageMeta[]> {
        let filtered = pages;
        if (options?.mountName)
          filtered = filtered.filter((p) => p.mounts.includes(options.mountName!));
        if (options?.category) filtered = filtered.filter((p) => p.category === options.category);
        return filtered.map(toPageMeta);
      },
      async getPage(path, mountName) {
        return (
          pages.find((p) => p.path === path && (!mountName || p.mounts.includes(mountName))) ?? null
        );
      },
      async upsertPage() {
        // no-op in fake
      },
      async search(query, _mode, mountName): Promise<SearchResult[]> {
        const q = query.toLowerCase();
        return pages
          .filter(
            (p) =>
              (p.title.toLowerCase().includes(q) || p.summary.toLowerCase().includes(q)) &&
              (!mountName || p.mounts.includes(mountName)),
          )
          .map((p, i) => ({
            path: p.path,
            title: p.title,
            summary: p.summary,
            category: p.category,
            type: p.type,
            confidence: p.confidence,
            score: Math.max(0.5, 0.95 - i * 0.1),
            mounts: p.mounts,
          }));
      },
      async listSources(options) {
        let filtered = sources;
        if (options?.originType)
          filtered = filtered.filter((s) => s.originType === options.originType);
        return filtered;
      },
      async getPageSources(path) {
        const page = pages.find((p) => p.path === path);
        if (!page) return [];
        return sources.filter((s) => page.sourceIds.includes(s.id));
      },
      async ingestUrl(url) {
        if (overrides.ingestShouldFail) throw new Error('ingest failed: fetch refused');
        const source: Source = {
          id: `src-${Date.now()}`,
          title: url,
          originType: 'web',
          originUrl: url,
          ingestedAt: new Date().toISOString(),
          ingestAgent: 'ravn-muninn',
          compiledInto: [],
          content: '',
        };
        sources = [source, ...sources];
        return source;
      },
      async ingestFile(file) {
        if (overrides.ingestShouldFail) throw new Error('ingest failed: unreadable file');
        const source: Source = {
          id: `src-${Date.now()}`,
          title: file.name,
          originType: 'file',
          originPath: file.name,
          ingestedAt: new Date().toISOString(),
          ingestAgent: 'ravn-muninn',
          compiledInto: [],
          content: '',
        };
        sources = [source, ...sources];
        return source;
      },
      async getGraph(options) {
        if (!options?.mountName) return graph;
        const nodeIds = new Set(
          graph.nodes.filter((n) => n.mount === options.mountName).map((n) => n.id),
        );
        return {
          nodes: graph.nodes.filter((n) => nodeIds.has(n.id)),
          edges: graph.edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target)),
        };
      },
      async getKnowledgeGraph(options) {
        const layers = options?.layers ?? [];
        if (layers.length === 0) return knowledgeGraph;
        const wanted = new Set(layers);
        const nodes = knowledgeGraph.nodes.filter((n) => wanted.has(n.layer!));
        const ids = new Set(nodes.map((n) => n.id));
        return {
          layers: knowledgeGraph.layers.filter((l) => wanted.has(l.id)),
          nodes,
          edges: knowledgeGraph.edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
          unresolved: knowledgeGraph.unresolved.filter((u) => ids.has(u.source)),
        };
      },
      async listEntities() {
        return [];
      },
      async getLiveActivity(options) {
        if (!options?.since) return liveActivity;
        return liveActivity.filter((a) => a.timestamp > options.since!);
      },
    },
    embeddings: {
      async semanticSearch() {
        return [];
      },
    },
  };
}
