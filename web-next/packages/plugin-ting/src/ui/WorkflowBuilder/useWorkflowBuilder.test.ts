import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useWorkflowBuilder } from './useWorkflowBuilder';
import type { Workflow } from '../../domain/workflow';
import type { PersonaEntry } from './LibraryPanel';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeWorkflow(): Workflow {
  return {
    id: '00000000-0000-0000-0000-000000000001',
    name: 'Test',
    nodes: [
      {
        id: 'stage-1',
        kind: 'stage',
        label: 'Stage 1',
        runId: null,
        personaIds: [],
        position: { x: 100, y: 100 },
      },
      {
        id: 'gate-1',
        kind: 'gate',
        label: 'Gate',
        condition: 'ok',
        position: { x: 300, y: 100 },
      },
    ],
    edges: [
      {
        id: 'e1',
        source: 'stage-1',
        target: 'gate-1',
        cp1: { x: 80, y: 0 },
        cp2: { x: -80, y: 0 },
      },
    ],
  };
}

const PERSONAS: PersonaEntry[] = [
  {
    id: 'coder',
    label: 'coder',
    role: 'build',
    consumes: ['code.requested'],
    produces: ['code.changed'],
  },
  {
    id: 'reviewer',
    label: 'reviewer',
    role: 'review',
    consumes: ['code.changed'],
    produces: ['review.completed'],
  },
];

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

describe('useWorkflowBuilder — initial state', () => {
  it('starts with the provided workflow', () => {
    const wf = makeWorkflow();
    const { result } = renderHook(() => useWorkflowBuilder(wf));
    expect(result.current.workflow).toMatchObject(wf);
  });

  it('starts on graph view', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    expect(result.current.view).toBe('graph');
  });

  it('has no selected node', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    expect(result.current.selectedNodeId).toBeNull();
  });

  it('has no connectingFromId', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    expect(result.current.connectingFromId).toBeNull();
  });

  it('has no inspectorNodeId', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    expect(result.current.inspectorNodeId).toBeNull();
  });

  it('normalizes personaIds from stageMembers on load', () => {
    const wf: Workflow = {
      ...makeWorkflow(),
      nodes: [
        {
          id: 'stage-1',
          kind: 'stage',
          label: 'Stage 1',
          runId: null,
          personaIds: [],
          stageMembers: [{ personaId: 'coder', model: '', budget: 40 }],
          position: { x: 100, y: 100 },
        },
        {
          id: 'gate-1',
          kind: 'gate',
          label: 'Gate',
          condition: 'ok',
          position: { x: 300, y: 100 },
        },
      ],
    };
    const { result } = renderHook(() => useWorkflowBuilder(wf));
    const node = result.current.workflow.nodes.find((candidate) => candidate.id === 'stage-1');
    expect(node?.kind).toBe('stage');
    if (node?.kind === 'stage') {
      expect(node.personaIds).toEqual(['coder']);
    }
  });
});

// ---------------------------------------------------------------------------
// setView
// ---------------------------------------------------------------------------

describe('useWorkflowBuilder — setView', () => {
  it('changes view to pipeline', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.setView('pipeline'));
    expect(result.current.view).toBe('pipeline');
  });

  it('changes view to yaml', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.setView('yaml'));
    expect(result.current.view).toBe('yaml');
  });
});

// ---------------------------------------------------------------------------
// selectNode
// ---------------------------------------------------------------------------

describe('useWorkflowBuilder — selectNode', () => {
  it('sets selectedNodeId', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.selectNode('stage-1'));
    expect(result.current.selectedNodeId).toBe('stage-1');
  });

  it('clears connectingFromId when selecting a node', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.startConnect('stage-1'));
    act(() => result.current.selectNode('gate-1'));
    expect(result.current.connectingFromId).toBeNull();
  });

  it('clears selection with null', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.selectNode('stage-1'));
    act(() => result.current.selectNode(null));
    expect(result.current.selectedNodeId).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// addNode
// ---------------------------------------------------------------------------

