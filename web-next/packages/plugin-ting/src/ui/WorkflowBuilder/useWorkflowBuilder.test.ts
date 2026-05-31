import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { hasMatchingEdge, useWorkflowBuilder } from './useWorkflowBuilder';
import type { Workflow } from '../../domain/workflow';
import type { PersonaEntry } from './LibraryPanel';
import type { WorkflowStageModelOption } from './useWorkflowBuilder';

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

const WORKFLOW_MODELS: WorkflowStageModelOption[] = [
  { id: 'gpt-5.5', label: 'GPT 5.5', vendor: 'openai' },
  { id: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6', vendor: 'anthropic' },
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

  it('fills stage member defaults from personaIds and model options on load', () => {
    const wf: Workflow = {
      ...makeWorkflow(),
      nodes: [
        {
          id: 'stage-1',
          kind: 'stage',
          label: 'Stage 1',
          runId: null,
          personaIds: ['coder'],
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
    const { result } = renderHook(() => useWorkflowBuilder(wf, PERSONAS, WORKFLOW_MODELS));
    const node = result.current.workflow.nodes.find((candidate) => candidate.id === 'stage-1');
    expect(node?.kind).toBe('stage');
    if (node?.kind === 'stage') {
      expect(node.stageMembers).toEqual([
        {
          personaId: 'coder',
          model: 'gpt-5.5',
          budget: 40,
          consumesEventTypes: [],
          eventFilters: {},
        },
      ]);
      expect(node.executionMode).toBe('parallel');
      expect(node.maxConcurrent).toBe(3);
      expect(node.joinMode).toBe('all');
    }
  });
});

describe('useWorkflowBuilder — helper coverage', () => {
  it('matches edges by source, target, and normalized label text', () => {
    expect(
      hasMatchingEdge(
        [
          {
            id: 'edge-1',
            source: 'trigger-1',
            target: 'stage-1',
            cp1: { x: 80, y: 0 },
            cp2: { x: -80, y: 0 },
          },
          {
            id: 'edge-2',
            source: 'trigger-1',
            target: 'stage-1',
            label: 'code.requested -> code.requested',
            cp1: { x: 80, y: 0 },
            cp2: { x: -80, y: 0 },
          },
        ],
        'trigger-1',
        'stage-1',
        'code.requested',
      ),
    ).toBe(true);

    expect(
      hasMatchingEdge(
        [
          {
            id: 'edge-3',
            source: 'trigger-1',
            target: 'stage-1',
            cp1: { x: 80, y: 0 },
            cp2: { x: -80, y: 0 },
          },
        ],
        'trigger-1',
        'stage-1',
        'review.completed',
      ),
    ).toBe(false);
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

describe('useWorkflowBuilder — inspectNode', () => {
  it('opens and closes the inspector', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.inspectNode('stage-1'));
    expect(result.current.inspectorNodeId).toBe('stage-1');
    act(() => result.current.inspectNode(null));
    expect(result.current.inspectorNodeId).toBeNull();
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

  it('adds an end node', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.addNode('end'));
    expect(result.current.workflow.nodes[2]!.kind).toBe('end');
  });

  it('adds a resource node with registry defaults', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.addNode('resource'));
    const added = result.current.workflow.nodes[2]!;
    expect(added.kind).toBe('resource');
    if (added.kind === 'resource') {
      expect(added.bindingMode).toBe('registry');
      expect(added.registryEntryId).toBeNull();
      expect(added.defaultReadPriority).toBe(10);
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

  it('clears connect state and inspector when deleting the active node', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.inspectNode('stage-1'));
    act(() => result.current.startConnect('stage-1', 'qa.report'));
    act(() => result.current.deleteNode('stage-1'));
    expect(result.current.connectingFromId).toBeNull();
    expect(result.current.inspectorNodeId).toBeNull();
  });

  it('removes resource bindings that reference the deleted node', () => {
    const wf: Workflow = {
      ...makeWorkflow(),
      nodes: [
        ...makeWorkflow().nodes,
        {
          id: 'resource-1',
          kind: 'resource',
          label: 'Docs',
          resourceType: 'mimir',
          bindingMode: 'registry',
          registryEntryId: 'docs',
          seedFromRegistryId: null,
          categories: [],
          path: '/docs',
          url: null,
          role: null,
          authRef: null,
          defaultReadPriority: 10,
          position: { x: 500, y: 100 },
        },
      ],
      resourceBindings: [
        {
          id: 'binding-resource',
          resourceNodeId: 'resource-1',
          targetType: 'workflow',
          targetId: '00000000-0000-0000-0000-000000000001',
          access: 'read',
          writePrefixes: [],
          readPriority: 10,
        },
        {
          id: 'binding-target',
          resourceNodeId: 'stage-1',
          targetType: 'node',
          targetId: 'resource-1',
          access: 'write',
          writePrefixes: ['/tmp'],
          readPriority: 5,
        },
      ],
    };
    const { result } = renderHook(() => useWorkflowBuilder(wf));
    act(() => result.current.deleteNode('resource-1'));
    expect(result.current.workflow.resourceBindings).toEqual([]);
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

describe('useWorkflowBuilder — deleteEdge', () => {
  it('removes only the requested edge', () => {
    const wf: Workflow = {
      ...makeWorkflow(),
      edges: [
        ...makeWorkflow().edges,
        {
          id: 'e2',
          source: 'gate-1',
          target: 'stage-1',
          label: 'review.completed -> review.completed',
          cp1: { x: 80, y: 0 },
          cp2: { x: -80, y: 0 },
        },
      ],
    };
    const { result } = renderHook(() => useWorkflowBuilder(wf));
    act(() => result.current.deleteEdge('e1'));
    expect(result.current.workflow.edges.map((edge) => edge.id)).toEqual(['e2']);
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

  it('ignores startConnect calls without a label', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.startConnect('stage-1'));
    expect(result.current.connectingFromId).toBeNull();
    expect(result.current.selectedNodeId).toBeNull();
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

  it('completeConnect does nothing when not currently connecting', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    const before = result.current.workflow.edges;
    act(() => result.current.completeConnect('gate-1', 'review.verdict'));
    expect(result.current.workflow.edges).toEqual(before);
  });

  it('completeConnect ignores missing target nodes', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    const before = result.current.workflow.edges;
    act(() => result.current.startConnect('stage-1', 'qa.report'));
    act(() => result.current.completeConnect('missing-node', 'review.verdict'));
    expect(result.current.workflow.edges).toEqual(before);
  });

  it('completeConnect derives the default condition input for condition nodes', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.addNode('cond', { x: 520, y: 100 }));
    const condNodeId = result.current.workflow.nodes[2]!.id;
    act(() => result.current.startConnect('stage-1', 'qa.report'));
    act(() => result.current.completeConnect(condNodeId));
    expect(
      result.current.workflow.edges.find(
        (edge) => edge.source === 'stage-1' && edge.target === condNodeId,
      )?.label,
    ).toBe('qa.report -> condition.input');
  });

  it('completeConnect does not connect to targets without a default input label', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() => result.current.addNode('resource', { x: 520, y: 100 }));
    const resourceNodeId = result.current.workflow.nodes[2]!.id;
    const before = result.current.workflow.edges.length;
    act(() => result.current.startConnect('stage-1', 'qa.report'));
    act(() => result.current.completeConnect(resourceNodeId));
    expect(result.current.workflow.edges).toHaveLength(before);
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

  it('connecting a trigger rewrites malformed edge labels when the dispatch event changes', () => {
    const triggerWorkflow: Workflow = {
      id: '00000000-0000-0000-0000-000000000006',
      name: 'Trigger Rewrite',
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
          id: 'gate-1',
          kind: 'gate',
          label: 'Gate',
          condition: '',
          mode: 'human_approval',
          pendingBehavior: 'help_needed',
          approvalEvent: '',
          changesRequestedEvent: '',
          instructions: '',
          autoForwardAfter: '30m',
          position: { x: 220, y: 20 },
        },
      ],
      edges: [
        {
          id: 'edge-1',
          source: 'trigger-1',
          target: 'gate-1',
          label: 'bad label',
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

  it('addStageWithPersona is a no-op for auto-wiring when no personas are provided', () => {
    const existing: Workflow = {
      id: '00000000-0000-0000-0000-000000000007',
      name: 'No Personas',
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
    const { result } = renderHook(() => useWorkflowBuilder(existing, []));
    act(() => result.current.addStageWithPersona('coder', undefined, { x: 220, y: 20 }));
    expect(result.current.workflow.nodes).toHaveLength(2);
    expect(result.current.workflow.edges).toEqual([]);
  });

  it('addStageWithPersona wires backward into an existing stage that consumes its output', () => {
    const existing: Workflow = {
      id: '00000000-0000-0000-0000-000000000008',
      name: 'Backward Wire',
      nodes: [
        {
          id: 'stage-review',
          kind: 'stage',
          label: 'Review',
          runId: null,
          personaIds: ['reviewer'],
          stageMembers: [{ personaId: 'reviewer', model: '', budget: 40 }],
          executionMode: 'parallel',
          maxConcurrent: 3,
          joinMode: 'all',
          position: { x: 320, y: 100 },
        },
      ],
      edges: [],
    };
    const { result } = renderHook(() => useWorkflowBuilder(existing, PERSONAS));
    act(() => result.current.addStageWithPersona('coder', undefined, { x: 100, y: 100 }));
    expect(result.current.workflow.edges).toHaveLength(1);
    expect(result.current.workflow.edges[0]?.label).toBe('code.changed -> code.changed');
    expect(result.current.workflow.edges[0]?.source).toBe(result.current.workflow.nodes[1]?.id);
    expect(result.current.workflow.edges[0]?.target).toBe('stage-review');
  });

  it('addStageWithPersona prefers forward wiring when stages share events in both directions', () => {
    const cyclePersonas: PersonaEntry[] = [
      {
        id: 'producer-a',
        label: 'Producer A',
        role: 'a',
        consumes: ['event.b'],
        produces: ['event.a'],
      },
      {
        id: 'producer-b',
        label: 'Producer B',
        role: 'b',
        consumes: ['event.a'],
        produces: ['event.b'],
      },
    ];
    const existing: Workflow = {
      id: '00000000-0000-0000-0000-000000000009',
      name: 'Bidirectional Wire',
      nodes: [
        {
          id: 'stage-a',
          kind: 'stage',
          label: 'A',
          runId: null,
          personaIds: ['producer-a'],
          stageMembers: [{ personaId: 'producer-a', model: '', budget: 40 }],
          executionMode: 'parallel',
          maxConcurrent: 3,
          joinMode: 'all',
          position: { x: 100, y: 100 },
        },
      ],
      edges: [],
    };
    const { result } = renderHook(() => useWorkflowBuilder(existing, cyclePersonas));
    act(() => result.current.addStageWithPersona('producer-b', undefined, { x: 320, y: 100 }));
    expect(result.current.workflow.edges).toHaveLength(1);
    expect(result.current.workflow.edges[0]?.source).toBe('stage-a');
    expect(result.current.workflow.edges[0]?.label).toBe('event.a -> event.a');
  });

  it('addStageWithPersona wires backward when bidirectional stages are placed to the left', () => {
    const cyclePersonas: PersonaEntry[] = [
      {
        id: 'producer-a',
        label: 'Producer A',
        role: 'a',
        consumes: ['event.b'],
        produces: ['event.a'],
      },
      {
        id: 'producer-b',
        label: 'Producer B',
        role: 'b',
        consumes: ['event.a'],
        produces: ['event.b'],
      },
    ];
    const existing: Workflow = {
      id: '00000000-0000-0000-0000-000000000009b',
      name: 'Bidirectional Reverse Wire',
      nodes: [
        {
          id: 'stage-a',
          kind: 'stage',
          label: 'A',
          runId: null,
          personaIds: ['producer-a'],
          stageMembers: [{ personaId: 'producer-a', model: '', budget: 40 }],
          executionMode: 'parallel',
          maxConcurrent: 3,
          joinMode: 'all',
          position: { x: 320, y: 100 },
        },
      ],
      edges: [],
    };
    const { result } = renderHook(() => useWorkflowBuilder(existing, cyclePersonas));
    act(() => result.current.addStageWithPersona('producer-b', undefined, { x: 100, y: 100 }));
    expect(result.current.workflow.edges).toHaveLength(1);
    expect(result.current.workflow.edges[0]?.source).toBe(result.current.workflow.nodes[1]?.id);
    expect(result.current.workflow.edges[0]?.target).toBe('stage-a');
    expect(result.current.workflow.edges[0]?.label).toBe('event.b -> event.b');
  });

  it('addStageWithPersona leaves stages disconnected when their events do not overlap', () => {
    const existing: Workflow = {
      id: '00000000-0000-0000-0000-000000000010',
      name: 'No Shared Events',
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
    act(() => result.current.addStageWithPersona('coder', undefined, { x: 320, y: 100 }));
    expect(result.current.workflow.edges).toEqual([]);
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

describe('useWorkflowBuilder — stage member updates', () => {
  it('adds personas using the workflow default model when no model is provided', () => {
    const { result } = renderHook(() =>
      useWorkflowBuilder(makeWorkflow(), PERSONAS, WORKFLOW_MODELS),
    );
    act(() => result.current.addPersonaToStage('stage-1', 'coder'));
    const node = result.current.workflow.nodes.find((candidate) => candidate.id === 'stage-1');
    expect(node?.kind).toBe('stage');
    if (node?.kind === 'stage') {
      expect(node.stageMembers?.[0]).toMatchObject({
        personaId: 'coder',
        model: 'gpt-5.5',
        budget: 40,
      });
    }
  });

  it('replaces personas while preserving the previous model by default', () => {
    const existing: Workflow = {
      ...makeWorkflow(),
      nodes: [
        {
          id: 'stage-1',
          kind: 'stage',
          label: 'Stage 1',
          runId: null,
          personaIds: ['coder'],
          stageMembers: [{ personaId: 'coder', model: 'claude-sonnet-4-6', budget: 50 }],
          executionMode: 'parallel',
          maxConcurrent: 3,
          joinMode: 'all',
          position: { x: 100, y: 100 },
        },
        makeWorkflow().nodes[1]!,
      ],
    };
    const { result } = renderHook(() => useWorkflowBuilder(existing, PERSONAS, WORKFLOW_MODELS));
    act(() => result.current.replacePersonaInStage('stage-1', 'coder', 'reviewer'));
    const node = result.current.workflow.nodes.find((candidate) => candidate.id === 'stage-1');
    expect(node?.kind).toBe('stage');
    if (node?.kind === 'stage') {
      expect(node.stageMembers).toEqual([
        {
          personaId: 'reviewer',
          model: 'claude-sonnet-4-6',
          budget: 50,
          consumesEventTypes: [],
          eventFilters: {},
        },
      ]);
      expect(node.personaIds).toEqual(['reviewer']);
    }
  });

  it('replaces personas with an explicit model override', () => {
    const existing: Workflow = {
      ...makeWorkflow(),
      nodes: [
        {
          id: 'stage-1',
          kind: 'stage',
          label: 'Stage 1',
          runId: null,
          personaIds: ['coder'],
          stageMembers: [{ personaId: 'coder', model: '', budget: 40 }],
          executionMode: 'parallel',
          maxConcurrent: 3,
          joinMode: 'all',
          position: { x: 100, y: 100 },
        },
        makeWorkflow().nodes[1]!,
      ],
    };
    const { result } = renderHook(() => useWorkflowBuilder(existing, PERSONAS, WORKFLOW_MODELS));
    act(() =>
      result.current.replacePersonaInStage('stage-1', 'coder', 'reviewer', 'claude-sonnet-4-6'),
    );
    const node = result.current.workflow.nodes.find((candidate) => candidate.id === 'stage-1');
    expect(node?.kind).toBe('stage');
    if (node?.kind === 'stage') {
      expect(node.stageMembers?.[0]?.model).toBe('claude-sonnet-4-6');
    }
  });

  it('updates persona models and budgets', () => {
    const existing: Workflow = {
      ...makeWorkflow(),
      nodes: [
        {
          id: 'stage-1',
          kind: 'stage',
          label: 'Stage 1',
          runId: null,
          personaIds: ['coder'],
          stageMembers: [{ personaId: 'coder', model: 'gpt-5.5', budget: 40 }],
          executionMode: 'parallel',
          maxConcurrent: 3,
          joinMode: 'all',
          position: { x: 100, y: 100 },
        },
        makeWorkflow().nodes[1]!,
      ],
    };
    const { result } = renderHook(() => useWorkflowBuilder(existing, PERSONAS, WORKFLOW_MODELS));
    act(() => result.current.updatePersonaModel('stage-1', 'coder', 'claude-sonnet-4-6'));
    act(() => result.current.updatePersonaBudget('stage-1', 'coder', 75));
    const node = result.current.workflow.nodes.find((candidate) => candidate.id === 'stage-1');
    expect(node?.kind).toBe('stage');
    if (node?.kind === 'stage') {
      expect(node.stageMembers?.[0]).toMatchObject({
        model: 'claude-sonnet-4-6',
        budget: 75,
      });
    }
  });

  it('setWorkflow normalizes stage defaults with the active model list', () => {
    const { result } = renderHook(() =>
      useWorkflowBuilder(makeWorkflow(), PERSONAS, WORKFLOW_MODELS),
    );
    act(() =>
      result.current.setWorkflow({
        id: '00000000-0000-0000-0000-000000000099',
        name: 'Replacement',
        nodes: [
          {
            id: 'stage-9',
            kind: 'stage',
            label: 'Replacement Stage',
            runId: null,
            personaIds: ['reviewer'],
            position: { x: 10, y: 20 },
          },
        ],
        edges: [],
      }),
    );
    const node = result.current.workflow.nodes[0];
    expect(result.current.workflow.id).toBe('00000000-0000-0000-0000-000000000099');
    expect(node?.kind).toBe('stage');
    if (node?.kind === 'stage') {
      expect(node.stageMembers?.[0]?.model).toBe('gpt-5.5');
      expect(node.personaIds).toEqual(['reviewer']);
    }
  });

  it('updateNode normalizes patched stage members', () => {
    const { result } = renderHook(() =>
      useWorkflowBuilder(makeWorkflow(), PERSONAS, WORKFLOW_MODELS),
    );
    act(() =>
      result.current.updateNode('stage-1', {
        stageMembers: [{ personaId: 'coder', model: undefined, budget: undefined }] as never,
      }),
    );
    const node = result.current.workflow.nodes.find((candidate) => candidate.id === 'stage-1');
    expect(node?.kind).toBe('stage');
    if (node?.kind === 'stage') {
      expect(node.stageMembers).toEqual([
        {
          personaId: 'coder',
          model: 'gpt-5.5',
          budget: 40,
          consumesEventTypes: [],
          eventFilters: {},
        },
      ]);
    }
  });

  it('updateNode does not rewrite trigger edges for empty dispatch events', () => {
    const triggerWorkflow: Workflow = {
      id: '00000000-0000-0000-0000-000000000011',
      name: 'Trigger Empty Patch',
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
    act(() => result.current.updateNode('trigger-1', { dispatchEvent: '' }));
    expect(result.current.workflow.edges[0]?.label).toBe('code.requested -> code.requested');
  });
});

describe('useWorkflowBuilder — resource bindings and workflow metadata', () => {
  it('adds resource bindings with defaults and overrides', () => {
    const { result } = renderHook(() => useWorkflowBuilder(makeWorkflow()));
    act(() =>
      result.current.addResourceBinding('resource-1', {
        targetType: 'node',
        targetId: 'stage-1',
        access: 'write',
        writePrefixes: ['/workspace'],
        readPriority: 2,
      }),
    );
    expect(result.current.workflow.resourceBindings).toHaveLength(1);
    expect(result.current.workflow.resourceBindings?.[0]).toMatchObject({
      resourceNodeId: 'resource-1',
      targetType: 'node',
      targetId: 'stage-1',
      access: 'write',
      writePrefixes: ['/workspace'],
      readPriority: 2,
    });
  });

  it('updates resource bindings while preserving their ids', () => {
    const wf: Workflow = {
      ...makeWorkflow(),
      resourceBindings: [
        {
          id: 'binding-1',
          resourceNodeId: 'resource-1',
          targetType: 'workflow',
          targetId: '00000000-0000-0000-0000-000000000001',
          access: 'read',
          writePrefixes: [],
          readPriority: 10,
        },
      ],
    };
    const { result } = renderHook(() => useWorkflowBuilder(wf));
    act(() =>
      result.current.updateResourceBinding('binding-1', {
        id: 'binding-overwrite',
        access: 'write',
        readPriority: 3,
      } as never),
    );
    expect(result.current.workflow.resourceBindings?.[0]).toMatchObject({
      id: 'binding-1',
      access: 'write',
      readPriority: 3,
    });
  });

  it('removes resource bindings and leaves unrelated ones intact', () => {
    const wf: Workflow = {
      ...makeWorkflow(),
      resourceBindings: [
        {
          id: 'binding-1',
          resourceNodeId: 'resource-1',
          targetType: 'workflow',
          targetId: '00000000-0000-0000-0000-000000000001',
          access: 'read',
          writePrefixes: [],
          readPriority: 10,
        },
        {
          id: 'binding-2',
          resourceNodeId: 'resource-2',
          targetType: 'workflow',
          targetId: '00000000-0000-0000-0000-000000000001',
          access: 'read',
          writePrefixes: [],
          readPriority: 5,
        },
      ],
    };
    const { result } = renderHook(() => useWorkflowBuilder(wf));
    act(() => result.current.removeResourceBinding('binding-1'));
    expect(result.current.workflow.resourceBindings).toEqual([
      expect.objectContaining({ id: 'binding-2' }),
    ]);
  });

  it('updates workflow metadata', () => {
    const { result } = renderHook(() =>
      useWorkflowBuilder(makeWorkflow(), PERSONAS, WORKFLOW_MODELS),
    );
    act(() =>
      result.current.updateWorkflowMeta({
        name: 'Updated workflow',
        description: 'Runs the council review path',
        version: '2',
        tags: ['review', 'automation'],
      }),
    );
    expect(result.current.workflow).toMatchObject({
      name: 'Updated workflow',
      description: 'Runs the council review path',
      version: '2',
      tags: ['review', 'automation'],
    });
  });
});
