import { describe, it, expect } from 'vitest';
import {
  clusterSummaries,
  meshSubtitle,
  mimirBadge,
  placementSubtitle,
  residentSubtitle,
} from './railSummaries';
import type { Topology, TopologyNode } from './index';

function node(id: string, typeId: string, over: Record<string, unknown> = {}): TopologyNode {
  return { id, typeId, label: id, parentId: null, status: 'healthy', ...over } as TopologyNode;
}

function topology(nodes: TopologyNode[]): Topology {
  return { timestamp: '2026-08-02T00:00:00Z', nodes, edges: [] };
}

describe('clusterSummaries', () => {
  it('counts the hosts, GPUs and pods discovered inside a cluster', () => {
    const summaries = clusterSummaries(
      topology([
        node('realm-asgard', 'realm', { label: 'asgard' }),
        node('c1', 'cluster', { label: 'valaskjalf', parentId: 'realm-asgard' }),
        node('h1', 'host', { parentId: 'c1', gpuCount: 2, pods: 120 }),
        node('h2', 'host', { parentId: 'c1', gpuCount: 2, pods: 105 }),
      ]),
    );

    expect(summaries).toHaveLength(1);
    expect(summaries[0]).toMatchObject({ realm: 'asgard', hosts: 2, gpus: 4, pods: 225 });
  });

  it('takes the realm from the parent when the node does not name one', () => {
    const summaries = clusterSummaries(
      topology([
        node('realm-midgard', 'realm', { label: 'midgard' }),
        node('c1', 'cluster', { label: 'jarnvidr', parentId: 'realm-midgard' }),
      ]),
    );
    expect(summaries[0]?.realm).toBe('midgard');
  });

  it('reports null rather than zero for a figure nothing supplied', () => {
    // "No GPU in this cluster" and "nobody counted" are different claims, and
    // the rail badge has to be able to say the second one.
    const summaries = clusterSummaries(
      topology([node('c1', 'cluster', { label: 'eitri' }), node('h1', 'host', { parentId: 'c1' })]),
    );
    expect(summaries[0]).toMatchObject({ hosts: 1, gpus: null, pods: null });
  });

  it('counts a host nested below a namespace towards its cluster', () => {
    const summaries = clusterSummaries(
      topology([
        node('c1', 'cluster', { label: 'ymir' }),
        node('ns', 'namespace', { parentId: 'c1' }),
        node('h1', 'host', { parentId: 'ns', pods: 40 }),
      ]),
    );
    expect(summaries[0]).toMatchObject({ hosts: 1, pods: 40 });
  });

  it('orders clusters by name so the rail does not shuffle', () => {
    const summaries = clusterSummaries(
      topology([
        node('c2', 'cluster', { label: 'ymir' }),
        node('c1', 'cluster', { label: 'eitri' }),
      ]),
    );
    expect(summaries.map((s) => s.node.label)).toEqual(['eitri', 'ymir']);
  });

  it('is empty for an absent topology', () => {
    expect(clusterSummaries(null)).toEqual([]);
  });
});

describe('row subtitles', () => {
  it('names a resident by what it runs on and where', () => {
    expect(residentSubtitle(node('a', 'ravn_long', { engine: 'ravn', cluster: 'ymir' }))).toBe(
      'ravn · ymir',
    );
  });

  it('calls a resident with no cluster local', () => {
    expect(residentSubtitle(node('a', 'ravn_long', { engine: 'openclaw' }))).toBe(
      'openclaw · local',
    );
  });

  it('places a Mímir by cluster and realm', () => {
    expect(placementSubtitle(node('m', 'mimir', { cluster: 'ymir', realm: 'yggdrasil' }))).toBe(
      'ymir · yggdrasil',
    );
  });

  it('says nothing about a node with no placement at all', () => {
    expect(placementSubtitle(node('m', 'mimir'))).toBe('');
  });

  it('says what a mesh does, from what its members declare', () => {
    const topo = topology([
      node('a', 'ravn_long', { specialty: 'build', cluster: 'ymir' }),
      node('b', 'ravn_run', { role: 'verify', cluster: 'ymir' }),
      node('c', 'ravn_run', { role: 'ship', cluster: 'ymir' }),
    ]);
    expect(meshSubtitle(['a', 'b', 'c'], topo)).toBe('build · verify · ship');
  });

  it('calls a mesh built around a session a workflow', () => {
    const topo = topology([
      node('r', 'run', { cluster: 'valhalla' }),
      node('a', 'ravn_run', { cluster: 'valhalla' }),
    ]);
    expect(meshSubtitle(['r', 'a'], topo)).toBe('workflow · valhalla');
  });

  it('names the host a resident runs on over its cluster', () => {
    // "Which machine" is the more specific answer, and the cluster is already
    // one row down the rail.
    expect(residentSubtitle(node('a', 'ravn_long', { engine: 'ravn', hostId: 'saehrimnir' }))).toBe(
      'ravn · saehrimnir',
    );
  });

  it('lists the clusters a mesh reaches across when members declare nothing', () => {
    const topo = topology([
      node('a', 'ravn_long', { cluster: 'ymir' }),
      node('b', 'ravn_long', { cluster: 'eitri' }),
      node('c', 'ravn_long'),
    ]);
    expect(meshSubtitle(['a', 'b', 'c'], topo)).toBe('eitri · local · ymir');
  });
});

describe('mimirBadge', () => {
  it('carries the page count an instance reports', () => {
    expect(mimirBadge(node('m', 'mimir', { pages: 1203 }))).toBe('1,203p');
  });

  it('carries nothing when no count was reported', () => {
    expect(mimirBadge(node('m', 'mimir'))).toBeNull();
  });
});
