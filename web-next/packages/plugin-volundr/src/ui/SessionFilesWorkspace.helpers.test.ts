import { describe, expect, it } from 'vitest';
import type { FileTreeNode } from '../ports/IFileSystemPort';
import {
  buildBreadcrumbs,
  buildIndex,
  cloneNode,
  formatSize,
  joinPath,
  normalizeRoots,
  sortEntries,
} from './SessionFilesWorkspace';

const TREE: FileTreeNode[] = [
  {
    name: 'src',
    path: '/workspace/src',
    kind: 'directory',
    children: [{ name: 'main.ts', path: '/workspace/src/main.ts', kind: 'file', size: 128 }],
  },
  { name: 'README.md', path: '/workspace/README.md', kind: 'file', size: 2048 },
  {
    name: 'secrets',
    path: '/mnt/secrets',
    kind: 'directory',
    mountName: 'secrets',
    children: [{ name: 'token', path: '/mnt/secrets/token', kind: 'file', isSecret: true }],
  },
];

describe('SessionFilesWorkspace helpers', () => {
  it('normalizes roots and deep-clones mount trees', () => {
    const roots = normalizeRoots(TREE);
    expect(roots.map((node) => node.path)).toEqual(['/workspace', '/mnt/secrets']);
    expect(roots[0]?.children?.map((node) => node.name)).toEqual(['src', 'README.md']);

    const cloned = cloneNode(TREE[0]!);
    expect(cloned).not.toBe(TREE[0]);
    expect(cloned.children).not.toBe(TREE[0]?.children);
  });

  it('indexes nodes and builds breadcrumbs for nested paths', () => {
    const index = buildIndex(normalizeRoots(TREE));
    expect(index.get('/workspace/src/main.ts')?.name).toBe('main.ts');
    expect(buildBreadcrumbs('/workspace/src/main.ts', index)).toEqual([
      { path: '/workspace', name: 'workspace' },
      { path: '/workspace/src', name: 'src' },
      { path: '/workspace/src/main.ts', name: 'main.ts' },
    ]);
    expect(buildBreadcrumbs('/mnt/secrets/token', index)).toEqual([
      { path: '/mnt/secrets', name: 'secrets' },
      { path: '/mnt/secrets/token', name: 'token' },
    ]);
  });

  it('joins paths, formats file sizes, and sorts directories before files', () => {
    expect(joinPath('/workspace', 'src')).toBe('/workspace/src');
    expect(joinPath('/workspace/', 'src')).toBe('/workspace/src');

    expect(formatSize(512)).toBe('512 B');
    expect(formatSize(2048)).toBe('2 KB');
    expect(formatSize(2 * 1024 * 1024)).toBe('2.0 MB');

    const sorted = sortEntries([
      { name: 'zeta.txt', path: '/workspace/zeta.txt', kind: 'file' },
      { name: 'alpha', path: '/workspace/alpha', kind: 'directory' },
      { name: 'beta.txt', path: '/workspace/beta.txt', kind: 'file' },
    ]);
    expect(sorted.map((node) => node.name)).toEqual(['alpha', 'beta.txt', 'zeta.txt']);
  });
});
