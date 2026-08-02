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
      { id: 'forge', label: 'forge', memberIds: ['huginn', 'muninn'] },
      { id: 'ops', label: 'ops', memberIds: ['kvasir', 'forseti'] },
    ]);
  });

  it('names a mesh after the flock node that declares it', () => {
    const flock: TopologyNode = {
      id: 'flock:flock-k8s',
      typeId: 'flock',
      label: 'K8S flock',
      parentId: null,
      status: 'healthy',
      flockId: 'flock-k8s',
    };
    const meshes = deriveAgentMeshes(
      topology([
        flock,
        node('bryn', 'valkyrie', 'flock-k8s'),
        node('eir', 'valkyrie', 'flock-k8s'),
      ]),
    );

    expect(meshes.map((m) => m.label)).toEqual(['K8S flock']);
  });

  it('names a workflow mesh after the session its members run in', () => {
    // The flock id of a workflow is the session UUID — an honest identifier
    // and a useless name. The session node is named after the work.
    const session: TopologyNode = {
      id: 'session-1',
      typeId: 'skuld',
      label: 'research-campaign-investigate-the-gateway',
      parentId: 'namespace-skuld',
      status: 'healthy',
    };
    const member = (id: string): TopologyNode => ({
      ...node(id, 'ravn_run', '42fdc3ee-5722-4fa3-9a6b-81560e8a48b5'),
      parentId: 'session-1',
    });
    const meshes = deriveAgentMeshes(topology([session, member('framer'), member('skeptic')]));

    expect(meshes.map((m) => m.label)).toEqual(['research-campaign-investigate-the-gateway']);
  });

  it('falls back to the flock id when members are scattered', () => {
    const here: TopologyNode = { ...node('a', 'ravn_long', 'roaming'), parentId: 'cluster-a' };
    const there: TopologyNode = { ...node('b', 'ravn_long', 'roaming'), parentId: 'cluster-b' };

    expect(deriveAgentMeshes(topology([here, there])).map((m) => m.label)).toEqual(['roaming']);
  });

  it('falls back to the flock id when the shared parent is not in the graph', () => {
    const orphan = (id: string): TopologyNode => ({
      ...node(id, 'ravn_run', 'session-gone'),
      parentId: 'session-gone',
    });

    expect(deriveAgentMeshes(topology([orphan('a'), orphan('b')])).map((m) => m.label)).toEqual([
      'session-gone',
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
    expect(meshes).toEqual([{ id: 'forge', label: 'forge', memberIds: ['a', 'b'] }]);
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
