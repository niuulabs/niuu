import { describe, it, expect } from 'vitest';
import {
  computeClassOf,
  countNodesByComputeClass,
  COMPUTE_CLASSES,
  COMPUTE_CLASS_LABELS,
  COMPUTE_CLASS_SHORT,
} from './computeClass';
import type { TopologyNode } from './index';

function node(partial: Partial<TopologyNode> & { id: string }): TopologyNode {
  return {
    typeId: 'service',
    label: partial.id,
    parentId: null,
    status: 'healthy',
    ...partial,
  } as TopologyNode;
}

describe('computeClassOf', () => {
  it('places anything found inside a cluster in the k8s estate', () => {
    expect(computeClassOf(node({ id: 'a', cluster: 'ymir' }))).toBe('k8s');
  });

  it('treats a node with no cluster as your own hardware', () => {
    // This is the bare-metal and workstation case: a resident that reported
    // itself through the push inbox has no cluster to belong to.
    expect(computeClassOf(node({ id: 'b', typeId: 'ravn_long' }))).toBe('own');
  });

  it('reads a model by where it is served from, not by where it was found', () => {
    const vendorCall = node({
      id: 'claude',
      typeId: 'model',
      cluster: 'ymir',
      location: 'external',
    } as Partial<TopologyNode> & { id: string });
    expect(computeClassOf(vendorCall)).toBe('outside');
  });

  it('counts a self-hosted model as your own silicon even outside a cluster', () => {
    const local = node({
      id: 'nemotron',
      typeId: 'model',
      location: 'internal',
    } as Partial<TopologyNode> & { id: string });
    expect(computeClassOf(local)).toBe('own');
  });

  it('falls back to placement when a model has no known provenance', () => {
    // `unknown` must not be asserted as a cost — Bifröst returns empty base
    // URLs for every provider, so this is the common case, not the edge one.
    const unknown = node({
      id: 'mystery',
      typeId: 'model',
      cluster: 'ymir',
      location: 'unknown',
    } as Partial<TopologyNode> & { id: string });
    expect(computeClassOf(unknown)).toBe('k8s');
  });
});

describe('computeClassMap', () => {
  it('places a cluster in the k8s estate — it does not sit inside one', () => {
    const counts = countNodesByComputeClass([
      node({ id: 'c', typeId: 'cluster' }),
      node({ id: 'ns', typeId: 'namespace' }),
    ]);
    expect(counts.k8s).toBe(2);
    expect(counts.own).toBe(0);
  });

  it('inherits placement from an ancestor when the node omits it', () => {
    const counts = countNodesByComputeClass([
      node({ id: 'c', typeId: 'cluster', label: 'ymir' }),
      node({ id: 'r', typeId: 'ravn_long', parentId: 'c' }),
    ]);
    expect(counts.k8s).toBe(2);
  });
});

describe('countNodesByComputeClass', () => {
  it('reports a zero for a class with nothing in it', () => {
    const counts = countNodesByComputeClass([node({ id: 'a', cluster: 'ymir' })]);
    expect(counts).toEqual({ k8s: 1, own: 0, outside: 0 });
  });

  it('covers every declared class', () => {
    const counts = countNodesByComputeClass([]);
    for (const cls of COMPUTE_CLASSES) {
      expect(counts[cls]).toBe(0);
      expect(COMPUTE_CLASS_LABELS[cls]).toBeTruthy();
      expect(COMPUTE_CLASS_SHORT[cls]).toBeTruthy();
    }
  });
});
