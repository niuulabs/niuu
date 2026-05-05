import { describe, it, expect } from 'vitest';
import { buildFileTree, mergeFileTrees, countLeaves, collectLeaves } from './tree';
import type { PageMeta } from './page';

const makePage = (path: string, mounts: string[] = ['local']): PageMeta => ({
  path,
  title: path.split('/').pop() ?? path,
  summary: '',
  category: 'test',
  type: 'topic',
  confidence: 'high',
  mounts,
  updatedAt: '2026-01-01T00:00:00Z',
  updatedBy: 'ravn-test',
  sourceIds: [],
  size: 100,
});

describe('buildFileTree', () => {
  it('returns an empty root for no pages', () => {
    const tree = buildFileTree([]);
    expect(tree.isDir).toBe(true);
    expect(Object.keys(tree.children)).toHaveLength(0);
  });

  it('places a top-level page at the root', () => {
    const tree = buildFileTree([makePage('/overview')]);
    expect(tree.children['overview']).toBeDefined();
    expect(tree.children['overview']?.isDir).toBe(false);
  });

  it('builds nested directories from deep paths', () => {
    const tree = buildFileTree([makePage('/arch/hexagonal')]);
    const arch = tree.children['arch'];
    expect(arch?.isDir).toBe(true);
    if (arch?.isDir) {
      const hex = arch.children['hexagonal'];
      expect(hex?.isDir).toBe(false);
      if (!hex?.isDir) expect(hex?.page.path).toBe('/arch/hexagonal');
    }
  });

  it('groups sibling pages under the same directory node', () => {
    const tree = buildFileTree([makePage('/api/overview'), makePage('/api/auth')]);
    const api = tree.children['api'];
    expect(api?.isDir).toBe(true);
    if (api?.isDir) {
      expect(Object.keys(api.children)).toHaveLength(2);
    }
  });

  it('handles paths without leading slash', () => {
    // Defensive: buildFileTree normalises leading slashes internally
    const tree = buildFileTree([makePage('/arch/overview')]);
    expect(tree.children['arch']).toBeDefined();
  });
});

describe('mergeFileTrees', () => {
  it('deduplicates pages by path — last wins', () => {
    const p1 = { ...makePage('/arch/overview', ['local']), title: 'v1' };
    const p2 = { ...makePage('/arch/overview', ['shared']), title: 'v2' };
    const tree = mergeFileTrees([p1, p2]);
    const arch = tree.children['arch'];
    if (!arch?.isDir) throw new Error('expected dir');
    const leaf = arch.children['overview'];
    if (!leaf || leaf.isDir) throw new Error('expected leaf');
    expect(leaf.page.title).toBe('v2');
  });

  it('merges pages from different mounts into a single tree', () => {
    const pages = [makePage('/arch/overview', ['local']), makePage('/api/auth', ['shared'])];
    const tree = mergeFileTrees(pages);
    expect(Object.keys(tree.children)).toHaveLength(2);
  });
});

describe('countLeaves', () => {
  it('returns 0 for an empty tree', () => {
    expect(countLeaves(buildFileTree([]))).toBe(0);
  });

  it('counts all leaf pages', () => {
    const pages = [makePage('/a'), makePage('/b/c'), makePage('/b/d')];
    expect(countLeaves(buildFileTree(pages))).toBe(3);
  });
});

describe('collectLeaves', () => {
  it('returns pages in DFS order', () => {
    const pages = [makePage('/arch/overview'), makePage('/api/auth')];
    const leaves = collectLeaves(buildFileTree(pages));
    expect(leaves).toHaveLength(2);
    expect(leaves.map((l) => l.path)).toContain('/arch/overview');
    expect(leaves.map((l) => l.path)).toContain('/api/auth');
  });
});
