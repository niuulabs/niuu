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

  describe('mounts registry mutations', () => {
    const registryMount = {
      name: 'shared',
      kind: 'remote' as const,
      lifecycle: 'registered' as const,
      role: 'shared',
      url: 'https://mimir.internal',
      categories: ['entity'],
      authRef: 'mimir-secret',
      defaultReadPriority: 5,
      enabled: true,
      healthStatus: 'healthy' as const,
      healthMessage: 'ok',
      desc: 'Shared mount',
    };

    it('creates registry mounts through POST /registry/mounts', async () => {
      const client = makeClient({
        post: vi.fn().mockResolvedValue({
          id: 'registry-shared',
          name: 'shared',
          kind: 'remote',
          lifecycle: 'registered',
          role: 'shared',
          url: 'https://mimir.internal',
          categories: ['entity'],
          auth_ref: 'mimir-secret',
          default_read_priority: 5,
          enabled: true,
          health_status: 'healthy',
          health_message: 'ok',
          desc: 'Shared mount',
        }),
      });

      const mount = await buildMimirHttpAdapter(client).mounts.createRegistryMount(registryMount);

      expect(client.post).toHaveBeenCalledWith('/registry/mounts', {
        adapter: '',
        kwargs: {},
        secret_kwargs_env: {},
        name: 'shared',
        kind: 'remote',
        lifecycle: 'registered',
        role: 'shared',
        url: 'https://mimir.internal',
        categories: ['entity'],
        auth_ref: 'mimir-secret',
        default_read_priority: 5,
        enabled: true,
        health_status: 'healthy',
        health_message: 'ok',
        desc: 'Shared mount',
      });
      expect(mount).toMatchObject({ id: 'registry-shared', authRef: 'mimir-secret' });
    });

    it('updates registry mounts through PUT /registry/mounts/{id}', async () => {
      const client = makeClient({
        put: vi.fn().mockResolvedValue({
          id: 'registry-shared',
          name: 'shared',
          kind: 'remote',
          lifecycle: 'registered',
          role: 'shared',
          url: 'https://mimir.internal',
          categories: ['entity'],
          auth_ref: null,
          default_read_priority: 5,
          enabled: false,
          health_status: 'down',
          health_message: 'offline',
          desc: 'Shared mount',
        }),
      });

      const mount = await buildMimirHttpAdapter(client).mounts.updateRegistryMount(
        'registry-shared',
        {
          ...registryMount,
          authRef: null,
          enabled: false,
          healthStatus: 'down',
          healthMessage: 'offline',
        },
      );

      expect(client.put).toHaveBeenCalledWith('/registry/mounts/registry-shared', {
        adapter: '',
        kwargs: {},
        secret_kwargs_env: {},
        name: 'shared',
        kind: 'remote',
        lifecycle: 'registered',
        role: 'shared',
        url: 'https://mimir.internal',
        categories: ['entity'],
        auth_ref: null,
        default_read_priority: 5,
        enabled: false,
        health_status: 'down',
        health_message: 'offline',
        desc: 'Shared mount',
      });
      expect(mount).toMatchObject({ enabled: false, healthStatus: 'down' });
    });

    it('deletes registry mounts through DELETE /registry/mounts/{id}', async () => {
      const client = makeClient();
      await buildMimirHttpAdapter(client).mounts.deleteRegistryMount('registry-shared');
      expect(client.delete).toHaveBeenCalledWith('/registry/mounts/registry-shared');
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

    it('derives structured zones from compiled truth markdown when the backend omits zones', async () => {
      const client = makeClient({
        get: vi.fn().mockResolvedValue({
          path: 'runs/NIU-912-postmortem.md',
          title: 'NIU-912 Postmortem',
          summary: 'Summary',
          category: 'runs',
          updated_at: '2026-05-10T14:03:29.614843+00:00',
          source_ids: ['src-1'],
          content: `## Compiled Truth

**Run**: test

### What was done

- Added artifact

## Timeline

- 2026-05-10: Artifact created. [Source: src-1]
`,
          related: [],
        }),
      });

      const result = await buildMimirHttpAdapter(client).pages.getPage(
        'runs/NIU-912-postmortem.md',
      );

      expect(result?.zones).toEqual([
        expect.objectContaining({ kind: 'assessment' }),
        expect.objectContaining({
          kind: 'timeline',
          items: [expect.objectContaining({ date: '2026-05-10', note: 'Artifact created' })],
        }),
      ]);
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

    it('carries the mount each result was read from', async () => {
      const client = makeClient({
        get: vi
          .fn()
          .mockResolvedValue([
            { path: '/a', title: 'A', summary: 'S', category: 'arch', mount: 'shared' },
          ]),
      });
      const [result] = await buildMimirHttpAdapter(client).pages.search('a');
      expect(result?.mounts).toEqual(['shared']);
    });

    it('passes the active mount through to the backend when provided', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.search('k8s', 'hybrid', 'local');
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('mount=local');
    });

    it('passes debug=true through to the backend when requested', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.search('k8s', 'hybrid', undefined, true);
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).toContain('debug=true');
    });

    it('omits the debug param when debug is false', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.search('k8s', 'hybrid', undefined, false);
      const call = (client.get as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
      expect(call).not.toContain('debug=');
    });

    it('maps score and score_breakdown to the domain shape', async () => {
      const breakdown = {
        base: 0.42,
        recency: 0.93,
        title_match: 1.25,
        confidence: 1.15,
        backlinks: 1.08,
        zone: 1.1,
        final: 0.61,
      };
      const client = makeClient({
        get: vi.fn().mockResolvedValue([
          {
            path: '/arch/overview',
            title: 'Architecture Overview',
            summary: 'Summary',
            category: 'arch',
            score: 0.61,
            score_breakdown: breakdown,
          },
        ]),
      });

      const results = await buildMimirHttpAdapter(client).pages.search(
        'arch',
        'hybrid',
        undefined,
        true,
      );

      expect(results[0]).toMatchObject({ score: 0.61, scoreBreakdown: breakdown });
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

  describe('pages.getKnowledgeGraph (G1, GET /memory/graph)', () => {
    const rawKnowledgeGraph = {
      layers: [
        { id: 'org', name: 'Org' },
        { id: 'project:p1', name: 'Booking platform' },
      ],
      nodes: [
        {
          id: 'project:p1/projects/booking-cutover',
          layer: 'project:p1',
          path: 'projects/booking-cutover',
          title: 'Booking cut-over',
          kind: 'decision',
          summary: 'Bookings move to booking-api v2.',
          updated_at: '2026-11-12T09:00:00Z',
          first_seen: '2026-11-03',
          changed_at: '2026-11-12',
        },
        {
          id: 'project:p1/sources/lin-501',
          layer: 'project:p1',
          path: 'sources/lin-501',
          title: 'LIN-501',
          kind: 'ticket',
          summary: '',
          updated_at: '2026-11-12T08:00:00Z',
          first_seen: null,
          changed_at: null,
        },
        {
          id: 'org/people/ines',
          layer: 'org',
          path: 'people/ines',
          title: 'Ines Moreau',
          kind: 'person',
          summary: 'Lead.',
          updated_at: null,
          first_seen: null,
          changed_at: null,
        },
      ],
      edges: [
        {
          source: 'project:p1/sources/lin-501',
          target: 'project:p1/projects/booking-cutover',
          relation: 'moved',
          since: '2026-11-12',
        },
        {
          source: 'org/people/ines',
          target: 'project:p1/projects/booking-cutover',
          relation: 'owns',
          since: null,
        },
      ],
      unresolved: [{ source: 'org/people/ines', target_slug: 'teams/booking' }],
    };

    it('calls GET /memory/graph, narrowed by ?layers= only when layers are given', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue(rawKnowledgeGraph) });
      const adapter = buildMimirHttpAdapter(client);
      await adapter.pages.getKnowledgeGraph();
      await adapter.pages.getKnowledgeGraph({ layers: [] });
      await adapter.pages.getKnowledgeGraph({ layers: ['org', 'project:p1'] });
      const calls = (client.get as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
      expect(calls).toEqual([
        '/memory/graph',
        '/memory/graph',
        `/memory/graph?layers=${encodeURIComponent('org,project:p1')}`,
      ]);
    });

    it('maps G1 nodes onto the scene graph: layer clusters, kind, summary, dates', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue(rawKnowledgeGraph) });
      const graph = await buildMimirHttpAdapter(client).pages.getKnowledgeGraph();
      expect(graph.layers).toEqual(rawKnowledgeGraph.layers);
      expect(graph.nodes[0]).toEqual({
        id: 'project:p1/projects/booking-cutover',
        title: 'Booking cut-over',
        category: 'projects',
        path: 'projects/booking-cutover',
        kind: 'decision',
        summary: 'Bookings move to booking-api v2.',
        mount: 'project:p1',
        layer: 'project:p1',
        updatedAt: '2026-11-12T09:00:00Z',
        firstSeen: '2026-11-03',
        changedAt: '2026-11-12',
        confidence: null,
      });
    });

    it('a page with no dated timeline row has no known first-seen date, not its last write', async () => {
      // An import writes every page on one day; that must not read as "new this week".
      const client = makeClient({ get: vi.fn().mockResolvedValue(rawKnowledgeGraph) });
      const graph = await buildMimirHttpAdapter(client).pages.getKnowledgeGraph();
      expect(graph.nodes[1]).toMatchObject({
        updatedAt: '2026-11-12T08:00:00Z',
        firstSeen: '',
        changedAt: null,
      });
      expect(graph.nodes[2]).toMatchObject({ updatedAt: '', firstSeen: '', changedAt: null });
    });

    it('maps edges with relation and since, and unresolved links', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue(rawKnowledgeGraph) });
      const graph = await buildMimirHttpAdapter(client).pages.getKnowledgeGraph();
      expect(graph.edges).toEqual([
        {
          source: 'project:p1/sources/lin-501',
          target: 'project:p1/projects/booking-cutover',
          relation: 'moved',
          since: '2026-11-12',
        },
        {
          source: 'org/people/ines',
          target: 'project:p1/projects/booking-cutover',
          relation: 'owns',
          since: null,
        },
      ]);
      expect(graph.unresolved).toEqual([
        { source: 'org/people/ines', targetSlug: 'teams/booking' },
      ]);
    });

    it('propagates a failed layer as a failed call', async () => {
      const client = makeClient({
        get: vi.fn().mockRejectedValue(new Error('layer project:p1 could not be read')),
      });
      await expect(buildMimirHttpAdapter(client).pages.getKnowledgeGraph()).rejects.toThrow(
        'layer project:p1 could not be read',
      );
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

    it('maps kind, updatedAt, firstSeen, confidence, and edge type', async () => {
      const rawGraph = {
        nodes: [
          {
            id: 'local:/entities/person-karpathy',
            title: 'Andrej Karpathy',
            category: 'person',
            kind: 'person',
            updated_at: '2026-04-17T14:22:00Z',
            first_seen: '2026-03-01T00:00:00Z',
            confidence: 'high',
          },
          {
            id: 'local:/arch/overview',
            title: 'Architecture Overview',
            category: 'arch',
            kind: 'topic',
            updated_at: '2026-04-18T10:00:00Z',
            first_seen: '2026-02-01T00:00:00Z',
            confidence: null,
          },
        ],
        edges: [
          {
            source: 'local:/entities/person-karpathy',
            target: 'local:/arch/overview',
            type: 'contradicts',
          },
        ],
      };
      const client = makeClient({ get: vi.fn().mockResolvedValue(rawGraph) });
      const graph = await buildMimirHttpAdapter(client).pages.getGraph();
      expect(graph.nodes[0]).toMatchObject({
        kind: 'person',
        updatedAt: '2026-04-17T14:22:00Z',
        firstSeen: '2026-03-01T00:00:00Z',
        confidence: 'high',
      });
      expect(graph.nodes[1]).toMatchObject({ kind: 'topic', confidence: null });
      expect(graph.edges[0]).toMatchObject({ relation: 'contradicts' });
    });

    it('passes a null confidence through as null', async () => {
      const rawGraph = {
        nodes: [
          {
            id: 'local:%2Fx',
            title: 'X',
            category: 'x',
            path: '/x',
            mount: 'local',
            updated_at: '2026-04-02T10:00:00+00:00',
            first_seen: '2026-04-02T10:00:00+00:00',
            confidence: null,
          },
        ],
        edges: [],
      };
      const client = makeClient({ get: vi.fn().mockResolvedValue(rawGraph) });
      const graph = await buildMimirHttpAdapter(client).pages.getGraph();
      expect(graph.nodes[0]?.confidence).toBeNull();
    });
  });

  describe('pages.getLiveActivity', () => {
    it('calls GET /activity/live without query string when no options', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.getLiveActivity();
      expect(client.get).toHaveBeenCalledWith('/activity/live');
    });

    it('url-encodes the since param when provided', async () => {
      const client = makeClient({ get: vi.fn().mockResolvedValue([]) });
      await buildMimirHttpAdapter(client).pages.getLiveActivity({
        since: '2026-04-19T09:00:00Z',
      });
      expect(client.get).toHaveBeenCalledWith(
        `/activity/live?since=${encodeURIComponent('2026-04-19T09:00:00Z')}`,
      );
    });

    it('maps snake_case raw activity to LiveActivity', async () => {
      const raw = [
        {
          id: 'act-1',
          timestamp: '2026-04-19T09:00:00Z',
          kind: 'write',
          mount: 'local',
          path: '/arch/overview',
          actor: 'ravn-fjolnir',
        },
        {
          id: 'act-2',
          timestamp: '2026-04-19T08:55:00Z',
          kind: 'read',
          mount: 'shared',
          path: '/api/overview',
          actor: null,
        },
      ];
      const client = makeClient({ get: vi.fn().mockResolvedValue(raw) });
      const activity = await buildMimirHttpAdapter(client).pages.getLiveActivity();
      expect(activity).toEqual([
        {
          id: 'act-1',
          timestamp: '2026-04-19T09:00:00Z',
          kind: 'write',
          mount: 'local',
          path: '/arch/overview',
          actor: 'ravn-fjolnir',
        },
        {
          id: 'act-2',
          timestamp: '2026-04-19T08:55:00Z',
          kind: 'read',
          mount: 'shared',
          path: '/api/overview',
          actor: null,
        },
      ]);
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

  describe('mounts.getEvalReport', () => {
    it('calls GET /eval/latest and maps the wire shape', async () => {
      const client = makeClient({
        get: vi.fn().mockResolvedValue({
          generated_at: '2026-04-19T04:30:00Z',
          overall: { precision_at_5: 0.82, mrr: 0.74, recall_at_10: 0.91 },
          by_category: {
            arch: { precision_at_5: 0.88, mrr: 0.81, recall_at_10: 0.95 },
          },
          query_count: 120,
        }),
      });

      const report = await buildMimirHttpAdapter(client).mounts.getEvalReport();

      expect(client.get).toHaveBeenCalledWith('/eval/latest');
      expect(report).toEqual({
        generatedAt: '2026-04-19T04:30:00Z',
        overall: { precisionAt5: 0.82, mrr: 0.74, recallAt10: 0.91 },
        byCategory: { arch: { precisionAt5: 0.88, mrr: 0.81, recallAt10: 0.95 } },
        queryCount: 120,
      });
    });

    it('returns null when the eval route is unavailable', async () => {
      const client = makeClient({ get: vi.fn().mockRejectedValue(missingRoute()) });
      await expect(buildMimirHttpAdapter(client).mounts.getEvalReport()).resolves.toBeNull();
    });

    it('rethrows non-404 errors', async () => {
      const client = makeClient({ get: vi.fn().mockRejectedValue(new Error('boom')) });
      await expect(buildMimirHttpAdapter(client).mounts.getEvalReport()).rejects.toThrow('boom');
    });
  });

  describe('mounts.getQueryStats', () => {
    it('calls GET /eval/queries and maps the wire shape', async () => {
      const client = makeClient({
        get: vi.fn().mockResolvedValue({
          total: 1843,
          zero_result_count: 37,
          recent: [{ ts: '2026-04-19T10:14:02Z', query: 'hexagonal', result_count: 6 }],
        }),
      });

      const stats = await buildMimirHttpAdapter(client).mounts.getQueryStats();

      expect(client.get).toHaveBeenCalledWith('/eval/queries');
      expect(stats).toEqual({
        total: 1843,
        zeroResultCount: 37,
        recent: [{ ts: '2026-04-19T10:14:02Z', query: 'hexagonal', resultCount: 6 }],
      });
    });

    it('returns null when query stats are unavailable', async () => {
      const client = makeClient({ get: vi.fn().mockRejectedValue(missingRoute()) });
      await expect(buildMimirHttpAdapter(client).mounts.getQueryStats()).resolves.toBeNull();
    });

    it('rethrows non-404 errors', async () => {
      const client = makeClient({ get: vi.fn().mockRejectedValue(new Error('boom')) });
      await expect(buildMimirHttpAdapter(client).mounts.getQueryStats()).rejects.toThrow('boom');
    });
  });
});

it('routes deployments through Guild while keeping knowledge queries on Mimir', async () => {
  const knowledge = makeClient();
  const guild = makeClient();
  const service = buildMimirHttpAdapter(knowledge, guild);
  await service.mounts.listMounts();
  await service.mounts.getDeployments!();
  await service.mounts.deployInstance!({
    name: 'brain',
    backend: 'gbrain',
    target: 'ymir:cluster',
  });
  await service.mounts.inspectDeployment!('brain', 'ymir:cluster');
  await service.mounts.controlDeployment!('brain', 'update', 'ymir:cluster');
  expect(knowledge.get).toHaveBeenCalledWith('/mounts');
  expect(knowledge.post).not.toHaveBeenCalled();
  expect(guild.get).toHaveBeenCalledWith('/deployments');
  expect(guild.get).toHaveBeenCalledWith('/deployments/brain?target=ymir%3Acluster');
  expect(guild.post).toHaveBeenCalledWith('/deployments', {
    name: 'brain',
    backend: 'gbrain',
    target: 'ymir:cluster',
  });
  expect(guild.post).toHaveBeenCalledWith('/deployments/brain/update?target=ymir%3Acluster', {});
});