describe('useWorkflowBuilder — addNode', () => {
  it('adds a stage node', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.addNode('stage'));
    expect(result.current.workflow.nodes).toHaveLength(3);
    expect(result.current.workflow.nodes[2]!.kind).toBe('stage');
  });

  it('adds a gate node', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.addNode('gate'));
    expect(result.current.workflow.nodes[2]!.kind).toBe('gate');
    const added = result.current.workflow.nodes[2]!;
    if (added.kind === 'gate') {
      expect(added.mode).toBe('human_approval');
      expect(added.pendingBehavior).toBe('help_needed');
      expect(added.approvalEvent).toBe('');
      expect(added.changesRequestedEvent).toBe('');
    }
  });

  it('adds a cond node', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.addNode('cond'));
    expect(result.current.workflow.nodes[2]!.kind).toBe('cond');
  });

  it('adds node at specified position', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.addNode('stage', { x: 500, y: 200 }));
    const added = result.current.workflow.nodes[2]!;
    expect(added.position.x).toBe(500);
    expect(added.position.y).toBe(200);
  });

  it('new stage has empty personaIds', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.addNode('stage'));
    const added = result.current.workflow.nodes[2]!;
    if (added.kind === 'stage') {
      expect(added.personaIds).toEqual([]);
    }
  });

  it('new trigger has a dispatch event', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.addNode('trigger'));
    const added = result.current.workflow.nodes[2]!;
    if (added.kind === 'trigger') {
      expect(added.dispatchEvent).toBe('code.requested');
    }
  });

  it('adds a Mimir resource node with a default workflow binding', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() =>
      result.current.addMimirResource(
        {
          id: 'shared-mimir',
          name: 'Shared Mimir',
          kind: 'remote',
          lifecycle: 'registered',
          role: 'shared',
          url: 'https://mimir.example',
          path: '/shared',
          categories: ['decision', 'entity'],
          authRef: 'mimir-secret',
          defaultReadPriority: 3,
          enabled: true,
          healthStatus: 'healthy',
          healthMessage: 'ok',
          desc: 'Shared team mount',
        },
        { x: 420, y: 240 },
      ),
    );
    const resourceNode = result.current.workflow.nodes.find((node) => node.kind === 'resource');
    expect(resourceNode).toBeDefined();
    expect(resourceNode?.label).toBe('Shared Mimir');
    expect(resourceNode?.position).toEqual({ x: 420, y: 240 });
    expect(result.current.workflow.resourceBindings).toHaveLength(1);
    expect(result.current.workflow.resourceBindings?.[0]?.targetType).toBe('workflow');
  });

  it('adds an explicit ephemeral local Mimir resource node', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() =>
      result.current.addMimirResource(
        {
          id: '__ephemeral_local__',
          name: 'Ephemeral Local Mimir',
          kind: 'local',
          lifecycle: 'ephemeral',
          role: 'local',
          url: '',
          path: '',
          categories: ['scratch'],
          authRef: null,
          defaultReadPriority: 10,
          enabled: true,
          healthStatus: 'unknown',
          healthMessage: 'runtime',
          desc: 'Workspace-local scratch',
        },
        { x: 300, y: 180 },
      ),
    );
    const resourceNode = result.current.workflow.nodes.find((node) => node.kind === 'resource');
    expect(resourceNode).toBeDefined();
    expect(resourceNode?.bindingMode).toBe('ephemeral_local');
    expect(resourceNode?.registryEntryId).toBeNull();
    expect(resourceNode?.role).toBe('local');
  });
});

// ---------------------------------------------------------------------------
// deleteNode
// ---------------------------------------------------------------------------

describe('useWorkflowBuilder — deleteNode', () => {
  it('removes the node from the workflow', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.deleteNode('stage-1'));
    expect(result.current.workflow.nodes.find((n) => n.id === 'stage-1')).toBeUndefined();
  });

  it('removes edges connected to the deleted node', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.deleteNode('stage-1'));
    expect(
      result.current.workflow.edges.filter((e) => e.source === 'stage-1' || e.target === 'stage-1'),
    ).toHaveLength(0);
  });

  it('clears selectedNodeId when deleting the selected node', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.selectNode('stage-1'));
    act(() => result.current.deleteNode('stage-1'));
    expect(result.current.selectedNodeId).toBeNull();
  });

  it('preserves other nodes', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.deleteNode('stage-1'));
    expect(result.current.workflow.nodes.find((n) => n.id === 'gate-1')).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// moveNode
// ---------------------------------------------------------------------------

describe('useWorkflowBuilder — moveNode', () => {
  it('updates node position', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.moveNode('stage-1', { x: 999, y: 888 }));
    const node = result.current.workflow.nodes.find((n) => n.id === 'stage-1')!;
    expect(node.position.x).toBe(999);
    expect(node.position.y).toBe(888);
  });
});

// ---------------------------------------------------------------------------
// connect
// ---------------------------------------------------------------------------

