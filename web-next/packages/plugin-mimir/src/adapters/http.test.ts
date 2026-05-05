import { describe, it, expect, vi } from 'vitest';
import { buildMimirHttpAdapter } from './http';

function makeClient(overrides: Record<string, ReturnType<typeof vi.fn>> = {}) {
  return {
    get: vi.fn().mockResolvedValue([]),
    post: vi.fn().mockResolvedValue({ issues: [], pages_checked: 0 }),
    put: vi.fn().mockResolvedValue(undefined),
    patch: vi.fn(),
    delete: vi.fn(),
    ...overrides,
  };
}

function missingRoute(status = 404) {
  return Object.assign(new Error(`missing route ${status}`), { status });
}

describe('buildMimirHttpAdapter', () => {
  describe('mounts.listMounts', () => {
    it('calls GET /mounts', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).mounts.listMounts();
      expect(client.get).toHaveBeenCalledWith('/mounts');
    });

    it('maps raw mount fields to domain Mount', async () => {
      const rawMount = {
        name: 'local',
        role: 'local',
        host: 'localhost',
        url: 'http://localhost:7700',
        priority: 1,
        categories: null,
        status: 'healthy',
        pages: 42,
        sources: 18,
        lint_issues: 3,
        last_write: '2026-04-18T14:22:00Z',
        embedding: 'all-minilm-l6-v2',
        size_kb: 512,
        desc: 'Local mount',
      };
      const client = makeClient({ get: vi.fn().mockResolvedValue([rawMount]) });
      const mounts = await buildMimirHttpAdapter(client).mounts.listMounts();
      expect(mounts[0]).toMatchObject({
        name: 'local',
        role: 'local',
        pages: 42,
        lintIssues: 3,
        lastWrite: '2026-04-18T14:22:00Z',
        sizeKb: 512,
      });
    });

    it('falls back to a synthetic local mount when /mounts is unavailable', async () => {
      const client = makeClient({
        get: vi
          .fn()
          .mockRejectedValueOnce(missingRoute())
          .mockResolvedValueOnce({ page_count: 12, categories: ['arch'], healthy: true }),
      });

      const mounts = await buildMimirHttpAdapter(client).mounts.listMounts();

      expect(mounts).toEqual([
        expect.objectContaining({
          name: 'local',
          role: 'local',
          pages: 12,
          categories: ['arch'],
          status: 'healthy',
        }),
      ]);
    });
  });

  describe('mounts.listRegistryMounts', () => {
    it('calls GET /registry/mounts when the route exists', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).mounts.listRegistryMounts?.();
      expect(client.get).toHaveBeenCalledWith('/registry/mounts');
    });

    it('falls back to mount data when registry routes are unavailable', async () => {
      const client = makeClient({
        get: vi
          .fn()
          .mockRejectedValueOnce(missingRoute())
          .mockResolvedValueOnce([
            {
              name: 'shared',
              role: 'shared',
              host: 'mimir.internal',
              url: 'https://mimir.internal',
              priority: 2,
              categories: ['entity'],
              status: 'healthy',
              pages: 40,
              sources: 12,
              lint_issues: 0,
              last_write: '2026-04-18T14:22:00Z',
              embedding: 'all-minilm-l6-v2',
              size_kb: 512,
              desc: 'Shared mount',
            },
          ]),
      });

      const mounts = await buildMimirHttpAdapter(client).mounts.listRegistryMounts?.();

      expect(mounts).toEqual([
        expect.objectContaining({
          name: 'shared',
          role: 'shared',
          kind: 'remote',
          healthStatus: 'healthy',
        }),
      ]);
    });
  });

  describe('pages.getStats', () => {
    it('calls GET /stats', async () => {
      const client = makeClient({
        get: vi.fn().mockResolvedValue({ page_count: 5, categories: [], healthy: true }),
      });
      await buildMimirHttpAdapter(client).pages.getStats();
      expect(client.get).toHaveBeenCalledWith('/stats');
    });

    it('maps snake_case to camelCase', async () => {
      const client = makeClient({
        get: vi.fn().mockResolvedValue({
          page_count: 10,
          categories: ['arch', 'api'],
          healthy: false,
        }),
      });
      const stats = await buildMimirHttpAdapter(client).pages.getStats();
      expect(stats.pageCount).toBe(10);
      expect(stats.categories).toEqual(['arch', 'api']);
      expect(stats.healthy).toBe(false);
    });
  });

  describe('pages.listPages', () => {
    it('calls GET /pages without query string when no options', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.listPages();
      expect(client.get).toHaveBeenCalledWith('/pages');
    });

    it('appends mount and category query params', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.listPages({ mountName: 'local', category: 'arch' });
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('mount=local');
      expect(call).toContain('category=arch');
    });
  });

  describe('pages.getPage', () => {
    it('calls GET /page with path param', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue(null) });
      await buildMimirHttpAdapter(client).pages.getPage('/arch/overview');
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('/page');
      expect(call).toContain('path=%2Farch%2Foverview');
    });

    it('returns null when API returns null', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue(null) });
      const result = await buildMimirHttpAdapter(client).pages.getPage('/missing');
      expect(result).toBeNull();
    });

    it('returns null when the existing backend responds with 404', async () => {
      const client = makeClient({ get: vi.fn().mockRejectedValue(missingRoute()) });
      const result = await buildMimirHttpAdapter(client).pages.getPage('/missing');
      expect(result).toBeNull();
    });
  });

  describe('pages.upsertPage', () => {
    it('calls PUT /page with path and content', async () => {
      const client = makeClient();
      await buildMimirHttpAdapter(client).pages.upsertPage('/test', '# Content');
      expect(client.put).toHaveBeenCalledWith('/page', { path: '/test', content: '# Content' });
    });
  });

  describe('pages.search', () => {
    it('calls GET /search with query and default mode', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.search('kubernetes');
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('q=kubernetes');
      expect(call).toContain('mode=hybrid');
    });

    it('respects explicit search mode', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.search('k8s', 'fts');
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('mode=fts');
    });

    it('infers page type and confidence when the backend returns the lean shape', async () => {
      const client = makeClient({
        get: vi.fn().mockResolvedValue([
          {
            path: '/decisions/adr-001',
            title: 'ADR-001',
            summary: 'Decision',
            category: 'arch',
          },
          {
            path: '/preferences/editor',
            title: 'Editor Preference',
            summary: 'Preference',
            category: 'prefs',
          },
          {
            path: '/directives/writing',
            title: 'Writing Directive',
            summary: 'Directive',
            category: 'guide',
          },
        ]),
      });

      const results = await buildMimirHttpAdapter(client).pages.search('adr');

      expect(results).toEqual([
        expect.objectContaining({ type: 'decision', confidence: 'medium' }),
        expect.objectContaining({ type: 'preference', confidence: 'medium' }),
        expect.objectContaining({ type: 'directive', confidence: 'medium' }),
      ]);
    });
  });

  describe('embeddings.semanticSearch', () => {
    it('calls GET /embeddings/search with query and topK', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).embeddings.semanticSearch('arch', 5);
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('/embeddings/search');
      expect(call).toContain('q=arch');
      expect(call).toContain('top_k=5');
    });

    it('maps raw embedding result to EmbeddingSearchResult', async () => {
      const raw = [{ path: '/a', title: 'A', summary: 'S', score: 0.9, mount_name: 'local' }];
      const client = makeClient({ get: vi.fn().mockResolvedValue(raw) });
      const results = await buildMimirHttpAdapter(client).embeddings.semanticSearch('q');
      expect(results[0]).toMatchObject({ path: '/a', score: 0.9, mountName: 'local' });
    });

    it('falls back to FTS search when embedding search is unavailable', async () => {
      const client = makeClient({
        get: vi
          .fn()
          .mockRejectedValueOnce(missingRoute())
          .mockResolvedValueOnce([
            {
              path: '/arch/overview',
              title: 'Architecture Overview',
              summary: 'Summary',
              category: 'arch',
            },
          ]),
      });

      const results = await buildMimirHttpAdapter(client).embeddings.semanticSearch('arch', 5);

      expect(client.get).toHaveBeenNthCalledWith(1, '/embeddings/search?q=arch&top_k=5');
      expect(client.get).toHaveBeenNthCalledWith(2, '/search?q=arch&mode=fts');
      expect(results[0]).toMatchObject({
        path: '/arch/overview',
        mountName: 'local',
      });
    });
  });

  describe('lint.getLintReport', () => {
    it('calls GET /lint', async () => {
      const client = makeClient({
        get: vi.fn().mockResolvedValue({ issues: [], pages_checked: 0 }),
      });
      await buildMimirHttpAdapter(client).lint.getLintReport();
      expect(client.get).toHaveBeenCalledWith('/lint');
    });

    it('maps raw lint issues with snake_case fields', async () => {
      const rawIssue = {
        id: 'lint-1',
        rule: 'L05',
        severity: 'error',
        page: '/a',
        mount: 'local',
        auto_fix: true,
        message: 'Broken link',
      };
      const client = makeClient({
        get: vi.fn().mockResolvedValue({ issues: [rawIssue], pages_checked: 5 }),
      });
      const report = await buildMimirHttpAdapter(client).lint.getLintReport();
      expect(report.issues[0]).toMatchObject({
        id: 'lint-1',
        rule: 'L05',
        autoFix: true,
      });
    });

    it('maps the current backend lint issue shape', async () => {
      const client = makeClient({
        get: vi.fn().mockResolvedValue({
          issues: [
            {
              id: 'L12',
              severity: 'warning',
              message: 'Invalid frontmatter',
              page_path: '/arch/overview',
              auto_fixable: true,
            },
          ],
          pages_checked: 3,
        }),
      });

      const report = await buildMimirHttpAdapter(client).lint.getLintReport();

      expect(report).toMatchObject({
        pagesChecked: 3,
        summary: { error: 0, warn: 1, info: 0 },
      });
      expect(report.issues[0]).toMatchObject({
        rule: 'L12',
        severity: 'warn',
        page: '/arch/overview',
        mount: 'local',
        autoFix: true,
      });
    });
  });

  describe('lint.runAutoFix', () => {
    it('calls POST /lint/fix with empty body when no ids', async () => {
      const client = makeClient();
      await buildMimirHttpAdapter(client).lint.runAutoFix();
      expect(client.post).toHaveBeenCalledWith('/lint/fix', {});
    });

    it('passes issue_ids when provided', async () => {
      const client = makeClient();
      await buildMimirHttpAdapter(client).lint.runAutoFix(['lint-1', 'lint-2']);
      expect(client.post).toHaveBeenCalledWith('/lint/fix', {
        issue_ids: ['lint-1', 'lint-2'],
      });
    });
  });

  describe('lint.getDreamCycles', () => {
    it('calls GET /dreams with limit', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).lint.getDreamCycles(5);
      expect(client.get).toHaveBeenCalledWith('/dreams?limit=5');
    });

    it('maps raw dream cycle fields', async () => {
      const raw = [
        {
          id: 'dream-1',
          timestamp: '2026-04-19T03:00:00Z',
          ravn: 'ravn-fjolnir',
          mounts: ['local'],
          pages_updated: 8,
          entities_created: 2,
          lint_fixes: 1,
          duration_ms: 42000,
        },
      ];
      const client = makeClient({ get: vi.fn().mockResolvedValue(raw) });
      const cycles = await buildMimirHttpAdapter(client).lint.getDreamCycles();
      expect(cycles[0]).toMatchObject({
        id: 'dream-1',
        pagesUpdated: 8,
        entitiesCreated: 2,
        lintFixes: 1,
        durationMs: 42000,
      });
    });

    it('returns an empty list when dreams are not implemented yet', async () => {
      const client = makeClient({ get: vi.fn().mockRejectedValue(missingRoute()) });
      await expect(buildMimirHttpAdapter(client).lint.getDreamCycles()).resolves.toEqual([]);
    });
  });

  describe('lint.getActivityLog', () => {
    it('calls GET /activity with limit', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).lint.getActivityLog(10);
      expect(client.get).toHaveBeenCalledWith('/activity?limit=10');
    });

    it('maps raw activity event fields', async () => {
      const raw = [
        {
          id: 'act-1',
          timestamp: '2026-04-19T10:00:00Z',
          kind: 'write',
          mount: 'local',
          ravn: 'ravn-fjolnir',
          message: 'wrote some page',
          page: 'some/page.md',
        },
      ];
      const client = makeClient({ get: vi.fn().mockResolvedValue(raw) });
      const events = await buildMimirHttpAdapter(client).lint.getActivityLog();
      expect(events[0]).toMatchObject({
        id: 'act-1',
        kind: 'write',
        mount: 'local',
        ravn: 'ravn-fjolnir',
        message: 'wrote some page',
        page: 'some/page.md',
      });
    });

    it('returns an empty list when activity is not implemented yet', async () => {
      const client = makeClient({ get: vi.fn().mockRejectedValue(missingRoute()) });
      await expect(buildMimirHttpAdapter(client).lint.getActivityLog()).resolves.toEqual([]);
    });
  });

  describe('pages.getGraph', () => {
    it('calls GET /graph without query string when no options', async () => {
      const rawGraph = { nodes: [], edges: [] };
      const client = makeClient({ get: vi.fn().mockResolvedValue(rawGraph) });
      await buildMimirHttpAdapter(client).pages.getGraph();
      expect(client.get).toHaveBeenCalledWith('/graph');
    });

    it('appends mount query param when mountName is provided', async () => {
      const rawGraph = { nodes: [], edges: [] };
      const client = makeClient({ get: vi.fn().mockResolvedValue(rawGraph) });
      await buildMimirHttpAdapter(client).pages.getGraph({ mountName: 'local' });
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('/graph');
      expect(call).toContain('mount=local');
    });

    it('maps raw graph node and edge fields', async () => {
      const rawGraph = {
        nodes: [
          {
            id: '/arch/overview',
            title: 'Architecture Overview',
            category: 'arch',
            inbound_count: 2,
          },
        ],
        edges: [{ source: '/infra/k8s', target: '/arch/overview' }],
      };
      const client = makeClient({ get: vi.fn().mockResolvedValue(rawGraph) });
      const graph = await buildMimirHttpAdapter(client).pages.getGraph();
      expect(graph.nodes[0]).toMatchObject({
        id: '/arch/overview',
        title: 'Architecture Overview',
        category: 'arch',
        inboundCount: 2,
      });
      expect(graph.edges[0]).toMatchObject({ source: '/infra/k8s', target: '/arch/overview' });
    });
  });

  describe('mounts.getRecentWrites', () => {
    it('calls GET /mounts/recent-writes without limit when none specified', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).mounts.getRecentWrites();
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('/mounts/recent-writes');
      expect(call).not.toContain('limit=');
    });

    it('passes explicit limit', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).mounts.getRecentWrites(5);
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('limit=5');
    });

    it('maps raw recent write fields', async () => {
      const raw = [
        {
          id: 'rw-1',
          timestamp: '2026-04-19T10:00:00Z',
          mount: 'local',
          page: '/arch/overview',
          ravn: 'ravn-fjolnir',
          kind: 'write',
          message: 'Updated architecture overview',
        },
      ];
      const client = makeClient({ get: vi.fn().mockResolvedValue(raw) });
      const writes = await buildMimirHttpAdapter(client).mounts.getRecentWrites();
      expect(writes[0]).toMatchObject({
        id: 'rw-1',
        mount: 'local',
        page: '/arch/overview',
        ravn: 'ravn-fjolnir',
        kind: 'write',
      });
    });

    it('derives recent writes from sources when the dedicated endpoint is unavailable', async () => {
      const client = makeClient({
        get: vi
          .fn()
          .mockRejectedValueOnce(missingRoute())
          .mockResolvedValueOnce([
            {
              source_id: 'src-001',
              title: 'ADR-001',
              source_type: 'document',
              ingested_at: '2026-04-11T10:30:00Z',
            },
          ]),
      });

      const writes = await buildMimirHttpAdapter(client).mounts.getRecentWrites();

      expect(writes[0]).toMatchObject({
        id: 'src-001',
        mount: 'local',
        ravn: 'mimir',
        kind: 'compile',
        message: 'ADR-001',
      });
    });
  });

  describe('pages.listSources', () => {
    it('calls GET /sources without query string when no options', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.listSources();
      expect(client.get).toHaveBeenCalledWith('/sources');
    });

    it('appends origin_type and mount query params', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.listSources({
        originType: 'web',
        mountName: 'local',
      });
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('origin_type=web');
      expect(call).toContain('mount=local');
    });
  });

  describe('pages.getPageSources', () => {
    it('calls GET /page/sources with encoded path', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.getPageSources('/arch/overview');
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('/page/sources');
      expect(call).toContain('path=%2Farch%2Foverview');
    });
  });

  describe('pages.listEntities', () => {
    it('calls GET /entities without query string when no options', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.listEntities();
      expect(client.get).toHaveBeenCalledWith('/entities');
    });

    it('appends kind query param when kind is provided', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.listEntities({ kind: 'org' });
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('/entities');
      expect(call).toContain('kind=org');
    });

    it('maps raw entity meta fields', async () => {
      const rawEntities = [
        {
          path: '/entities/niuulabs',
          title: 'Niuu Labs',
          entity_kind: 'org',
          summary: 'The organisation behind Niuu.',
          relationship_count: 3,
        },
      ];
      const client = makeClient({ get: vi.fn().mockResolvedValue(rawEntities) });
      const entities = await buildMimirHttpAdapter(client).pages.listEntities();
      expect(entities[0]).toMatchObject({
        path: '/entities/niuulabs',
        title: 'Niuu Labs',
        entityKind: 'org',
        summary: 'The organisation behind Niuu.',
        relationshipCount: 3,
      });
    });

    it('derives entity cards from pages when /entities is unavailable', async () => {
      const client = makeClient({
        get: vi
          .fn()
          .mockRejectedValueOnce(missingRoute())
          .mockResolvedValueOnce([
            {
              path: '/entities/niuulabs',
              title: 'Niuu Labs',
              summary: 'The organisation behind Niuu.',
              category: 'entity',
              updated_at: '2026-04-10T08:00:00Z',
              source_ids: [],
            },
          ]),
      });

      const entities = await buildMimirHttpAdapter(client).pages.listEntities();

      expect(entities).toEqual([
        expect.objectContaining({
          path: '/entities/niuulabs',
          title: 'Niuu Labs',
          entityKind: 'org',
        }),
      ]);
    });

    it('supports kind filtering against derived entity cards', async () => {
      const client = makeClient({
        get: vi
          .fn()
          .mockRejectedValueOnce(missingRoute())
          .mockResolvedValueOnce([
            {
              path: '/entities/people/jane-doe',
              title: 'Jane Doe',
              summary: 'Person record',
              category: 'entity',
              updated_at: '2026-04-10T08:00:00Z',
              source_ids: [],
            },
            {
              path: '/entities/project/atlas',
              title: 'Atlas',
              summary: 'Project record',
              category: 'entity',
              updated_at: '2026-04-10T08:00:00Z',
              source_ids: [],
            },
            {
              path: '/entities/component/api-gateway',
              title: 'API Gateway',
              summary: 'Component record',
              category: 'entity',
              updated_at: '2026-04-10T08:00:00Z',
              source_ids: [],
            },
            {
              path: '/entities/technology/postgres',
              title: 'Postgres',
              summary: 'Technology record',
              category: 'entity',
              updated_at: '2026-04-10T08:00:00Z',
              source_ids: [],
            },
            {
              path: '/entities/idea/federation',
              title: 'Federation',
              summary: 'Concept record',
              category: 'entity',
              updated_at: '2026-04-10T08:00:00Z',
              source_ids: [],
            },
          ]),
      });

      await expect(
        buildMimirHttpAdapter(client).pages.listEntities({ kind: 'person' }),
      ).resolves.toEqual([expect.objectContaining({ entityKind: 'person' })]);
    });
  });

  describe('pages.listSources', () => {
    it('calls GET /sources without query string when no options', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.listSources();
      expect(client.get).toHaveBeenCalledWith('/sources');
    });

    it('appends origin_type and mount params when provided', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.listSources({
        originType: 'web',
        mountName: 'local',
      });
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('origin_type=web');
      expect(call).toContain('mount=local');
    });

    it('maps raw source fields to domain Source', async () => {
      const raw = [
        {
          id: 'src-001',
          title: 'Arch wiki',
          origin_type: 'web',
          origin_url: 'https://wiki.niuu.world/arch',
          ingested_at: '2026-04-10T08:00:00Z',
          ingest_agent: 'ravn-fjolnir',
          compiled_into: ['/arch/overview'],
          content: 'The Niuu platform uses hexagonal architecture.',
        },
      ];
      const client = makeClient({ get: vi.fn().mockResolvedValue(raw) });
      const sources = await buildMimirHttpAdapter(client).pages.listSources();
      expect(sources[0]).toMatchObject({
        id: 'src-001',
        title: 'Arch wiki',
        originType: 'web',
        originUrl: 'https://wiki.niuu.world/arch',
        ingestedAt: '2026-04-10T08:00:00Z',
        ingestAgent: 'ravn-fjolnir',
        compiledInto: ['/arch/overview'],
      });
    });

    it('maps the current backend source metadata shape', async () => {
      const raw = [
        {
          source_id: 'src-001',
          title: 'Arch wiki',
          source_type: 'document',
          ingested_at: '2026-04-10T08:00:00Z',
        },
      ];
      const client = makeClient({ get: vi.fn().mockResolvedValue(raw) });
      const sources = await buildMimirHttpAdapter(client).pages.listSources();
      expect(sources[0]).toMatchObject({
        id: 'src-001',
        originType: 'file',
        ingestAgent: 'mimir',
        compiledInto: [],
      });
    });

    it('normalizes conversation metadata to chat origins', async () => {
      const client = makeClient({
        get: vi.fn().mockResolvedValue([
          {
            source_id: 'src-chat',
            title: 'Conversation',
            source_type: 'conversation',
            ingested_at: '2026-04-10T08:00:00Z',
          },
        ]),
      });

      const sources = await buildMimirHttpAdapter(client).pages.listSources();
      expect(sources[0]).toMatchObject({ originType: 'chat' });
    });

    it('defaults unknown legacy source types to file during fallback filtering', async () => {
      const client = makeClient({
        get: vi
          .fn()
          .mockRejectedValueOnce(missingRoute())
          .mockResolvedValueOnce([
            {
              source_id: 'src-custom',
              title: 'Custom Source',
              source_type: 'custom',
              ingested_at: '2026-04-10T08:00:00Z',
            },
            {
              source_id: 'src-web',
              title: 'Web Source',
              source_type: 'web',
              ingested_at: '2026-04-10T08:00:00Z',
            },
          ]),
      });

      const sources = await buildMimirHttpAdapter(client).pages.listSources({ originType: 'web' });
      expect(sources).toEqual([expect.objectContaining({ id: 'src-web', originType: 'web' })]);
    });
  });

  describe('pages.getPageSources', () => {
    it('calls GET /page/sources with encoded path', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.getPageSources('/arch/overview');
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('/page/sources');
      expect(call).toContain('path=%2Farch%2Foverview');
    });

    it('maps raw source fields for page sources', async () => {
      const raw = [
        {
          id: 'src-002',
          title: 'ADR-001',
          origin_type: 'file',
          origin_path: '/docs/adr/001.md',
          ingested_at: '2026-04-11T10:30:00Z',
          ingest_agent: 'ravn-fjolnir',
          compiled_into: ['/arch/overview'],
          content: 'We adopt hexagonal architecture.',
        },
      ];
      const client = makeClient({ get: vi.fn().mockResolvedValue(raw) });
      const sources = await buildMimirHttpAdapter(client).pages.getPageSources('/arch/overview');
      expect(sources[0]).toMatchObject({
        id: 'src-002',
        originType: 'file',
        originPath: '/docs/adr/001.md',
        ingestAgent: 'ravn-fjolnir',
      });
    });

    it('falls back through page.sourceIds when /page/sources is unavailable', async () => {
      const client = makeClient({
        get: vi
          .fn()
          .mockRejectedValueOnce(missingRoute())
          .mockResolvedValueOnce({
            path: '/arch/overview',
            title: 'Architecture Overview',
            summary: 'Summary',
            category: 'arch',
            updated_at: '2026-04-10T08:00:00Z',
            source_ids: ['src-001'],
            content: '# Overview',
            related: [],
          })
          .mockResolvedValueOnce({
            source_id: 'src-001',
            title: 'ADR-001',
            source_type: 'document',
            ingested_at: '2026-04-11T10:30:00Z',
            content: 'hello',
          }),
      });

      const sources = await buildMimirHttpAdapter(client).pages.getPageSources('/arch/overview');

      expect(sources).toEqual([
        expect.objectContaining({
          id: 'src-001',
          title: 'ADR-001',
          content: 'hello',
        }),
      ]);
    });

    it('returns an empty list when fallback page lookup has no source ids', async () => {
      const client = makeClient({
        get: vi.fn().mockRejectedValueOnce(missingRoute()).mockResolvedValueOnce({
          path: '/arch/overview',
          title: 'Architecture Overview',
          summary: 'Summary',
          category: 'arch',
          updated_at: '2026-04-10T08:00:00Z',
          source_ids: [],
          content: '# Overview',
          related: [],
        }),
      });

      await expect(
        buildMimirHttpAdapter(client).pages.getPageSources('/arch/overview'),
      ).resolves.toEqual([]);
    });
  });

  describe('pages.ingestUrl', () => {
    it('calls the dedicated URL ingest route when available', async () => {
      const client = makeClient({
        post: vi.fn().mockResolvedValue({
          id: 'src-url',
          title: 'Example',
          origin_type: 'web',
          origin_url: 'https://example.com',
          ingested_at: '2026-04-10T08:00:00Z',
          ingest_agent: 'ravn-fjolnir',
          compiled_into: [],
          content: '',
        }),
      });

      const source = await buildMimirHttpAdapter(client).pages.ingestUrl('https://example.com');
      expect(client.post).toHaveBeenCalledWith('/sources/ingest/url', {
        url: 'https://example.com',
      });
      expect(source).toMatchObject({ id: 'src-url', originType: 'web' });
    });

    it('fails clearly when only the legacy backend is available', async () => {
      const client = makeClient({ post: vi.fn().mockRejectedValue(missingRoute()) });
      await expect(
        buildMimirHttpAdapter(client).pages.ingestUrl('https://example.com'),
      ).rejects.toThrow('URL ingest is not supported by the current Mimir backend');
    });
  });

  describe('pages.ingestFile', () => {
    it('uses the dedicated upload route when available', async () => {
      const client = makeClient({
        post: vi.fn().mockResolvedValue({
          id: 'src-file',
          title: 'test.md',
          origin_type: 'file',
          origin_path: 'test.md',
          ingested_at: '2026-04-10T08:00:00Z',
          ingest_agent: 'ravn-fjolnir',
          compiled_into: [],
          content: '',
        }),
      });

      const file = new File(['# hello'], 'test.md', { type: 'text/markdown' });
      const source = await buildMimirHttpAdapter(client).pages.ingestFile(file);
      expect(source).toMatchObject({ id: 'src-file', originType: 'file' });
    });

    it('falls back to the legacy /ingest endpoint when file upload is unavailable', async () => {
      const client = makeClient({
        post: vi
          .fn()
          .mockRejectedValueOnce(missingRoute())
          .mockResolvedValueOnce({
            source_id: 'src-ingest',
            pages_updated: ['/arch/overview'],
          }),
      });

      const file = new File(['# hello'], 'test.md', { type: 'text/markdown' });
      Object.defineProperty(file, 'text', { value: vi.fn().mockResolvedValue('# hello') });
      const source = await buildMimirHttpAdapter(client).pages.ingestFile(file);

      expect(client.post).toHaveBeenNthCalledWith(2, '/ingest', {
        title: 'test.md',
        content: '# hello',
        source_type: 'document',
      });
      expect(source).toMatchObject({
        id: 'src-ingest',
        originType: 'file',
        compiledInto: ['/arch/overview'],
      });
    });

    it('uses arrayBuffer fallback when File.text is unavailable', async () => {
      const client = makeClient({
        post: vi.fn().mockRejectedValueOnce(missingRoute()).mockResolvedValueOnce({
          source_id: 'src-buffer',
          pages_updated: [],
        }),
      });

      const file = new File(['# hello'], 'buffer.md', { type: 'text/markdown' });
      Object.defineProperty(file, 'text', { value: undefined });
      Object.defineProperty(file, 'arrayBuffer', {
        value: vi.fn().mockResolvedValue(new TextEncoder().encode('# hello').buffer),
      });

      await buildMimirHttpAdapter(client).pages.ingestFile(file);

      expect(client.post).toHaveBeenNthCalledWith(2, '/ingest', {
        title: 'buffer.md',
        content: '# hello',
        source_type: 'document',
      });
    });
  });

  describe('mounts.listRoutingRules', () => {
    it('returns an empty list when routing rules are unavailable', async () => {
      const client = makeClient({ get: vi.fn().mockRejectedValue(missingRoute()) });
      await expect(buildMimirHttpAdapter(client).mounts.listRoutingRules()).resolves.toEqual([]);
    });

    it('treats 501 as a missing-route compatibility case too', async () => {
      const client = makeClient({ get: vi.fn().mockRejectedValue(missingRoute(501)) });
      await expect(buildMimirHttpAdapter(client).mounts.listRoutingRules()).resolves.toEqual([]);
    });
  });

  describe('mounts.upsertRoutingRule', () => {
    it('calls PUT /routing/rules/{id} with the rule body', async () => {
      const client = makeClient();
      const rule = {
        id: 'rule-1',
        prefix: '/arch',
        mountName: 'local',
        priority: 1,
        active: true,
      };

      await buildMimirHttpAdapter(client).mounts.upsertRoutingRule(rule);

      expect(client.put).toHaveBeenCalledWith('/routing/rules/rule-1', rule);
    });
  });

  describe('mounts.deleteRoutingRule', () => {
    it('calls DELETE /routing/rules/{id}', async () => {
      const client = makeClient();
      await buildMimirHttpAdapter(client).mounts.deleteRoutingRule('rule-1');
      expect(client.delete).toHaveBeenCalledWith('/routing/rules/rule-1');
    });
  });

  describe('mounts.listRavnBindings', () => {
    it('returns an empty list when ravn bindings are unavailable', async () => {
      const client = makeClient({ get: vi.fn().mockRejectedValue(missingRoute()) });
      await expect(buildMimirHttpAdapter(client).mounts.listRavnBindings()).resolves.toEqual([]);
    });
  });

  describe('lint.reassignIssues', () => {
    it('calls POST /lint/reassign with issue ids and assignee', async () => {
      const client = makeClient({
        post: vi.fn().mockResolvedValue({ issues: [], pages_checked: 0 }),
      });

      await buildMimirHttpAdapter(client).lint.reassignIssues(['lint-1'], 'ravn-fjolnir');

      expect(client.post).toHaveBeenCalledWith('/lint/reassign', {
        issue_ids: ['lint-1'],
        assignee: 'ravn-fjolnir',
      });
    });
  });
});
