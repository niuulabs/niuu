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
import type { MimirGraph, LiveActivity } from '../domain/api-types';
import type { Source } from '../domain/source';
import type { QueryStats } from '../domain/analytics';
import type { LintReport } from '../domain/lint';
import type { FactEvidence, RelatedPage } from '../domain/evidence';
import { toPageMeta } from '../domain/page';
import { tallySeverity } from '../domain/lint';

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
    lintIssues: 1,
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
    lintIssues: 0,
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

export const FAKE_GRAPH: MimirGraph = {
  nodes: FAKE_PAGES.map((p) => ({
    id: p.path,
    title: p.title,
    category: p.category,
    path: p.path,
    kind: p.type,
    summary: p.summary,
    mount: p.mounts[0],
    updatedAt: p.updatedAt,
    firstSeen: FIRST_SEEN[p.path]!,
    confidence: p.confidence,
  })),
  edges: [
    {
      source: '/platform/gateway-routing',
      target: '/platform/cedar-authorization',
      type: 'depends_on',
    },
    {
      source: '/platform/gateway-routing',
      target: '/platform/guilds-gateway',
      type: 'contradicts',
    },
    { source: '/platform/gateway-routing', target: '/shared/openbao-policy', type: 'same_failure' },
    { source: '/shared/openbao-policy', target: '/shared/volundr', type: 'routes_for' },
  ],
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

export const FAKE_LINT_REPORT: LintReport = {
  issues: [
    {
      id: 'lint-l01-1',
      rule: 'L01',
      severity: 'warn',
      page: '/platform/guilds-gateway',
      mount: 'platform',
      autoFix: false,
      message: "Guild's gateway disagrees with Gateway routing on ymir",
    },
  ],
  pagesChecked: FAKE_PAGES.length,
  summary: tallySeverity([]),
};

const FAKE_RELATED: Record<string, RelatedPage[]> = {
  '/platform/gateway-routing': [
    { path: '/platform/cedar-authorization', hop: 1, rel: 'depends_on', direction: 'out' },
    { path: '/platform/guilds-gateway', hop: 1, rel: null, direction: 'out' },
    { path: '/shared/openbao-policy', hop: 1, rel: 'same_failure', direction: 'out' },
    { path: '/shared/volundr', hop: 2, rel: 'routes_for', direction: 'out' },
  ],
  '/shared/openbao-policy': [
    { path: '/platform/gateway-routing', hop: 1, rel: 'same_failure', direction: 'in' },
    { path: '/shared/volundr', hop: 1, rel: 'routes_for', direction: 'out' },
  ],
};

const FAKE_EVIDENCE: Record<string, FactEvidence[]> = {
  '/platform/gateway-routing': [
    {
      fact: 'The route tables live in values-cedar.yaml, which wins over values-niuu.yaml.',
      proofCount: 4,
      trend: 'stable',
      latestSupport: '2026-04-19',
      supportingDates: ['2026-04-19', '2026-03-20'],
      sourceProofCount: 2,
    },
    {
      fact: 'Helm replaces lists, so the chart defaults must be repeated.',
      proofCount: 1,
      trend: 'new',
      latestSupport: null,
      supportingDates: [],
      sourceProofCount: 0,
    },
    {
      fact: 'Guild has no gateway of its own.',
      proofCount: 1,
      trend: 'weakening',
      latestSupport: '2026-03-05',
      supportingDates: ['2026-03-05'],
      sourceProofCount: 1,
    },
  ],
};

export interface FakeMimirOverrides {
  pages?: Page[];
  graph?: MimirGraph;
  mounts?: Mount[];
  queryStats?: QueryStats;
  liveActivity?: LiveActivity[];
  lintReport?: LintReport;
  /** Force ingestUrl/ingestFile to reject, to test the error path. */
  ingestShouldFail?: boolean;
}

export function createFakeMimirService(overrides: FakeMimirOverrides = {}): IMimirService {
  const pages = overrides.pages ?? FAKE_PAGES;
  const graph = overrides.graph ?? FAKE_GRAPH;
  const mounts = overrides.mounts ?? FAKE_MOUNTS;
  const queryStats = overrides.queryStats ?? FAKE_QUERY_STATS;
  const liveActivity = overrides.liveActivity ?? FAKE_LIVE_ACTIVITY;
  const lintReport = overrides.lintReport ?? FAKE_LINT_REPORT;
  let sources = [...FAKE_SOURCES];

  return {
    mounts: {
      async listMounts() {
        return mounts;
      },
      async listRoutingRules() {
        return [];
      },
      async upsertRoutingRule(rule) {
        return rule;
      },
      async deleteRoutingRule() {
        // no-op in fake
      },
      async listRavnBindings() {
        return [];
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
      async getDoctor() {
        return null;
      },
      async runDoctorFixes() {
        return null;
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
        const mountPages = new Set(
          pages.filter((p) => p.mounts.includes(options.mountName!)).map((p) => p.path),
        );
        return {
          nodes: graph.nodes.filter((n) => mountPages.has(n.id)),
          edges: graph.edges.filter((e) => mountPages.has(e.source) && mountPages.has(e.target)),
        };
      },
      async listEntities() {
        return [];
      },
      async getLiveActivity(options) {
        if (!options?.since) return liveActivity;
        return liveActivity.filter((a) => a.timestamp > options.since!);
      },
      async getEvidence(path) {
        return FAKE_EVIDENCE[path] ?? [];
      },
      async getRelated(path, depth = 1) {
        return (FAKE_RELATED[path] ?? []).filter((r) => r.hop <= depth);
      },
      async revisePage(request) {
        const page = pages.find((p) => p.path === request.path);
        if (!page) throw new Error(`unknown page: ${request.path}`);
        return page;
      },
    },
    embeddings: {
      async semanticSearch() {
        return [];
      },
    },
    lint: {
      async getLintReport() {
        return lintReport;
      },
      async runAutoFix() {
        return lintReport;
      },
      async reassignIssues() {
        return lintReport;
      },
      async getDreamCycles() {
        return [];
      },
      async getActivityLog() {
        return [];
      },
    },
  };
}