describe('useWorkflowBuilder — startConnect / cancelConnect / completeConnect', () => {
  it('sets connectingFromId', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.startConnect('stage-1', 'qa.report'));
    expect(result.current.connectingFromId).toBe('stage-1');
  });

  it('cancelConnect clears connectingFromId', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.startConnect('stage-1', 'qa.report'));
    act(() => result.current.cancelConnect());
    expect(result.current.connectingFromId).toBeNull();
  });

  it('completeConnect adds a new edge', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    // add a third node so we can connect to it
    act(() => result.current.addNode('stage', { x: 500, y: 100 }));
    const newNodeId = result.current.workflow.nodes[2]!.id;
    act(() => result.current.startConnect('stage-1', 'qa.report'));
    act(() => result.current.completeConnect(newNodeId, 'review.verdict'));
    const newEdge = result.current.workflow.edges.find(
      (e) => e.source === 'stage-1' && e.target === newNodeId,
    );
    expect(newEdge).toBeDefined();
    expect(newEdge?.label).toBe('qa.report -> review.verdict');
    expect(result.current.connectingFromId).toBeNull();
  });

  it('completeConnect can target an end node without an explicit input label', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.addNode('end', { x: 500, y: 100 }));
    const endNodeId = result.current.workflow.nodes[2]!.id;
    act(() => result.current.startConnect('stage-1', 'qa.report'));
    act(() => result.current.completeConnect(endNodeId));
    const newEdge = result.current.workflow.edges.find(
      (e) => e.source === 'stage-1' && e.target === endNodeId,
    );
    expect(newEdge).toBeDefined();
    expect(newEdge?.label).toBe('qa.report -> complete');
  });

  it('completeConnect can target a gate without an explicit input label', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.startConnect('stage-1', 'qa.report'));
    act(() => result.current.completeConnect('gate-1'));
    const newEdge = result.current.workflow.edges.find(
      (e) =>
        e.source === 'stage-1' &&
        e.target === 'gate-1' &&
        e.label === 'qa.report -> approval.requested',
    );
    expect(newEdge).toBeDefined();
  });

  it('completeConnect does not create duplicate edges', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.startConnect('stage-1', 'qa.report'));
    act(() => result.current.completeConnect('gate-1', 'review.verdict'));
    act(() => result.current.startConnect('stage-1', 'qa.report'));
    act(() => result.current.completeConnect('gate-1', 'review.verdict'));
    const edges = result.current.workflow.edges.filter(
      (e) => e.source === 'stage-1' && e.target === 'gate-1',
    );
    expect(edges).toHaveLength(2);
    expect(edges.filter((e) => e.label === 'qa.report -> review.verdict')).toHaveLength(1);
  });

  it('completeConnect to self does nothing', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    const edgesBefore = result.current.workflow.edges.length;
    act(() => result.current.startConnect('stage-1', 'qa.report'));
    act(() => result.current.completeConnect('stage-1', 'review.verdict'));
    expect(result.current.workflow.edges).toHaveLength(edgesBefore);
  });

  it('connecting a trigger to a stage aligns the trigger event to the target input', () => {
    const triggerWorkflow: Workflow = {
      id: '00000000-0000-0000-0000-000000000002',
      name: 'Trigger Test',
      nodes: [
        {
          id: 'trigger-1',
          kind: 'trigger',
          label: 'Start',
          source: 'manual dispatch',
          dispatchEvent: 'code.requested',
          position: { x: 20, y: 20 },
        },
        {
          id: 'stage-1',
          kind: 'stage',
          label: 'Review',
          runId: null,
          personaIds: ['reviewer'],
          stageMembers: [{ personaId: 'reviewer', model: '', budget: 40 }],
          executionMode: 'parallel',
          maxConcurrent: 3,
          joinMode: 'all',
          position: { x: 220, y: 20 },
        },
      ],
      edges: [],
    };
    const { result } = renderHook(() => useWorkflowBuilder(triggerWorkflow, PERSONAS));
    act(() => result.current.startConnect('trigger-1', 'code.requested'));
    act(() => result.current.completeConnect('stage-1', 'code.changed'));
    const trigger = result.current.workflow.nodes.find((node) => node.id === 'trigger-1');
    expect(trigger?.kind).toBe('trigger');
    if (trigger?.kind === 'trigger') {
      expect(trigger.dispatchEvent).toBe('code.changed');
    }
    expect(result.current.workflow.edges[0]?.label).toBe('code.changed -> code.changed');
  });
});

// ---------------------------------------------------------------------------
// persona management
// ---------------------------------------------------------------------------

