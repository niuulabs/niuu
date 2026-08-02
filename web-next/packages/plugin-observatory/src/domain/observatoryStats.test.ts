import { describe, expect, it } from 'vitest';
import { deriveObservatoryStats, nodesOfType } from './observatoryStats';
import type { Topology, TopologyNode } from './index';

function node(id: string, typeId: string, extra: Record<string, unknown> = {}): TopologyNode {
  return { id, typeId, label: id, parentId: null, status: 'healthy', ...extra } as TopologyNode;
}

function topology(nodes: TopologyNode[]): Topology {
  return { nodes, edges: [], timestamp: '2026-08-02T00:00:00Z' };
}

describe('deriveObservatoryStats', () => {
  it('counts each kind the readout shows', () => {
    const stats = deriveObservatoryStats(
      topology([
        node('r', 'realm'),
        node('c', 'cluster'),
        node('h1', 'host'),
        node('h2', 'host'),
        node('m', 'mimir'),
        node('res', 'ravn_long'),
        node('v', 'valkyrie'),
      ]),
    );

    expect(stats).toMatchObject({ realms: 1, clusters: 1, hosts: 2, mimirs: 1, residents: 2 });
  });

  it('reports undiscovered pods as null rather than zero', () => {
    // "No pod data" and "no pods" are different claims.
    expect(deriveObservatoryStats(topology([node('h', 'host')])).pods).toBeNull();
  });

  it('sums pod counts when hosts carry them', () => {
    const stats = deriveObservatoryStats(
      topology([node('h1', 'host', { pods: 12 }), node('h2', 'host', { pods: 30 })]),
    );

    expect(stats.pods).toBe(42);
  });

  it('counts nothing before a snapshot arrives', () => {
    // Not "an estate with no realms" — "no answer yet". The readout draws a
    // dash for null, and that distinction is the point of the whole type.
    expect(deriveObservatoryStats(null)).toMatchObject({
      realms: null,
      clusters: null,
      pods: null,
    });
  });

  it('counts zero once a snapshot says the estate is empty', () => {
    expect(deriveObservatoryStats(topology([]))).toMatchObject({
      realms: 0,
      clusters: 0,
      pods: null,
    });
  });
});

describe('nodesOfType', () => {
  it('filters and orders by label for a stable rail', () => {
    const list = nodesOfType(
      topology([node('zeta', 'mimir'), node('alpha', 'mimir'), node('x', 'host')]),
      ['mimir'],
    );

    expect(list.map((n) => n.id)).toEqual(['alpha', 'zeta']);
  });

  it('returns nothing for an absent topology', () => {
    expect(nodesOfType(null, ['mimir'])).toEqual([]);
  });
});
