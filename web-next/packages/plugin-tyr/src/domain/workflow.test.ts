import { describe, it, expect } from 'vitest';
import {
  workflowSchema,
  validateWorkflow,
  WorkflowValidationError,
  workflowNodeSchema,
  workflowEdgeSchema,
} from './workflow';
import type { Workflow } from './workflow';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const nodeA = {
  id: 'node-a',
  kind: 'stage' as const,
  label: 'Set up CI',
  raidId: '00000000-0000-0000-0000-000000000001',
  position: { x: 0, y: 0 },
};

const nodeB = {
  id: 'node-b',
  kind: 'gate' as const,
  label: 'QA sign-off',
  condition: 'All acceptance tests pass',
  position: { x: 200, y: 0 },
};

const nodeC = {
  id: 'node-c',
  kind: 'cond' as const,
  label: 'All green?',
  predicate: 'ci.exitCode === 0',
  position: { x: 400, y: 0 },
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
    id: '00000000-0000-0000-0000-000000000001',
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
      expect(result.raidId).toBe('00000000-0000-0000-0000-000000000001');
    }
  });

  it('parses a gate node', () => {
    const result = workflowNodeSchema.parse(nodeB);
    expect(result.kind).toBe('gate');
    if (result.kind === 'gate') {
      expect(result.condition).toBe('All acceptance tests pass');
    }
  });

  it('parses a cond node', () => {
    const result = workflowNodeSchema.parse(nodeC);
    expect(result.kind).toBe('cond');
    if (result.kind === 'cond') {
      expect(result.predicate).toBe('ci.exitCode === 0');
    }
  });

  it('rejects unknown node kind', () => {
    expect(() => workflowNodeSchema.parse({ ...nodeA, kind: 'action' })).toThrow();
  });

  it('rejects empty node id', () => {
    expect(() => workflowNodeSchema.parse({ ...nodeA, id: '' })).toThrow();
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
});
