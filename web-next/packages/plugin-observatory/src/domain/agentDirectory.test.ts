import { describe, expect, it } from 'vitest';
import type { AgentDirectoryEntry, Topology } from './index';
import { findAgentTopologyNode } from './agentDirectory';

describe('findAgentTopologyNode', () => {
  it('associates an aggregate directory record with its existing topology node', () => {
    const topology: Topology = {
      nodes: [
        {
          id: 'runtime:noatun:skuld:skuld:session-a',
          typeId: 'skuld',
          label: 'session-a',
          parentId: null,
          status: 'healthy',
        },
      ],
      edges: [],
      timestamp: '2026-07-14T12:00:00Z',
    };
    const entry = {
      topologyNodeId: 'runtime:noatun:skuld:skuld:session-a',
    } as Pick<AgentDirectoryEntry, 'topologyNodeId'>;

    expect(findAgentTopologyNode(entry, topology)).toBe(topology.nodes[0]);
  });

  it('returns undefined when the source topology is not loaded', () => {
    const topology: Topology = { nodes: [], edges: [], timestamp: '' };
    expect(findAgentTopologyNode({ topologyNodeId: 'missing' }, topology)).toBeUndefined();
  });
});
