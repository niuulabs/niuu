import { describe, it, expect } from 'vitest';
import {
  workflowSchema,
  validateWorkflow,
  WorkflowValidationError,
  workflowNodeSchema,
  workflowEdgeSchema,
  includeNodes,
  providedNodeIds,
  resolveWorkflowNode,
} from './workflow';
import type { Workflow } from './workflow';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const nodeA = {
  id: 'node-a',
  kind: 'stage' as const,
  label: 'Set up CI',
  runId: '00000000-0000-4000-8000-000000000001',
  position: { x: 0, y: 0 },
};

const nodeB = {
  id: 'node-b',
  kind: 'gate' as const,
  label: 'QA sign-off',
  condition: 'All acceptance tests pass',
  mode: 'human_approval' as const,
  pendingBehavior: 'help_needed' as const,
  approvalEvent: 'qa.approved',
  changesRequestedEvent: 'qa.changes_requested',
  instructions: 'Review the QA pack before approving.',
  position: { x: 200, y: 0 },
};

const nodeC = {
  id: 'node-c',
  kind: 'cond' as const,
  label: 'All green?',
  predicate: 'ci.exitCode === 0',
  position: { x: 400, y: 0 },
};

const nodeD = {
  id: 'node-d',
  kind: 'wait' as const,
  label: 'Wait for CI',
  position: { x: 600, y: 0 },
};

const nodeInclude = {
  id: 'node-include',
  kind: 'include' as const,
  label: 'Plan the delivery',
  workflow: 'planning',
  nodes: {
    'planning-analysis': 'delivery-plan-author',
    'planning-reviews': 'delivery-plan-reviews',
  },
  overrides: {
    'delivery-plan-author': { label: 'Author the plan', position: { x: 0, y: 0 } },
  },
  position: { x: 800, y: 0 },
};

const edgeAB = {
  id: 'edge-ab',
  source: 'node-a',
  target: 'node-b',
  cp1: { x: 50, y: 0 },
  cp2: { x: 150, y: 0 },
};

const edgeBC = {
  id: 'edge-bc',
  source: 'node-b',
  target: 'node-c',
  label: 'approved',
  cp1: { x: 250, y: 0 },
  cp2: { x: 350, y: 0 },
};

