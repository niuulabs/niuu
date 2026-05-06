import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPut = vi.fn();

vi.mock('@/modules/shared/api/client', () => ({
  createApiClient: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    put: (...args: unknown[]) => mockPut(...args),
  }),
}));

import {
  getStats,
  listPages,
  getPage,
  search,
  getLog,
  getLint,
  lintFix,
  upsertPage,
  getGraph,
  ingest,
} from './client';

describe('mimir API client', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('getStats', () => {
    it('maps raw stats to domain model', async () => {
      mockGet.mockResolvedValue({
        page_count: 42,
        categories: ['infra', 'api'],
        healthy: true,
      });

      const result = await getStats();

      expect(mockGet).toHaveBeenCalledWith('/stats');
      expect(result).toEqual({
        pageCount: 42,
        categories: ['infra', 'api'],
        healthy: true,
      });
    });
  });

  describe('listPages', () => {
    it('lists all pages without category filter', async () => {
      mockGet.mockResolvedValue([
        {
          path: '/arch/overview',
          title: 'Architecture Overview',
          summary: 'Main overview',
          category: 'infra',
          updated_at: '2026-04-01T10:00:00Z',
          source_ids: ['src-1'],
        },
      ]);

      const result = await listPages();

      expect(mockGet).toHaveBeenCalledWith('/pages');
      expect(result).toHaveLength(1);
      expect(result[0]).toEqual({
        path: '/arch/overview',
        title: 'Architecture Overview',
        summary: 'Main overview',
        category: 'infra',
        updatedAt: '2026-04-01T10:00:00Z',
        sourceIds: ['src-1'],
      });
    });

    it('filters pages by category', async () => {
      mockGet.mockResolvedValue([]);

      await listPages('infra');

      expect(mockGet).toHaveBeenCalledWith('/pages?category=infra');
    });

    it('defaults sourceIds to empty array when absent', async () => {
      mockGet.mockResolvedValue([
        {
          path: '/test',
          title: 'Test',
          summary: 'Sum',
          category: 'cat',
          updated_at: '2026-01-01T00:00:00Z',
        },
      ]);

      const result = await listPages();
      expect(result[0].sourceIds).toEqual([]);
    });
  });

  describe('getPage', () => {
    it('fetches and maps a single page', async () => {
      mockGet.mockResolvedValue({
        path: '/arch/api',
        title: 'API Design',
        summary: 'API guidelines',
        category: 'api',
        updated_at: '2026-03-15T12:00:00Z',
        content: '# API Design\n\nGuidelines here.',
      });

      const result = await getPage('/arch/api');

      expect(mockGet).toHaveBeenCalledWith('/page?path=%2Farch%2Fapi');
      expect(result.path).toBe('/arch/api');
      expect(result.content).toContain('# API Design');
      expect(result.updatedAt).toBe('2026-03-15T12:00:00Z');
      expect(result.sourceIds).toEqual([]);
    });
  });

  describe('search', () => {
    it('maps search results', async () => {
      mockGet.mockResolvedValue([
        {
          path: '/infra/k8s',
          title: 'Kubernetes',
          summary: 'K8s deployment guide',
          category: 'infra',
          updated_at: '2026-02-01T00:00:00Z',
        },
      ]);

      const result = await search('kubernetes');

      expect(mockGet).toHaveBeenCalledWith('/search?q=kubernetes');
      expect(result).toHaveLength(1);
      expect(result[0]).toEqual({
        path: '/infra/k8s',
        title: 'Kubernetes',
        summary: 'K8s deployment guide',
        category: 'infra',
      });
    });
  });

  describe('getLog', () => {
    it('fetches log with default count', async () => {
      mockGet.mockResolvedValue({
        raw: 'line1\nline2',
        entries: ['line1', 'line2'],
      });

      const result = await getLog();

      expect(mockGet).toHaveBeenCalledWith('/log?n=50');
      expect(result).toEqual({
        raw: 'line1\nline2',
        entries: ['line1', 'line2'],
      });
    });

    it('fetches log with custom count', async () => {
      mockGet.mockResolvedValue({ raw: '', entries: [] });

      await getLog(100);

      expect(mockGet).toHaveBeenCalledWith('/log?n=100');
    });
  });

  describe('getLint', () => {
    it('maps lint report', async () => {
      mockGet.mockResolvedValue({
        issues: [
          {
            id: 'lint-1',
            severity: 'warning',
            message: 'Missing summary',
            page_path: '/arch/api',
            auto_fixable: true,
          },
        ],
        pages_checked: 10,
        issues_found: true,
        summary: { error: 0, warning: 1, info: 0 },
      });

      const result = await getLint();

      expect(mockGet).toHaveBeenCalledWith('/lint');
      expect(result.pagesChecked).toBe(10);
      expect(result.issuesFound).toBe(true);
      expect(result.issues).toHaveLength(1);
      expect(result.issues[0]).toEqual({
        id: 'lint-1',
        severity: 'warning',
        message: 'Missing summary',
        pagePath: '/arch/api',
        autoFixable: true,
      });
    });
  });

  describe('lintFix', () => {
    it('posts lint fix and maps result', async () => {
      mockPost.mockResolvedValue({
        issues: [],
        pages_checked: 10,
        issues_found: false,
        summary: { error: 0, warning: 0, info: 0 },
      });

      const result = await lintFix();

      expect(mockPost).toHaveBeenCalledWith('/lint/fix', {});
      expect(result.issuesFound).toBe(false);
    });
  });

  describe('upsertPage', () => {
    it('puts page content', async () => {
      mockPut.mockResolvedValue(undefined);

      await upsertPage('/test/page', '# New Content');

      expect(mockPut).toHaveBeenCalledWith('/page', {
        path: '/test/page',
        content: '# New Content',
      });
    });
  });

  describe('getGraph', () => {
    it('maps graph nodes and edges', async () => {
      mockGet.mockResolvedValue({
        nodes: [
          { id: 'n1', title: 'Node 1', category: 'infra' },
          { id: 'n2', title: 'Node 2', category: 'api' },
        ],
        edges: [{ source: 'n1', target: 'n2' }],
      });

      const result = await getGraph();

      expect(mockGet).toHaveBeenCalledWith('/graph');
      expect(result.nodes).toHaveLength(2);
      expect(result.nodes[0]).toEqual({ id: 'n1', title: 'Node 1', category: 'infra' });
      expect(result.edges).toHaveLength(1);
      expect(result.edges[0]).toEqual({ source: 'n1', target: 'n2' });
    });
  });

  describe('ingest', () => {
    it('posts ingest request and maps response', async () => {
      mockPost.mockResolvedValue({
        source_id: 'src-abc',
        pages_updated: ['/arch/new-page'],
      });

      const result = await ingest({
        title: 'New doc',
        content: 'Some content',
        sourceType: 'document',
        originUrl: 'https://example.com/doc',
      });

      expect(mockPost).toHaveBeenCalledWith('/ingest', {
        title: 'New doc',
        content: 'Some content',
        source_type: 'document',
        origin_url: 'https://example.com/doc',
      });
      expect(result).toEqual({
        sourceId: 'src-abc',
        pagesUpdated: ['/arch/new-page'],
      });
    });
  });
});
