import { describe, it, expect } from 'vitest';
import { createMimirMockAdapter } from './mock';

describe('createMimirMockAdapter', () => {
  describe('mounts.listMounts', () => {
    it('returns a non-empty list of mounts', async () => {
      const svc = createMimirMockAdapter();
      const mounts = await svc.mounts.listMounts();
      expect(mounts.length).toBeGreaterThan(0);
    });

    it('each mount has required fields', async () => {
      const svc = createMimirMockAdapter();
      const mounts = await svc.mounts.listMounts();
      for (const mount of mounts) {
        expect(mount).toHaveProperty('name');
        expect(mount).toHaveProperty('role');
        expect(mount).toHaveProperty('status');
        expect(mount).toHaveProperty('pages');
        expect(mount).toHaveProperty('url');
      }
    });

    it('includes mounts with local, shared and domain roles', async () => {
      const svc = createMimirMockAdapter();
      const mounts = await svc.mounts.listMounts();
      const roles = mounts.map((m) => m.role);
      expect(roles).toContain('local');
      expect(roles).toContain('shared');
      expect(roles).toContain('domain');
    });
  });

  describe('mounts.registry', () => {
    it('lists registry mounts', async () => {
      const svc = createMimirMockAdapter();
      const mounts = await svc.mounts.listRegistryMounts?.();
      expect(mounts?.length).toBeGreaterThan(0);
    });

    it('creates and deletes a registry mount', async () => {
      const svc = createMimirMockAdapter();
      const created = await svc.mounts.createRegistryMount?.({
        name: 'new-remote',
        kind: 'remote',
        lifecycle: 'registered',
        role: 'shared',
        url: 'https://mimir.example.com',
        categories: ['decision'],
        authRef: null,
        defaultReadPriority: 5,
        enabled: true,
        healthStatus: 'unknown',
        healthMessage: '',
        desc: 'test mount',
      });
      expect(created?.id).toBeTruthy();

      await svc.mounts.deleteRegistryMount?.(created!.id);
      const mounts = await svc.mounts.listRegistryMounts?.();
      expect(mounts?.some((mount) => mount.id === created!.id)).toBe(false);
    });
  });

  describe('mounts.getEvalReport', () => {
    it('returns a populated eval report', async () => {
      const svc = createMimirMockAdapter();
      const report = await svc.mounts.getEvalReport();
      expect(report).not.toBeNull();
      expect(report!.overall.precisionAt5).toBeGreaterThan(0);
      expect(report!.overall.mrr).toBeGreaterThan(0);
      expect(report!.overall.recallAt10).toBeGreaterThan(0);
      expect(report!.queryCount).toBeGreaterThan(0);
      expect(Object.keys(report!.byCategory).length).toBeGreaterThan(0);
    });
  });

  describe('mounts.getQueryStats', () => {
    it('returns query stats with a recent log', async () => {
      const svc = createMimirMockAdapter();
      const stats = await svc.mounts.getQueryStats();
      expect(stats).not.toBeNull();
      expect(stats!.total).toBeGreaterThan(0);
      expect(stats!.recent.length).toBeGreaterThan(0);
      expect(stats!.recent.some((entry) => entry.resultCount === 0)).toBe(true);
    });
  });

  describe('pages.getStats', () => {
    it('returns healthy flag and non-zero pageCount', async () => {
      const svc = createMimirMockAdapter();
      const stats = await svc.pages.getStats();
      expect(stats.pageCount).toBeGreaterThan(0);
      expect(typeof stats.healthy).toBe('boolean');
      expect(Array.isArray(stats.categories)).toBe(true);
    });
  });

  describe('pages.listPages', () => {
    it('returns all pages when no filter is given', async () => {
      const svc = createMimirMockAdapter();
      const pages = await svc.pages.listPages();
      expect(pages.length).toBeGreaterThan(0);
    });

    it('filters by category', async () => {
      const svc = createMimirMockAdapter();
      const pages = await svc.pages.listPages({ category: 'arch' });
      expect(pages.length).toBeGreaterThan(0);
      expect(pages.every((p) => p.category === 'arch')).toBe(true);
    });

    it('filters by mountName', async () => {
      const svc = createMimirMockAdapter();
      const pages = await svc.pages.listPages({ mountName: 'local' });
      expect(pages.every((p) => p.mounts.includes('local'))).toBe(true);
    });

    it('each result has PageMeta fields', async () => {
      const svc = createMimirMockAdapter();
      const pages = await svc.pages.listPages();
      for (const p of pages) {
        expect(p).toHaveProperty('path');
        expect(p).toHaveProperty('title');
        expect(p).toHaveProperty('type');
        expect(p).toHaveProperty('confidence');
        expect(p).toHaveProperty('mounts');
      }
    });
  });

  describe('pages.getPage', () => {
    it('returns a page for a known path', async () => {
      const svc = createMimirMockAdapter();
      const page = await svc.pages.getPage('/arch/overview');
      expect(page).not.toBeNull();
      expect(page!.path).toBe('/arch/overview');
    });

    it('returns null for an unknown path', async () => {
      const svc = createMimirMockAdapter();
      const page = await svc.pages.getPage('/does-not-exist');
      expect(page).toBeNull();
    });

    it('returned page may have zones', async () => {
      const svc = createMimirMockAdapter();
      const page = await svc.pages.getPage('/arch/overview');
      expect(page).not.toBeNull();
      expect(page!.zones).toBeDefined();
      expect(page!.zones!.length).toBeGreaterThan(0);
    });
  });

  describe('pages.upsertPage', () => {
    it('resolves without error', async () => {
      const svc = createMimirMockAdapter();
      await expect(svc.pages.upsertPage('/test/new', '# New')).resolves.toBeUndefined();
    });
  });

  describe('pages.search', () => {
    it('returns results matching the query', async () => {
      const svc = createMimirMockAdapter();
      const results = await svc.pages.search('architecture');
      expect(results.length).toBeGreaterThan(0);
      expect(results[0]).toHaveProperty('path');
      expect(results[0]).toHaveProperty('type');
      expect(results[0]).toHaveProperty('confidence');
    });

    it('returns empty array for unmatched query', async () => {
      const svc = createMimirMockAdapter();
      const results = await svc.pages.search('xyzxyzxyz_no_match');
      expect(results).toHaveLength(0);
    });

    it('omits score breakdowns by default', async () => {
      const svc = createMimirMockAdapter();
      const results = await svc.pages.search('architecture');
      expect(results[0]?.scoreBreakdown).toBeUndefined();
    });

    it('returns per-factor score breakdowns in debug mode', async () => {
      const svc = createMimirMockAdapter();
      const results = await svc.pages.search('architecture', 'hybrid', undefined, true);
      expect(results.length).toBeGreaterThan(0);
      for (const result of results) {
        const breakdown = result.scoreBreakdown!;
        expect(breakdown).toBeDefined();
        for (const factor of [
          'base',
          'recency',
          'title_match',
          'confidence',
          'backlinks',
          'zone',
          'final',
        ]) {
          expect(typeof breakdown[factor]).toBe('number');
        }
        expect(breakdown['final']).toBeCloseTo(result.score!, 2);
      }
    });
  });

  describe('embeddings.semanticSearch', () => {
    it('returns results with score and mountName', async () => {
      const svc = createMimirMockAdapter();
      const results = await svc.embeddings.semanticSearch('architecture');
      expect(results.length).toBeGreaterThan(0);
      for (const r of results) {
        expect(r).toHaveProperty('score');
        expect(r).toHaveProperty('mountName');
        expect(r.score).toBeGreaterThan(0);
        expect(r.score).toBeLessThanOrEqual(1);
      }
    });

    it('respects topK limit', async () => {
      const svc = createMimirMockAdapter();
      const results = await svc.embeddings.semanticSearch('anything', 1);
      expect(results.length).toBeLessThanOrEqual(1);
    });
  });

  describe('pages.getGraph', () => {
    it('returns a graph with nodes and edges', async () => {
      const svc = createMimirMockAdapter();
      const graph = await svc.pages.getGraph();
      expect(graph.nodes.length).toBeGreaterThan(0);
      expect(Array.isArray(graph.edges)).toBe(true);
    });

    it('each node has id, title, and category', async () => {
      const svc = createMimirMockAdapter();
      const graph = await svc.pages.getGraph();
      for (const node of graph.nodes) {
        expect(node).toHaveProperty('id');
        expect(node).toHaveProperty('title');
        expect(node).toHaveProperty('category');
      }
    });

    it('each edge has source and target', async () => {
      const svc = createMimirMockAdapter();
      const graph = await svc.pages.getGraph();
      for (const edge of graph.edges) {
        expect(edge).toHaveProperty('source');
        expect(edge).toHaveProperty('target');
      }
    });

    it('filters by mountName — only includes pages from that mount', async () => {
      const svc = createMimirMockAdapter();
      const graph = await svc.pages.getGraph({ mountName: 'local' });
      // All nodes returned must correspond to pages on the local mount
      expect(Array.isArray(graph.nodes)).toBe(true);
    });

    it('returns fewer nodes when scoped to a single mount', async () => {
      const svc = createMimirMockAdapter();
      const all = await svc.pages.getGraph();
      const scoped = await svc.pages.getGraph({ mountName: 'local' });
      expect(scoped.nodes.length).toBeLessThanOrEqual(all.nodes.length);
    });

    it('returns a convincing demo-sized graph (600-900 pages)', async () => {
      const svc = createMimirMockAdapter();
      const graph = await svc.pages.getGraph();
      expect(graph.nodes.length).toBeGreaterThanOrEqual(600);
      expect(graph.nodes.length).toBeLessThanOrEqual(900);
      expect(graph.edges.length).toBeGreaterThan(graph.nodes.length);
    });

    it('scoping to a mount only returns nodes on that mount', async () => {
      const svc = createMimirMockAdapter();
      const scoped = await svc.pages.getGraph({ mountName: 'forge' });
      expect(scoped.nodes.length).toBeGreaterThan(0);
      for (const node of scoped.nodes) {
        expect(node.mount).toBe('forge');
      }
      const scopedIds = new Set(scoped.nodes.map((n) => n.id));
      for (const edge of scoped.edges) {
        expect(scopedIds.has(edge.source)).toBe(true);
        expect(scopedIds.has(edge.target)).toBe(true);
      }
    });

    it('nodes carry kind, updatedAt, firstSeen, and confidence', async () => {
      const svc = createMimirMockAdapter();
      const graph = await svc.pages.getGraph();
      for (const node of graph.nodes) {
        expect(typeof node.kind).toBe('string');
        expect(typeof node.updatedAt).toBe('string');
        expect(typeof node.firstSeen).toBe('string');
        expect(node.confidence === null || typeof node.confidence === 'string').toBe(true);
      }
    });

    it('includes at least one contradiction edge', async () => {
      const svc = createMimirMockAdapter();
      const graph = await svc.pages.getGraph();
      const contradictionTypes = new Set(['contradicts', 'disagrees_with', 'conflicts_with']);
      expect(graph.edges.some((e) => contradictionTypes.has(e.type ?? ''))).toBe(true);
    });
  });

  describe('pages.getLiveActivity', () => {
    it('returns recent read/write events', async () => {
      const svc = createMimirMockAdapter();
      const events = await svc.pages.getLiveActivity();
      expect(events.length).toBeGreaterThan(0);
      for (const event of events) {
        expect(['read', 'write']).toContain(event.kind);
        expect(typeof event.mount).toBe('string');
        expect(typeof event.path).toBe('string');
      }
    });

    it('respects the since filter', async () => {
      const svc = createMimirMockAdapter();
      const all = await svc.pages.getLiveActivity();
      const cutoff = all[Math.floor(all.length / 2)]!.timestamp;
      const filtered = await svc.pages.getLiveActivity({ since: cutoff });
      expect(filtered.every((e) => e.timestamp >= cutoff)).toBe(true);
    });
  });

  describe('pages.listEntities', () => {
    it('returns a non-empty list of entities', async () => {
      const svc = createMimirMockAdapter();
      const entities = await svc.pages.listEntities();
      expect(entities.length).toBeGreaterThan(0);
    });

    it('each entity has required EntityMeta fields', async () => {
      const svc = createMimirMockAdapter();
      const entities = await svc.pages.listEntities();
      for (const e of entities) {
        expect(e).toHaveProperty('path');
        expect(e).toHaveProperty('title');
        expect(e).toHaveProperty('entityKind');
        expect(e).toHaveProperty('summary');
        expect(e).toHaveProperty('relationshipCount');
      }
    });

    it('filters by kind — only returns entities of that kind', async () => {
      const svc = createMimirMockAdapter();
      const orgs = await svc.pages.listEntities({ kind: 'org' });
      expect(orgs.every((e) => e.entityKind === 'org')).toBe(true);
    });

    it('returns fewer entities when filtered by kind', async () => {
      const svc = createMimirMockAdapter();
      const all = await svc.pages.listEntities();
      const concepts = await svc.pages.listEntities({ kind: 'concept' });
      expect(concepts.length).toBeLessThanOrEqual(all.length);
    });

    it('includes entities with various kinds', async () => {
      const svc = createMimirMockAdapter();
      const entities = await svc.pages.listEntities();
      const kinds = new Set(entities.map((e) => e.entityKind));
      expect(kinds.size).toBeGreaterThan(1);
    });
  });

  describe('pages.ingestUrl', () => {
    it('returns a source with web originType and the given URL', async () => {
      const svc = createMimirMockAdapter();
      const source = await svc.pages.ingestUrl('https://example.com/doc');
      expect(source.originType).toBe('web');
      expect(source.originUrl).toBe('https://example.com/doc');
      expect(source.id).toBeDefined();
      expect(source.ingestedAt).toBeDefined();
    });

    it('appends the new source to the list', async () => {
      const svc = createMimirMockAdapter();
      const before = await svc.pages.listSources();
      await svc.pages.ingestUrl('https://example.com/new');
      const after = await svc.pages.listSources();
      expect(after.length).toBe(before.length + 1);
    });

    it('newly ingested source appears first in the list', async () => {
      const svc = createMimirMockAdapter();
      const source = await svc.pages.ingestUrl('https://example.com/newest');
      const all = await svc.pages.listSources();
      expect(all[0]!.id).toBe(source.id);
    });
  });

  describe('pages.ingestFile', () => {
    it('returns a source with file originType and the file name', async () => {
      const svc = createMimirMockAdapter();
      const file = new File(['# hello'], 'test.md', { type: 'text/markdown' });
      const source = await svc.pages.ingestFile(file);
      expect(source.originType).toBe('file');
      expect(source.originPath).toBe('test.md');
      expect(source.id).toBeDefined();
    });

    it('appends the new file source to the list', async () => {
      const svc = createMimirMockAdapter();
      const before = await svc.pages.listSources();
      const file = new File(['content'], 'notes.txt', { type: 'text/plain' });
      await svc.pages.ingestFile(file);
      const after = await svc.pages.listSources();
      expect(after.length).toBe(before.length + 1);
    });
  });
});