function makeWorkflow(overrides: Partial<Workflow> = {}): Workflow {
  return {
    id: '00000000-0000-4000-8000-000000000101',
    name: 'Auth Rewrite Workflow',
    nodes: [nodeA, nodeB, nodeC],
    edges: [edgeAB, edgeBC],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Node schemas
// ---------------------------------------------------------------------------

describe('workflowNodeSchema', () => {
  it('parses a stage node', () => {
    const result = workflowNodeSchema.parse(nodeA);
    expect(result.kind).toBe('stage');
    if (result.kind === 'stage') {
      expect(result.runId).toBe('00000000-0000-4000-8000-000000000001');
    }
  });

  it('parses a gate node', () => {
    const result = workflowNodeSchema.parse(nodeB);
    expect(result.kind).toBe('gate');
    if (result.kind === 'gate') {
      expect(result.condition).toBe('All acceptance tests pass');
      expect(result.mode).toBe('human_approval');
      expect(result.pendingBehavior).toBe('help_needed');
      expect(result.approvalEvent).toBe('qa.approved');
      expect(result.changesRequestedEvent).toBe('qa.changes_requested');
      expect(result.instructions).toContain('Review the QA pack');
    }
  });

  it('parses a cond node', () => {
    const result = workflowNodeSchema.parse(nodeC);
    expect(result.kind).toBe('cond');
    if (result.kind === 'cond') {
      expect(result.predicate).toBe('ci.exitCode === 0');
    }
  });

  it('parses a passive wait node without persona or gate fields', () => {
    const result = workflowNodeSchema.parse(nodeD);
    expect(result).toEqual(nodeD);
    expect(result.kind).toBe('wait');
  });

  it('rejects persona execution fields on a passive wait node', () => {
    expect(() => workflowNodeSchema.parse({ ...nodeD, personaIds: ['reviewer'] })).toThrow();
    expect(() => workflowNodeSchema.parse({ ...nodeD, stageMembers: [] })).toThrow();
  });

  it('parses an include node with overrides', () => {
    const result = workflowNodeSchema.parse(nodeInclude);
    expect(result.kind).toBe('include');
    if (result.kind === 'include') {
      expect(result.workflow).toBe('planning');
      expect(result.nodes).toEqual({
        'planning-analysis': 'delivery-plan-author',
        'planning-reviews': 'delivery-plan-reviews',
      });
      expect(result.overrides).toEqual({
        'delivery-plan-author': { label: 'Author the plan', position: { x: 0, y: 0 } },
      });
    }
  });

  it('rejects an include node with an empty nodes map', () => {
    expect(() => workflowNodeSchema.parse({ ...nodeInclude, nodes: {} })).toThrow();
  });

  it('rejects unknown node kind', () => {
    expect(() => workflowNodeSchema.parse({ ...nodeA, kind: 'action' })).toThrow();
  });

  it('rejects empty node id', () => {
    expect(() => workflowNodeSchema.parse({ ...nodeA, id: '' })).toThrow();
  });
});

describe('workflowSchema wait compatibility', () => {
  it('requires schema version 2 for passive wait nodes', () => {
    expect(() => workflowSchema.parse(makeWorkflow({ nodes: [nodeD] }))).toThrow(
      'Passive wait nodes require workflow schema version 2',
    );
    expect(workflowSchema.parse(makeWorkflow({ schemaVersion: 2, nodes: [nodeD] })).nodes).toEqual([
      nodeD,
    ]);
  });
});

// ---------------------------------------------------------------------------
// Edge schema
// ---------------------------------------------------------------------------

describe('workflowEdgeSchema', () => {
  it('parses a valid edge', () => {
    const result = workflowEdgeSchema.parse(edgeAB);
    expect(result.source).toBe('node-a');
    expect(result.target).toBe('node-b');
  });

  it('allows optional label', () => {
    const result = workflowEdgeSchema.parse({ ...edgeAB, label: 'yes' });
    expect(result.label).toBe('yes');
  });

  it('allows absent label', () => {
    const result = workflowEdgeSchema.parse(edgeAB);
    expect(result.label).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Workflow schema
// ---------------------------------------------------------------------------

describe('workflowSchema', () => {
  it('parses a valid workflow', () => {
    const result = workflowSchema.parse(makeWorkflow());
    expect(result.nodes).toHaveLength(3);
    expect(result.edges).toHaveLength(2);
  });

  it('accepts a workflow with no edges', () => {
    const result = workflowSchema.parse(makeWorkflow({ edges: [] }));
    expect(result.edges).toHaveLength(0);
  });

  it('rejects invalid UUID', () => {
    expect(() => workflowSchema.parse(makeWorkflow({ id: 'bad-id' }))).toThrow();
  });
});

// ---------------------------------------------------------------------------
// Include helpers
// ---------------------------------------------------------------------------

describe('includeNodes / providedNodeIds / resolveWorkflowNode', () => {
  it('returns only include-kind nodes', () => {
    const workflow = makeWorkflow({ nodes: [nodeA, nodeInclude] });
    expect(includeNodes(workflow)).toEqual([nodeInclude]);
  });

  it('returns an empty list when there are no includes', () => {
    expect(includeNodes(makeWorkflow())).toEqual([]);
  });

  it('maps every provided local id to its owning include node', () => {
    const workflow = makeWorkflow({ nodes: [nodeA, nodeInclude] });
    const provided = providedNodeIds(workflow);
    expect(provided.get('delivery-plan-author')).toBe(nodeInclude);
    expect(provided.get('delivery-plan-reviews')).toBe(nodeInclude);
    expect(provided.has('node-a')).toBe(false);
  });

  it('resolves a real node id directly', () => {
    const workflow = makeWorkflow({ nodes: [nodeA, nodeInclude] });
    expect(resolveWorkflowNode(workflow, 'node-a')).toBe(nodeA);
  });

  it('resolves a provided local id to its owning include node', () => {
    const workflow = makeWorkflow({ nodes: [nodeA, nodeInclude] });
    expect(resolveWorkflowNode(workflow, 'delivery-plan-reviews')).toBe(nodeInclude);
  });

  it('returns undefined for an id that is neither a node nor provided', () => {
    const workflow = makeWorkflow({ nodes: [nodeA, nodeInclude] });
    expect(resolveWorkflowNode(workflow, 'unknown-id')).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// validateWorkflow — DAG invariants
// ---------------------------------------------------------------------------

describe('validateWorkflow', () => {
  it('passes for a valid workflow', () => {
    expect(() => validateWorkflow(makeWorkflow())).not.toThrow();
  });

  it('throws on duplicate node ids', () => {
    const nodes = [nodeA, { ...nodeA, kind: 'gate' as const, condition: 'x', label: 'dup' }];
    expect(() => validateWorkflow(makeWorkflow({ nodes, edges: [] }))).toThrow(
      WorkflowValidationError,
    );
  });

  it('throws when edge source references unknown node', () => {
    const badEdge = { ...edgeAB, source: 'node-unknown' };
    expect(() => validateWorkflow(makeWorkflow({ edges: [badEdge] }))).toThrow(
      WorkflowValidationError,
    );
  });

  it('throws when edge target references unknown node', () => {
    const badEdge = { ...edgeAB, target: 'node-unknown' };
    expect(() => validateWorkflow(makeWorkflow({ edges: [badEdge] }))).toThrow(
      WorkflowValidationError,
    );
  });

  it('throws on self-loop', () => {
    const selfLoop = { ...edgeAB, target: 'node-a' };
    expect(() => validateWorkflow(makeWorkflow({ edges: [selfLoop] }))).toThrow(
      WorkflowValidationError,
    );
  });

  it('throws on duplicate edges', () => {
    expect(() => validateWorkflow(makeWorkflow({ edges: [edgeAB, edgeAB] }))).toThrow(
      WorkflowValidationError,
    );
  });

  it('passes for empty workflow (no nodes, no edges)', () => {
    expect(() => validateWorkflow(makeWorkflow({ nodes: [], edges: [] }))).not.toThrow();
  });

  it('accepts an edge whose source or target is a local id an include provides', () => {
    const toProvided = { ...edgeAB, source: 'node-a', target: 'delivery-plan-author' };
    const fromProvided = {
      id: 'edge-provided',
      source: 'delivery-plan-reviews',
      target: 'node-a',
      cp1: { x: 0, y: 0 },
      cp2: { x: 0, y: 0 },
    };
    expect(() =>
      validateWorkflow(
        makeWorkflow({ nodes: [nodeA, nodeInclude], edges: [toProvided, fromProvided] }),
      ),
    ).not.toThrow();
  });

  it('still throws when an edge references an id no include provides', () => {
    const badEdge = { ...edgeAB, source: 'node-a', target: 'not-provided-anywhere' };
    expect(() =>
      validateWorkflow(makeWorkflow({ nodes: [nodeA, nodeInclude], edges: [badEdge] })),
    ).toThrow(WorkflowValidationError);
  });

  it('error message names the problematic id', () => {
    const selfLoop = { ...edgeAB, target: 'node-a' };
    let message = '';
    try {
      validateWorkflow(makeWorkflow({ edges: [selfLoop] }));
    } catch (e) {
      if (e instanceof WorkflowValidationError) message = e.message;
    }
    expect(message).toContain('node-a');
  });

  it('validates resource binding targets and uniqueness', () => {
    const resource = {
      id: 'resource-a',
      kind: 'resource' as const,
      label: 'Knowledge mount',
      position: { x: 0, y: 100 },
    };
    const binding = {
      id: 'binding-a',
      resourceNodeId: resource.id,
      targetType: 'workflow' as const,
      targetId: makeWorkflow().id,
      access: 'write' as const,
    };
    const nodes = [nodeA, resource];
    expect(() =>
      validateWorkflow(
        makeWorkflow({
          nodes,
          edges: [],
          resourceBindings: [
            binding,
            { ...binding, id: 'binding-stage', targetType: 'stage', targetId: nodeA.id },
            { ...binding, id: 'binding-persona', targetType: 'persona', targetId: 'reviewer' },
          ],
        }),
      ),
    ).not.toThrow();

    expect(() =>
      validateWorkflow(
        makeWorkflow({ nodes, edges: [], resourceBindings: [{ ...binding, resourceNodeId: 'x' }] }),
      ),
    ).toThrow(/unknown resource node/i);
    expect(() =>
      validateWorkflow(
        makeWorkflow({ nodes, edges: [], resourceBindings: [{ ...binding, targetId: 'x' }] }),
      ),
    ).toThrow(/unknown workflow target/i);
    expect(() =>
      validateWorkflow(
        makeWorkflow({
          nodes,
          edges: [],
          resourceBindings: [{ ...binding, targetType: 'stage', targetId: 'x' }],
        }),
      ),
    ).toThrow(/unknown stage target/i);
    expect(() =>
      validateWorkflow(
        makeWorkflow({
          nodes,
          edges: [],
          resourceBindings: [binding, { ...binding, id: 'binding-duplicate' }],
        }),
      ),
    ).toThrow(/duplicate resource binding/i);
  });
});
