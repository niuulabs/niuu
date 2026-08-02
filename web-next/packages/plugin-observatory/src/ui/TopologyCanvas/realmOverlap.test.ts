import { describe, it, expect } from 'vitest';
import { computeLayout } from './layoutEngine';
import { realmBounds } from './renderer';
import type { Topology, TopologyNode } from '../../domain';

function node(id: string, typeId: string, parentId: string | null = null): TopologyNode {
  return { id, typeId, label: id, parentId, status: 'healthy' } as TopologyNode;
}

/**
 * A realm is drawn as a rectangle but was spaced as a circle, so the corners
 * of a wide realm crossed into its neighbour. This is the regression guard:
 * the shape the renderer draws has to fit the space the layout reserved.
 */
describe('realm hulls', () => {
  it('do not overlap, however unevenly the realms are filled', () => {
    const nodes: TopologyNode[] = [];
    // Five realms, one of them holding four clusters and the rest holding one.
    for (let r = 0; r < 5; r += 1) {
      nodes.push(node(`realm-${r}`, 'realm'));
      const clusters = r === 0 ? 4 : 1;
      for (let c = 0; c < clusters; c += 1) {
        nodes.push(node(`c-${r}-${c}`, 'cluster', `realm-${r}`));
        for (let s = 0; s < 6; s += 1) {
          nodes.push(node(`s-${r}-${c}-${s}`, 'service', `c-${r}-${c}`));
        }
      }
    }
    const topology: Topology = { timestamp: '2026-08-02T00:00:00Z', nodes, edges: [] };
    const positions = computeLayout(topology);

    const hulls = nodes
      .filter((n) => n.typeId === 'realm')
      .map((r) => ({ id: r.id, box: realmBounds(r, nodes, positions) }))
      .filter((h): h is { id: string; box: NonNullable<typeof h.box> } => h.box !== null);

    const overlaps: string[] = [];
    for (let i = 0; i < hulls.length; i += 1) {
      for (let j = i + 1; j < hulls.length; j += 1) {
        const a = hulls[i]!.box;
        const b = hulls[j]!.box;
        if (a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1) {
          overlaps.push(`${hulls[i]!.id}×${hulls[j]!.id}`);
        }
      }
    }

    expect(hulls).toHaveLength(5);
    expect(overlaps).toEqual([]);
  });
});
