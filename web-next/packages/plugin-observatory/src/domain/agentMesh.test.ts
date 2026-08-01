import { describe, it, expect } from 'vitest';
import { deriveAgentMeshes, findMeshForNode, isMeshMember } from './agentMesh';
import type { Topology, TopologyNode } from './index';

function node(id: string, typeId: string, flockId?: string): TopologyNode {
  return { id, typeId, label: id, parentId: null, status: 'healthy', flockId };
}

function topology(nodes: TopologyNode[]): Topology {
  return { nodes, edges: [], timestamp: '2026-08-01T00:00:00Z' };
}

describe('isMeshMember', () => {
  it('accepts agent types carrying a flock id', () => {
    expect(isMeshMember(node('a', 'ravn_long', 'forge'))).toBe(true);
    expect(isMeshMember(node('b', 'valkyrie', 'forge'))).toBe(true);
    expect(isMeshMember(node('c', 'ravn_run', 'run-1'))).toBe(true);
    expect(isMeshMember(node('d', 'run', 'run-1'))).toBe(true);
  });

  it('rejects an agent with no flock id', () => {
    expect(isMeshMember(node('a', 'ravn_long'))).toBe(false);
    expect(isMeshMember(node('a', 'ravn_long', ''))).toBe(false);
  });

  it('rejects infrastructure even when it carries a flock id', () => {
    expect(isMeshMember(node('h', 'host', 'forge'))).toBe(false);
    expect(isMeshMember(node('m', 'mimir', 'forge'))).toBe(false);
  });
});

describe('deriveAgentMeshes', () => {
  it('groups agents that share a flock id', () => {
    const meshes = deriveAgentMeshes(
      topology([
        node('huginn', 'ravn_long', 'forge'),
        node('muninn', 'ravn_long', 'forge'),
        node('kvasir', 'ravn_long', 'ops'),
        node('forseti', 'ravn_long', 'ops'),
      ]),
    );
    expect(meshes).toEqual([
      { id: 'forge', memberIds: ['huginn', 'muninn'] },
      { id: 'ops', memberIds: ['kvasir', 'forseti'] },
    ]);
  });

  it('drops a lone agent — one member is not a collaboration', () => {
    const meshes = deriveAgentMeshes(
      topology([
        node('solo', 'ravn_long', 'alone'),
        node('a', 'ravn_long', 'pair'),
        node('b', 'ravn_long', 'pair'),
      ]),
    );
    expect(meshes.map((m) => m.id)).toEqual(['pair']);
  });

  it('ignores nodes that cannot be members', () => {
    const meshes = deriveAgentMeshes(
      topology([
        node('a', 'ravn_long', 'forge'),
        node('b', 'ravn_long', 'forge'),
        node('h', 'host', 'forge'),
        node('s', 'service'),
      ]),
    );
    expect(meshes).toEqual([{ id: 'forge', memberIds: ['a', 'b'] }]);
  });

  it('preserves topology order so the hull does not shuffle between frames', () => {
    const first = deriveAgentMeshes(
      topology([
        node('z', 'ravn_long', 'm'),
        node('a', 'ravn_long', 'm'),
        node('k', 'ravn_long', 'm'),
      ]),
    );
    expect(first[0]?.memberIds).toEqual(['z', 'a', 'k']);
  });

  it('returns nothing for an absent or empty topology', () => {
    expect(deriveAgentMeshes(null)).toEqual([]);
    expect(deriveAgentMeshes(topology([]))).toEqual([]);
  });
});

describe('findMeshForNode', () => {
  const meshes = [
    { id: 'forge', memberIds: ['a', 'b'] },
    { id: 'ops', memberIds: ['c', 'd'] },
  ];

  it('finds the mesh containing a node', () => {
    expect(findMeshForNode(meshes, 'c')?.id).toBe('ops');
  });

  it('returns null for a non-member or no selection', () => {
    expect(findMeshForNode(meshes, 'zzz')).toBeNull();
    expect(findMeshForNode(meshes, null)).toBeNull();
  });
});
