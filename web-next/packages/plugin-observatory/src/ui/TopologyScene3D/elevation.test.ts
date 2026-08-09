import { describe, expect, it } from 'vitest';
import type { TopologyNode } from '../../domain';
import { CONTAINER_TYPES, elevationFor, isContainerType } from './elevation';
import { TIER } from './scene3dConfig';

function node(typeId: string, parentId: string | null = null): TopologyNode {
  return { id: `${typeId}-1`, typeId, label: typeId, parentId, status: 'healthy' };
}

describe('elevationFor', () => {
  it('puts each level of the containment chain on its own deck', () => {
    // The ordering is the point: if any two of these collapse together, the
    // 3D view stops saying anything the plan does not already say.
    const decks = [
      elevationFor(node('realm')),
      elevationFor(node('cluster')),
      elevationFor(node('namespace')),
      elevationFor(node('host')),
      elevationFor(node('ravn_long')),
    ];
    for (let i = 1; i < decks.length; i += 1) {
      expect(decks[i]!).toBeGreaterThan(decks[i - 1]!);
    }
  });

  it('stands a workflow session on the same deck as a host', () => {
    // Both are a place and a thing; putting them on different decks would
    // claim a containment relationship that does not exist.
    expect(elevationFor(node('run'))).toBe(elevationFor(node('host')));
  });

  it('sends the estate’s Mímir below the floor, because it is a well', () => {
    expect(elevationFor(node('mimir'))).toBe(TIER.MIMIR);
    expect(elevationFor(node('mimir'))).toBeLessThan(TIER.FLOOR);
  });

  it('stands a Mímir inside a cluster on the leaf deck instead', () => {
    // Sinking it would drive the shaft through the plate of the very cluster
    // that owns it.
    expect(elevationFor(node('mimir', 'cluster-1'))).toBe(TIER.LEAF);
  });

  it('puts an unknown type on the leaf deck rather than the floor', () => {
    expect(elevationFor(node('something-new'))).toBe(TIER.LEAF);
  });

  it('agrees with itself about what a container is', () => {
    for (const typeId of CONTAINER_TYPES) expect(isContainerType(typeId)).toBe(true);
    expect(isContainerType('ravn_long')).toBe(false);
  });
});
