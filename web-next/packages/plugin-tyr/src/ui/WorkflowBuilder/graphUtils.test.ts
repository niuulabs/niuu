import { describe, it, expect } from 'vitest';
import {
  makeNodeId,
  makeEdgeId,
  defaultBezierCPs,
  nodeCentre,
  edgeToPath,
  workflowToYaml,
  STAGE_WIDTH,
  STAGE_HEIGHT,
  GATE_SIZE,
  COND_RADIUS,
} from './graphUtils';
import type { WorkflowNode, WorkflowEdge } from '../../domain/workflow';

// ---------------------------------------------------------------------------
// ID generation
// ---------------------------------------------------------------------------

describe('makeNodeId / makeEdgeId', () => {
  it('generates non-empty strings', () => {
    expect(makeNodeId().length).toBeGreaterThan(0);
    expect(makeEdgeId().length).toBeGreaterThan(0);
  });

  it('generates unique IDs on consecutive calls', () => {
    const ids = new Set(Array.from({ length: 50 }, () => makeNodeId()));
    expect(ids.size).toBe(50);
  });

  it('node id starts with "node-"', () => {
    expect(makeNodeId().startsWith('node-')).toBe(true);
  });

  it('edge id starts with "edge-"', () => {
    expect(makeEdgeId().startsWith('edge-')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// defaultBezierCPs
// ---------------------------------------------------------------------------

describe('defaultBezierCPs', () => {
  it('produces horizontal CPs for horizontal connection', () => {
    const { cp1, cp2 } = defaultBezierCPs({ x: 0, y: 100 }, { x: 300, y: 100 });
    expect(cp1.x).toBeGreaterThan(0);
    expect(cp2.x).toBeLessThan(0);
    expect(cp1.y).toBe(0);
    expect(cp2.y).toBe(0);
  });

  it('produces vertical CPs for vertical connection', () => {
    const { cp1, cp2 } = defaultBezierCPs({ x: 100, y: 0 }, { x: 100, y: 300 });
    expect(cp1.y).toBeGreaterThan(0);
    expect(cp2.y).toBeLessThan(0);
    expect(cp1.x).toBe(0);
    expect(cp2.x).toBe(0);
  });

  it('uses horizontal CPs for equal dx/dy (tiebreak)', () => {
    const { cp1, cp2 } = defaultBezierCPs({ x: 0, y: 0 }, { x: 100, y: 100 });
    // abs(dx) === abs(dy) → isMoreHorizontal = true
    expect(cp1.y).toBe(0);
    expect(cp2.y).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// nodeCentre
// ---------------------------------------------------------------------------

describe('nodeCentre', () => {
  it('returns centre of a stage node', () => {
    const node: WorkflowNode = {
      id: 'n1',
      kind: 'stage',
      label: 'Test',
      raidId: null,
      personaIds: [],
      position: { x: 0, y: 0 },
    };
    const c = nodeCentre(node);
    expect(c.x).toBe(STAGE_WIDTH / 2);
    expect(c.y).toBe(STAGE_HEIGHT / 2);
  });

  it('returns centre of a gate node', () => {
    const node: WorkflowNode = {
      id: 'n2',
      kind: 'gate',
      label: 'Gate',
      condition: 'ok',
      position: { x: 10, y: 20 },
    };
    const c = nodeCentre(node);
    expect(c.x).toBe(10 + GATE_SIZE / 2);
    expect(c.y).toBe(20 + GATE_SIZE / 2);
  });

  it('returns centre of a cond node', () => {
    const node: WorkflowNode = {
      id: 'n3',
      kind: 'cond',
      label: 'Cond',
      predicate: 'x > 0',
      position: { x: 50, y: 50 },
    };
    const c = nodeCentre(node);
    expect(c.x).toBe(50 + COND_RADIUS);
    expect(c.y).toBe(50 + COND_RADIUS);
  });
});

// ---------------------------------------------------------------------------
// edgeToPath
// ---------------------------------------------------------------------------

describe('edgeToPath', () => {
  const stageA: WorkflowNode = {
    id: 'a',
    kind: 'stage',
    label: 'A',
    raidId: null,
    personaIds: [],
    position: { x: 0, y: 0 },
  };
  const stageB: WorkflowNode = {
    id: 'b',
    kind: 'stage',
    label: 'B',
    raidId: null,
    personaIds: [],
    position: { x: 200, y: 0 },
  };

  const edge: WorkflowEdge = {
    id: 'e1',
    source: 'a',
    target: 'b',
    cp1: { x: 80, y: 0 },
    cp2: { x: -80, y: 0 },
  };

  it('returns an SVG path string for valid source/target', () => {
    const nodes = new Map([
      ['a', stageA],
      ['b', stageB],
    ]);
    const path = edgeToPath(edge, nodes);
    expect(path).not.toBeNull();
    expect(path).toMatch(/^M /);
    expect(path).toContain('C ');
  });

  it('returns null when source node is missing', () => {
    const nodes = new Map([['b', stageB]]);
    expect(edgeToPath(edge, nodes)).toBeNull();
  });

  it('returns null when target node is missing', () => {
    const nodes = new Map([['a', stageA]]);
    expect(edgeToPath(edge, nodes)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// workflowToYaml
// ---------------------------------------------------------------------------

describe('workflowToYaml', () => {
  const workflow = {
    id: '00000000-0000-0000-0000-000000000001',
    name: 'Test Workflow',
    nodes: [
      {
        id: 'stage-1',
        kind: 'stage' as const,
        label: 'Set up CI',
        raidId: 'raid-123',
        personaIds: ['persona-build'],
        position: { x: 100, y: 100 },
      },
      {
        id: 'gate-1',
        kind: 'gate' as const,
        label: 'QA sign-off',
        condition: 'all tests pass',
        position: { x: 300, y: 100 },
      },
      {
        id: 'cond-1',
        kind: 'cond' as const,
        label: 'All green?',
        predicate: 'ci.exitCode === 0',
        position: { x: 500, y: 100 },
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

  it('includes the workflow id and name', () => {
    const yaml = workflowToYaml(workflow);
    expect(yaml).toContain(workflow.id);
    expect(yaml).toContain(workflow.name);
  });

  it('includes node IDs', () => {
    const yaml = workflowToYaml(workflow);
    expect(yaml).toContain('stage-1');
    expect(yaml).toContain('gate-1');
    expect(yaml).toContain('cond-1');
  });

  it('includes node kinds', () => {
    const yaml = workflowToYaml(workflow);
    expect(yaml).toContain('kind: stage');
    expect(yaml).toContain('kind: gate');
    expect(yaml).toContain('kind: cond');
  });

  it('includes edge source and target', () => {
    const yaml = workflowToYaml(workflow);
    expect(yaml).toContain('source:');
    expect(yaml).toContain('target:');
  });

  it('outputs "nodes: []" for empty node list', () => {
    const yaml = workflowToYaml({ ...workflow, nodes: [], edges: [] });
    expect(yaml).toContain('nodes: []');
    expect(yaml).toContain('edges: []');
  });

  it('includes personaIds for stage nodes', () => {
    const yaml = workflowToYaml(workflow);
    expect(yaml).toContain('personaIds:');
    expect(yaml).toContain('persona-build');
  });

  it('includes gate condition', () => {
    const yaml = workflowToYaml(workflow);
    expect(yaml).toContain('condition:');
    expect(yaml).toContain('all tests pass');
  });

  it('includes cond predicate', () => {
    const yaml = workflowToYaml(workflow);
    expect(yaml).toContain('predicate:');
    expect(yaml).toContain('ci.exitCode === 0');
  });

  it('outputs "null" for null raidId', () => {
    const wf = {
      ...workflow,
      nodes: [{ ...workflow.nodes[0]!, raidId: null, personaIds: [] }],
      edges: [],
    };
    const yaml = workflowToYaml(wf);
    expect(yaml).toContain('raidId: null');
  });
});