describe('useWorkflowBuilder — persona management', () => {
  it('addPersonaToStage adds a persona ID to a stage', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.addPersonaToStage('stage-1', 'persona-build'));
    const node = result.current.workflow.nodes.find((n) => n.id === 'stage-1')!;
    if (node.kind === 'stage') {
      expect(node.personaIds).toContain('persona-build');
    }
  });

  it('addPersonaToStage does not add duplicate persona', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.addPersonaToStage('stage-1', 'persona-build'));
    act(() => result.current.addPersonaToStage('stage-1', 'persona-build'));
    const node = result.current.workflow.nodes.find((n) => n.id === 'stage-1')!;
    if (node.kind === 'stage') {
      expect(node.personaIds.filter((p) => p === 'persona-build')).toHaveLength(1);
    }
  });

  it('removePersonaFromStage removes a persona ID', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.addPersonaToStage('stage-1', 'persona-build'));
    act(() => result.current.removePersonaFromStage('stage-1', 'persona-build'));
    const node = result.current.workflow.nodes.find((n) => n.id === 'stage-1')!;
    if (node.kind === 'stage') {
      expect(node.personaIds).not.toContain('persona-build');
    }
  });

  it('addPersonaToStage is a no-op for non-stage nodes', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    const before = result.current.workflow.nodes.find((n) => n.id === 'gate-1');
    act(() => result.current.addPersonaToStage('gate-1', 'persona-build'));
    const after = result.current.workflow.nodes.find((n) => n.id === 'gate-1');
    expect(after).toEqual(before);
  });

  it('addStageWithPersona auto-wires from compatible existing stages', () => {
    const existing: Workflow = {
      id: '00000000-0000-0000-0000-000000000003',
      name: 'Auto Wire',
      nodes: [
        {
          id: 'stage-1',
          kind: 'stage',
          label: 'Code',
          runId: null,
          personaIds: ['coder'],
          stageMembers: [{ personaId: 'coder', model: '', budget: 40 }],
          executionMode: 'parallel',
          maxConcurrent: 3,
          joinMode: 'all',
          position: { x: 100, y: 100 },
        },
      ],
      edges: [],
    };
    const { result } = renderHook(() => useWorkflowBuilder(existing, PERSONAS));
    act(() => result.current.addStageWithPersona('reviewer', { x: 320, y: 100 }));
    expect(result.current.workflow.nodes).toHaveLength(2);
    expect(result.current.workflow.edges).toHaveLength(1);
    expect(result.current.workflow.edges[0]?.label).toBe('code.changed -> code.changed');
  });

  it('addStageWithPersona auto-wires from an existing trigger when the persona consumes that event', () => {
    const existing: Workflow = {
      id: '00000000-0000-0000-0000-000000000004',
      name: 'Trigger Wire',
      nodes: [
        {
          id: 'trigger-1',
          kind: 'trigger',
          label: 'Start',
          source: 'manual dispatch',
          dispatchEvent: 'code.requested',
          position: { x: 20, y: 20 },
        },
      ],
      edges: [],
    };
    const { result } = renderHook(() => useWorkflowBuilder(existing, PERSONAS));
    act(() => result.current.addStageWithPersona('coder', { x: 220, y: 20 }));
    expect(result.current.workflow.edges).toHaveLength(1);
    expect(result.current.workflow.edges[0]?.label).toBe('code.requested -> code.requested');
  });
});

// ---------------------------------------------------------------------------
// updateNodeLabel
// ---------------------------------------------------------------------------

describe('useWorkflowBuilder — updateNodeLabel', () => {
  it('updates label for a node', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.updateNodeLabel('stage-1', 'Updated label'));
    const node = result.current.workflow.nodes.find((n) => n.id === 'stage-1')!;
    expect(node.label).toBe('Updated label');
  });

  it('updates outgoing trigger edges when the dispatch event changes', () => {
    const triggerWorkflow: Workflow = {
      id: '00000000-0000-0000-0000-000000000005',
      name: 'Trigger Update',
      nodes: [
        {
          id: 'trigger-1',
          kind: 'trigger',
          label: 'Start',
          source: 'manual dispatch',
          dispatchEvent: 'code.requested',
          position: { x: 20, y: 20 },
        },
        {
          id: 'stage-1',
          kind: 'stage',
          label: 'Code',
          runId: null,
          personaIds: ['coder'],
          stageMembers: [{ personaId: 'coder', model: '', budget: 40 }],
          executionMode: 'parallel',
          maxConcurrent: 3,
          joinMode: 'all',
          position: { x: 220, y: 20 },
        },
      ],
      edges: [
        {
          id: 'edge-1',
          source: 'trigger-1',
          target: 'stage-1',
          label: 'code.requested -> code.requested',
          cp1: { x: 80, y: 0 },
          cp2: { x: -80, y: 0 },
        },
      ],
    };
    const { result } = renderHook(() => useWorkflowBuilder(triggerWorkflow));
    act(() => result.current.updateNode('trigger-1', { dispatchEvent: 'review.requested' }));
    expect(result.current.workflow.edges[0]?.label).toBe('review.requested -> review.requested');
  });
});
