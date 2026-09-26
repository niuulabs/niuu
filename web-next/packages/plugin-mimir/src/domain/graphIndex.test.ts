import { describe, it, expect } from 'vitest';
import { pyQuote, encodeNodeId, nodeIndex } from './graphIndex';
import type { GraphNode, MimirGraph } from './api-types';

function node(overrides: Partial<GraphNode>): GraphNode {
  return {
    id: overrides.id ?? 'platform:%2Fa',
    title: 'A',
    category: 'infra',
    kind: 'topic',
    path: '/a',
    mount: 'platform',
    updatedAt: '2026-01-01T00:00:00Z',
    firstSeen: '2026-01-01T00:00:00Z',
    confidence: 'high',
    ...overrides,
  };
}

describe('pyQuote', () => {
  it('leaves unreserved characters untouched', () => {
    expect(pyQuote('platform')).toBe('platform');
    expect(pyQuote('realm-valhalla')).toBe('realm-valhalla');
  });

  it('percent-encodes slashes (safe="" means nothing is exempt)', () => {
    expect(pyQuote('/infra/gateway')).toBe('%2Finfra%2Fgateway');
  });

  it('percent-encodes characters encodeURIComponent leaves alone but Python quote does not exempt', () => {
    expect(pyQuote("it's (a) test*")).toBe('it%27s%20%28a%29%20test%2A');
  });
});

describe('encodeNodeId', () => {
  it('joins the quoted mount and path with a literal colon', () => {
    expect(encodeNodeId('platform', '/infra/gateway')).toBe('platform:%2Finfra%2Fgateway');
  });

  it('keeps two mounts with the same path distinct', () => {
    expect(encodeNodeId('platform', '/a')).not.toBe(encodeNodeId('shared', '/a'));
  });
});

describe('nodeIndex', () => {
  const a = node({ id: 'platform:%2Fa', path: '/a', mount: 'platform' });
  const b = node({ id: 'shared:%2Fa', path: '/a', mount: 'shared', title: 'B' });
  const graph: MimirGraph = { nodes: [a, b], edges: [] };
  const index = nodeIndex(graph);

  it('looks up by id', () => {
    expect(index.byId('platform:%2Fa')).toBe(a);
    expect(index.byId('shared:%2Fa')).toBe(b);
    expect(index.byId('missing')).toBeUndefined();
  });

  it('looks up by (mount, path), never colliding across mounts with the same path', () => {
    expect(index.byMountPath('platform', '/a')).toBe(a);
    expect(index.byMountPath('shared', '/a')).toBe(b);
    expect(index.byMountPath('shared', '/missing')).toBeUndefined();
  });

  it('skips nodes with no mount or path when indexing by (mount, path)', () => {
    const bare = node({ id: 'bare', mount: '', path: '' });
    const idx = nodeIndex({ nodes: [bare], edges: [] });
    expect(idx.byId('bare')).toBe(bare);
    expect(idx.byMountPath('', '')).toBeUndefined();
  });
});
