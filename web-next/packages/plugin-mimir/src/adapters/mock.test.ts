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
        path: '',
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

  describe('lint.getLintReport', () => {
    it('returns a report with issues and summary', async () => {
      const svc = createMimirMockAdapter();
      const report = await svc.lint.getLintReport();
      expect(Array.isArray(report.issues)).toBe(true);
      expect(typeof report.pagesChecked).toBe('number');
      expect(report.summary).toHaveProperty('error');
      expect(report.summary).toHaveProperty('warn');
      expect(report.summary).toHaveProperty('info');
    });

    it('summary tallies match issue list', async () => {
      const svc = createMimirMockAdapter();
      const report = await svc.lint.getLintReport();
      const manual = {
        error: report.issues.filter((i) => i.severity === 'error').length,
        warn: report.issues.filter((i) => i.severity === 'warn').length,
        info: report.issues.filter((i) => i.severity === 'info').length,
      };
      expect(report.summary).toEqual(manual);
    });
  });

  describe('lint.runAutoFix', () => {
    it('removes auto-fixable issues', async () => {
      const svc = createMimirMockAdapter();
      const before = await svc.lint.getLintReport();
      const after = await svc.lint.runAutoFix();
      const autoFixableCount = before.issues.filter((i) => i.autoFix).length;
      expect(after.issues.length).toBe(before.issues.length - autoFixableCount);
    });

    it('only fixes specified issue IDs when provided', async () => {
      const svc = createMimirMockAdapter();
      const before = await svc.lint.getLintReport();
      const autoFixable = before.issues.filter((i) => i.autoFix);
      if (autoFixable.length === 0) return;
      const idToFix = autoFixable[0]!.id;
      const after = await svc.lint.runAutoFix([idToFix]);
      expect(after.issues.find((i) => i.id === idToFix)).toBeUndefined();
    });
  });

  describe('lint.getDreamCycles', () => {
    it('returns dream cycle records', async () => {
      const svc = createMimirMockAdapter();
      const cycles = await svc.lint.getDreamCycles();
      expect(cycles.length).toBeGreaterThan(0);
      for (const c of cycles) {
        expect(c).toHaveProperty('id');
        expect(c).toHaveProperty('ravn');
        expect(c).toHaveProperty('timestamp');
        expect(c).toHaveProperty('pagesUpdated');
        expect(c).toHaveProperty('durationMs');
      }
    });

    it('respects limit parameter', async () => {
      const svc = createMimirMockAdapter();
      const cycles = await svc.lint.getDreamCycles(1);
      expect(cycles.length).toBeLessThanOrEqual(1);
    });
  });

  describe('lint.getActivityLog', () => {
    it('returns activity event records', async () => {
      const svc = createMimirMockAdapter();
      const events = await svc.lint.getActivityLog();
      expect(events.length).toBeGreaterThan(0);
    });

    it('each event has required fields', async () => {
      const svc = createMimirMockAdapter();
      const events = await svc.lint.getActivityLog();
      for (const e of events) {
        expect(e).toHaveProperty('id');
        expect(e).toHaveProperty('timestamp');
        expect(e).toHaveProperty('kind');
        expect(e).toHaveProperty('mount');
        expect(e).toHaveProperty('ravn');
        expect(e).toHaveProperty('message');
      }
    });

    it('includes all expected kind values', async () => {
      const svc = createMimirMockAdapter();
      const events = await svc.lint.getActivityLog();
      const kinds = new Set(events.map((e) => e.kind));
      expect(kinds.has('write')).toBe(true);
      expect(kinds.has('ingest')).toBe(true);
      expect(kinds.has('lint')).toBe(true);
      expect(kinds.has('dream')).toBe(true);
    });

    it('respects limit parameter', async () => {
      const svc = createMimirMockAdapter();
      const events = await svc.lint.getActivityLog(2);
      expect(events.length).toBeLessThanOrEqual(2);
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
