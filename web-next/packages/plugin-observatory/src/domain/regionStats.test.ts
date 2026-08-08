import { describe, expect, it } from 'vitest';
import type { Topology, TopologyEdge, TopologyNode } from './index';
import { descendantsOf, formatCount, regionReadout } from './regionStats';

function node(
  id: string,
  typeId: string,
  parentId: string | null = null,
  status: TopologyNode['status'] = 'healthy',
): TopologyNode {
  return { id, typeId, label: id, parentId, status };
}

function edge(
  id: string,
  sourceId: string,
  targetId: string,
  extra: Partial<TopologyEdge> = {},
): TopologyEdge {
  return { id, sourceId, targetId, kind: 'solid', ...extra };
}

const TOPOLOGY: Topology = {
  timestamp: '2026-08-08T00:00:00Z',
  nodes: [
    node('realm-a', 'realm'),
    node('cluster-a', 'cluster', 'realm-a'),
    node('host-a', 'host', 'cluster-a'),
    node('ravn-a', 'ravn_long', 'host-a'),
    node('ravn-b', 'ravn_long', 'host-a', 'degraded'),
    node('model-a', 'model', 'host-a'),
    node('realm-b', 'realm'),
    node('cluster-b', 'cluster', 'realm-b'),
    node('ravn-c', 'ravn_long', 'cluster-b'),
  ],
  edges: [
    edge('inside-a', 'ravn-a', 'ravn-b', { relationType: 'signals_to', ratePerMinute: 30 }),
    edge('inside-b', 'ravn-c', 'cluster-b', { relationType: 'signals_to', ratePerMinute: 10 }),
    // Restated at another hop, and so not counted twice.
    edge('restated', 'ravn-a', 'model-a', { relationType: 'uses', ratePerMinute: 500 }),
    // Measured, but crossing out of the region, so it belongs to neither.
    edge('crossing', 'ravn-a', 'ravn-c', { relationType: 'signals_to', ratePerMinute: 7 }),
  ],
};

describe('descendantsOf', () => {
  it('reaches every depth, not just the children', () => {
    expect(
      descendantsOf(TOPOLOGY, 'realm-a')
        .map((n) => n.id)
        .sort(),
    ).toEqual(['cluster-a', 'host-a', 'model-a', 'ravn-a', 'ravn-b']);
  });

  it('survives a parent cycle rather than recursing for ever', () => {
    const looped: Topology = {
      timestamp: TOPOLOGY.timestamp,
      nodes: [
        { ...node('a', 'cluster'), parentId: 'b' },
        { ...node('b', 'cluster'), parentId: 'a' },
      ],
      edges: [],
    };
    expect(() => descendantsOf(looped, 'a')).not.toThrow();
  });
});

describe('regionReadout', () => {
  it('reports nothing for a topology, or a region, it does not have', () => {
    expect(regionReadout(null, 'realm-a')).toBeNull();
    expect(regionReadout(TOPOLOGY, 'nowhere')).toBeNull();
  });

  it('counts what is actually inside', () => {
    const readout = regionReadout(TOPOLOGY, 'realm-a')!;
    expect(readout.rows).toEqual([
      { label: 'CLUSTERS', value: '1' },
      { label: 'HOSTS', value: '1' },
      { label: 'RESIDENTS', value: '2' },
      { label: 'MODELS', value: '1' },
      { label: 'MSGS/MIN', value: '30' },
    ]);
  });

  it('shows no row it has no figure for', () => {
    // A panel of dashes teaches an operator to stop reading the panel.
    const labels = regionReadout(TOPOLOGY, 'cluster-b')!.rows.map((row) => row.label);
    expect(labels).not.toContain('HOSTS');
    expect(labels).toContain('RESIDENTS');
  });

  it('never spells out what the health gauge already shows', () => {
    // A ring with a gap in it says a region is not well from across the
    // estate; a row of text restating it underneath is noise.
    for (const id of ['realm-a', 'realm-b', 'cluster-a']) {
      const labels = regionReadout(TOPOLOGY, id)!.rows.map((row) => row.label);
      expect(labels).not.toContain('DEGRADED');
    }
  });

  it('gauges health as the share of what is inside that is well', () => {
    // One of five in realm-a is degraded; realm-b is whole.
    expect(regionReadout(TOPOLOGY, 'realm-a')!.health).toBeCloseTo(4 / 5, 9);
    expect(regionReadout(TOPOLOGY, 'realm-b')!.health).toBe(1);
  });

  it('has nothing to gauge for a region holding nothing', () => {
    const bare: Topology = {
      timestamp: TOPOLOGY.timestamp,
      nodes: [node('r', 'realm')],
      edges: [],
    };
    const readout = regionReadout(bare, 'r')!;
    expect(readout.health).toBeNull();
    expect(readout.rows).toEqual([]);
  });

  it('counts a message once, at the hop that delivers it', () => {
    // The 500/min `uses` edge restates a call counted at another hop; counting
    // it would say the estate is many times busier than it is.
    expect(regionReadout(TOPOLOGY, 'realm-a')!.rows).toContainEqual({
      label: 'MSGS/MIN',
      value: '30',
    });
  });

  it('attributes traffic to the region it happened in, not the one it left', () => {
    // realm-a keeps its own 30; the 7/min crossing to realm-b belongs to
    // neither, because it happened between them.
    const a = regionReadout(TOPOLOGY, 'realm-a')!;
    const b = regionReadout(TOPOLOGY, 'realm-b')!;
    expect(a.trafficShare).toBeCloseTo(30 / 47, 9);
    expect(b.trafficShare).toBeCloseTo(10 / 47, 9);
  });

  it('has no traffic share when nothing measures any traffic at all', () => {
    // Distinct from a share of zero, which would claim a measurably idle estate.
    const unmeasured: Topology = {
      timestamp: TOPOLOGY.timestamp,
      nodes: [node('r', 'realm'), node('a', 'ravn_long', 'r')],
      edges: [edge('e', 'a', 'r')],
    };
    expect(regionReadout(unmeasured, 'r')!.trafficShare).toBeNull();
  });
});

describe('formatCount', () => {
  it('keeps small figures exact and abbreviates the rest', () => {
    expect(formatCount(0)).toBe('0');
    expect(formatCount(412)).toBe('412');
    expect(formatCount(1200)).toBe('1.2k');
    expect(formatCount(5000)).toBe('5k');
    expect(formatCount(250_000)).toBe('250k');
  });
});
