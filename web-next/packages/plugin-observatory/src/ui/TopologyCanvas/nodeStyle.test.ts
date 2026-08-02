import { describe, it, expect } from 'vitest';
import { buildTypeStyles, nodeStyle, COMPUTE_COLOUR, STATUS_COLOUR } from './nodeStyle';
import { NODE_SIZE } from './config';
import type { Registry, TopologyNode } from '../../domain';

function registry(types: Array<Partial<Registry['types'][number]>>): Registry {
  return {
    version: 11,
    updatedAt: '2026-08-01T00:00:00Z',
    types: types.map((type) => ({
      id: 'service',
      label: 'Service',
      rune: 'ᛦ',
      icon: 'box',
      shape: 'box',
      color: 'ice-300',
      size: 8,
      border: 'solid',
      canContain: [],
      parentTypes: [],
      category: 'topology',
      description: '',
      fields: [],
      ...type,
    })),
  } as Registry;
}

function node(partial: Partial<TopologyNode> & { id: string }): TopologyNode {
  return {
    typeId: 'service',
    label: partial.id,
    parentId: null,
    status: 'healthy',
    ...partial,
  } as TopologyNode;
}

describe('buildTypeStyles', () => {
  it('takes the glyph and size the registry declares', () => {
    const styles = buildTypeStyles(registry([{ id: 'bifrost', shape: 'pentagon', size: 15 }]));
    expect(styles.get('bifrost')).toEqual({ shape: 'pentagon', size: 15 });
  });

  it('substitutes the boxed dot for a glyph the canvas cannot draw', () => {
    // An operator can put anything in this field through the registry API;
    // the canvas has to stay drawable whatever they put there.
    const styles = buildTypeStyles(registry([{ id: 'ting', shape: 'spiral' }]));
    expect(styles.get('ting')?.shape).toBe('box');
  });

  it('falls back to the canvas size table when the registry size is unusable', () => {
    const styles = buildTypeStyles(registry([{ id: 'ting', size: 0 }]));
    expect(styles.get('ting')?.size).toBe(NODE_SIZE['ting']);
  });

  it('produces nothing at all without a registry', () => {
    expect(buildTypeStyles(null).size).toBe(0);
  });
});

describe('nodeStyle', () => {
  const styles = buildTypeStyles(
    registry([
      { id: 'ravn_long', shape: 'agent', size: 11 },
      { id: 'model', shape: 'hex-flat', size: 13 },
    ]),
  );

  it('colours by whose silicon the node runs on', () => {
    expect(
      nodeStyle(node({ id: 'a', typeId: 'ravn_long', cluster: 'ymir' }), styles).colour,
    ).toEqual(COMPUTE_COLOUR.k8s);
    expect(nodeStyle(node({ id: 'b', typeId: 'ravn_long' }), styles).colour).toEqual(
      COMPUTE_COLOUR.own,
    );
  });

  it('lets a fault outrank placement', () => {
    const failing = node({ id: 'c', typeId: 'ravn_long', cluster: 'ymir', status: 'failed' });
    expect(nodeStyle(failing, styles).colour).toEqual(STATUS_COLOUR['failed']);
  });

  it('reports the glyph reach so edges can stop at the outline', () => {
    const resident = nodeStyle(node({ id: 'd', typeId: 'ravn_long' }), styles);
    expect(resident.radius).toBeGreaterThan(resident.size);
  });

  it('draws an unregistered type as a boxed dot at a sensible size', () => {
    const style = nodeStyle(node({ id: 'e', typeId: 'brand-new' }), styles);
    expect(style.shape).toBe('box');
    expect(style.size).toBeGreaterThan(0);
  });
});
