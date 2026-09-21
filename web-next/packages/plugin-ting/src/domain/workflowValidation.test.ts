import { describe, it, expect } from 'vitest';
import { validateWorkflowFull } from './workflowValidation';
import type { Workflow, WorkflowNode, WorkflowEdge } from './workflow';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeStage(
  id: string,
  opts: {
    runId?: string | null;
    personaIds?: string[];
    stageMembers?: Array<{ personaId: string; budget?: number }>;
  } = {},
): WorkflowNode {
  return {
    id,
    kind: 'stage',
    label: `Stage ${id}`,
    runId: opts.runId ?? null,
    personaIds: opts.personaIds ?? [],
    stageMembers: opts.stageMembers?.map((member) => ({
      personaId: member.personaId,
      budget: member.budget ?? 40,
    })),
    position: { x: 0, y: 0 },
  };
}

function makeGate(id: string): WorkflowNode {
  return {
    id,
    kind: 'gate',
    label: `Gate ${id}`,
    condition: 'all tests pass',
    position: { x: 0, y: 0 },
  };
}

function makeCond(id: string): WorkflowNode {
  return {
    id,
    kind: 'cond',
    label: `Cond ${id}`,
    predicate: 'ci.exitCode === 0',
    position: { x: 0, y: 0 },
  };
}

function makeWait(id: string): WorkflowNode {
  return {
    id,
    kind: 'wait',
    label: `Wait ${id}`,
    position: { x: 0, y: 0 },
  };
}

function makeSubworkflow(
  id: string,
  templates: Record<string, string> = { default: 'worker' },
): WorkflowNode {
  return {
    id,
    kind: 'subworkflow',
    label: `Children ${id}`,
    position: { x: 0, y: 0 },
    templates,
    allowedCoordinator: 'coordinator',
    inputSchema: { type: 'object' },
    resultSchema: { type: 'object' },
    maxChildren: 10,
    maxAttempts: 3,
    maxActiveChildren: 4,
    joinMode: 'all',
    blockedEvent: 'children.blocked',
  };
}

function makeEdge(id: string, source: string, target: string, label?: string): WorkflowEdge {
  return {
    id,
    source,
    target,
    label,
    cp1: { x: 80, y: 0 },
    cp2: { x: -80, y: 0 },
  };
}

function makeWorkflow(nodes: WorkflowNode[], edges: WorkflowEdge[]): Workflow {
  return {
    id: '00000000-0000-0000-0000-000000000001',
    name: 'Test Workflow',
    nodes,
    edges,
  };
}

// ---------------------------------------------------------------------------
// Baseline — valid workflow
// ---------------------------------------------------------------------------

describe('validateWorkflowFull — valid workflow', () => {
  it('returns no issues for a fully valid linear workflow', () => {
    const nodes = [makeStage('s1', { runId: 'r1', personaIds: ['p1'] }), makeGate('g1')];
    const edges = [makeEdge('e1', 's1', 'g1')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    // gate has incoming (s1→g1); stage has outgoing (s1→g1)
    // stage has runId and personaIds
    expect(issues.filter((i) => i.kind !== 'no_consumer')).toHaveLength(0);
  });

  it('returns no issues for a single isolated node', () => {
    // Single node — orphan, no_producer, no_consumer rules do not apply
    const issues = validateWorkflowFull(makeWorkflow([makeStage('s1')], []));
    // missing_persona will fire; draft templates with no run mapping should not.
    const kinds = issues.map((i) => i.kind);
    expect(kinds).not.toContain('cycle');
    expect(kinds).not.toContain('orphan');
    expect(kinds).not.toContain('confidence_underset');
    expect(kinds).not.toContain('no_producer');
    expect(kinds).not.toContain('no_consumer');
  });
});

// ---------------------------------------------------------------------------
// Rule 1: cycle
// ---------------------------------------------------------------------------

describe('validateWorkflowFull — cycle', () => {
  it('reports cycle on nodes that form a back-edge', () => {
    const nodes = [makeStage('a'), makeStage('b')];
    const edges = [makeEdge('e1', 'a', 'b'), makeEdge('e2', 'b', 'a')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    const cycleIssues = issues.filter((i) => i.kind === 'cycle');
    expect(cycleIssues.length).toBeGreaterThanOrEqual(2);
    const cycleNodeIds = cycleIssues.map((i) => i.nodeId);
    expect(cycleNodeIds).toContain('a');
    expect(cycleNodeIds).toContain('b');
  });

  it('all cycle issues have error severity', () => {
    const nodes = [makeStage('a'), makeStage('b'), makeStage('c')];
    const edges = [makeEdge('e1', 'a', 'b'), makeEdge('e2', 'b', 'c'), makeEdge('e3', 'c', 'a')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    for (const issue of issues.filter((i) => i.kind === 'cycle')) {
      expect(issue.severity).toBe('error');
    }
  });

  it('does not report cycle on acyclic workflow', () => {
    const nodes = [makeStage('a'), makeStage('b'), makeGate('g')];
    const edges = [makeEdge('e1', 'a', 'g'), makeEdge('e2', 'b', 'g')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    expect(issues.filter((i) => i.kind === 'cycle')).toHaveLength(0);
  });

  it('does not report cycle for an expected re-entry loop', () => {
    const nodes = [makeStage('coder'), makeStage('reviewer')];
    const edges = [
      makeEdge('e1', 'coder', 'reviewer', 'code.changed -> code.changed'),
      makeEdge('e2', 'reviewer', 'coder', 'review.changes_requested -> review.changes_requested'),
    ];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    expect(issues.filter((i) => i.kind === 'cycle')).toHaveLength(0);
  });

  it.each([
    ['plan review feedback', 'plan.review.completed -> plan.review.completed'],
    ['child repair', 'developer.workstream.repair_requested -> developer.children.waiting'],
    ['provider wait resume', 'developer.delivery.observed -> developer.delivery.observed'],
  ])('does not report cycle for %s', (_name, feedbackLabel) => {
    const nodes = [makeStage('forward'), makeStage('feedback')];
    const edges = [
      makeEdge('forward', 'forward', 'feedback', 'workflow.progressed -> workflow.progressed'),
      makeEdge('feedback', 'feedback', 'forward', feedbackLabel),
    ];

    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));

    expect(issues.filter((issue) => issue.kind === 'cycle')).toHaveLength(0);
  });

  it('still reports a cycle whose outcome is not a recognized feedback or resume event', () => {
    const nodes = [makeStage('forward'), makeStage('backward')];
    const edges = [
      makeEdge('forward', 'forward', 'backward', 'workflow.progressed -> workflow.progressed'),
      makeEdge('backward', 'backward', 'forward', 'review.completed -> review.completed'),
    ];

    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));

    expect(issues.filter((issue) => issue.kind === 'cycle')).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
// Rule 2: orphan
// ---------------------------------------------------------------------------

describe('validateWorkflowFull — orphan', () => {
  it('reports orphan for a disconnected node in a multi-node workflow', () => {
    const nodes = [makeStage('a'), makeStage('b'), makeStage('c')];
    // a and b connected; c is isolated
    const edges = [makeEdge('e1', 'a', 'b')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    const orphanIssues = issues.filter((i) => i.kind === 'orphan');
    expect(orphanIssues.some((i) => i.nodeId === 'c')).toBe(true);
  });

  it('does not report orphan for a single-node workflow', () => {
    const issues = validateWorkflowFull(makeWorkflow([makeStage('a')], []));
    expect(issues.filter((i) => i.kind === 'orphan')).toHaveLength(0);
  });

  it('orphan issues have warning severity', () => {
    const nodes = [makeStage('a'), makeStage('b'), makeStage('c')];
    const edges = [makeEdge('e1', 'a', 'b')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    for (const issue of issues.filter((i) => i.kind === 'orphan')) {
      expect(issue.severity).toBe('warning');
    }
  });
});

// ---------------------------------------------------------------------------
// Rule 3: dangling_condition
// ---------------------------------------------------------------------------

describe('validateWorkflowFull — dangling_condition', () => {
  it('reports dangling_condition for cond node with 0 outgoing edges', () => {
    const nodes = [makeStage('a'), makeCond('c1')];
    const edges = [makeEdge('e1', 'a', 'c1')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    const dc = issues.filter((i) => i.kind === 'dangling_condition');
    expect(dc.some((i) => i.nodeId === 'c1')).toBe(true);
  });

  it('reports dangling_condition for cond node with only 1 outgoing edge', () => {
    const nodes = [makeStage('a'), makeCond('c1'), makeStage('b')];
    const edges = [makeEdge('e1', 'a', 'c1'), makeEdge('e2', 'c1', 'b')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    expect(
      issues.filter((i) => i.kind === 'dangling_condition').some((i) => i.nodeId === 'c1'),
    ).toBe(true);
  });

  it('does NOT report dangling_condition for cond node with 2 outgoing edges', () => {
    const nodes = [makeStage('a'), makeCond('c1'), makeStage('yes'), makeStage('no')];
    const edges = [
      makeEdge('e1', 'a', 'c1'),
      makeEdge('e2', 'c1', 'yes', 'yes'),
      makeEdge('e3', 'c1', 'no', 'no'),
    ];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    expect(issues.filter((i) => i.kind === 'dangling_condition')).toHaveLength(0);
  });

  it('dangling_condition issues have error severity', () => {
    const nodes = [makeCond('c1')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    for (const issue of issues.filter((i) => i.kind === 'dangling_condition')) {
      expect(issue.severity).toBe('error');
    }
  });
});

// ---------------------------------------------------------------------------
// Rule 4: confidence_underset
// ---------------------------------------------------------------------------

describe('validateWorkflowFull — confidence_underset', () => {
  it('does NOT report confidence_underset when no stages are run-mapped', () => {
    const nodes = [makeStage('s1', { runId: null }), makeStage('s2', { runId: null })];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    expect(issues.filter((i) => i.kind === 'confidence_underset')).toHaveLength(0);
  });

  it('does NOT report confidence_underset for a fully mapped workflow', () => {
    const nodes = [makeStage('s1', { runId: 'run-123' }), makeStage('s2', { runId: 'run-456' })];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    expect(issues.filter((i) => i.kind === 'confidence_underset')).toHaveLength(0);
  });

  it('reports confidence_underset for unmapped stages when another stage is mapped', () => {
    const nodes = [makeStage('s1', { runId: 'run-123' }), makeStage('s2', { runId: null })];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    expect(issues.some((i) => i.kind === 'confidence_underset' && i.nodeId === 's2')).toBe(true);
  });

  it('confidence_underset has warning severity', () => {
    const nodes = [makeStage('s1', { runId: 'run-123' }), makeStage('s2', { runId: null })];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    for (const issue of issues.filter((i) => i.kind === 'confidence_underset')) {
      expect(issue.severity).toBe('warning');
    }
  });

  it('does not fire for gate or cond nodes', () => {
    const nodes = [makeGate('g1'), makeCond('c1')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    expect(issues.filter((i) => i.kind === 'confidence_underset')).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Rule 5: missing_persona
// ---------------------------------------------------------------------------

describe('validateWorkflowFull — missing_persona', () => {
  it('reports missing_persona for stage with empty personaIds', () => {
    const nodes = [makeStage('s1', { personaIds: [] })];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    expect(issues.some((i) => i.kind === 'missing_persona' && i.nodeId === 's1')).toBe(true);
  });

  it('does NOT report missing_persona when personaIds is populated', () => {
    const nodes = [makeStage('s1', { personaIds: ['persona-1'] })];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    expect(issues.filter((i) => i.kind === 'missing_persona')).toHaveLength(0);
  });

  it('does NOT report missing_persona when stageMembers is populated', () => {
    const nodes = [makeStage('s1', { stageMembers: [{ personaId: 'coder' }] })];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    expect(issues.filter((i) => i.kind === 'missing_persona')).toHaveLength(0);
  });

  it('missing_persona has warning severity', () => {
    const nodes = [makeStage('s1')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    for (const issue of issues.filter((i) => i.kind === 'missing_persona')) {
      expect(issue.severity).toBe('warning');
    }
  });

  it('does not fire for gate or cond nodes', () => {
    const nodes = [makeGate('g1'), makeCond('c1')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    expect(issues.filter((i) => i.kind === 'missing_persona')).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Rule 6: no_producer
// ---------------------------------------------------------------------------

describe('validateWorkflowFull — no_producer', () => {
  it('reports no_producer for a passive wait with no incoming edge', () => {
    const nodes = [makeStage('s1'), makeWait('w1')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    expect(issues).toContainEqual(
      expect.objectContaining({ kind: 'no_producer', nodeId: 'w1', severity: 'error' }),
    );
  });

  it('reports no_producer for gate node with no incoming edges', () => {
    const nodes = [makeStage('s1'), makeGate('g1')];
    const edges: WorkflowEdge[] = []; // gate has no input
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    expect(issues.some((i) => i.kind === 'no_producer' && i.nodeId === 'g1')).toBe(true);
  });

  it('reports no_producer for cond node with no incoming edges', () => {
    const nodes = [makeStage('s1'), makeCond('c1')];
    const edges: WorkflowEdge[] = [];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    expect(issues.some((i) => i.kind === 'no_producer' && i.nodeId === 'c1')).toBe(true);
  });

  it('does NOT report no_producer for gate with incoming edge', () => {
    const nodes = [makeStage('s1'), makeGate('g1')];
    const edges = [makeEdge('e1', 's1', 'g1')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    expect(issues.filter((i) => i.kind === 'no_producer')).toHaveLength(0);
  });

  it('does not report no_producer for a singleton workflow', () => {
    const nodes = [makeGate('g1')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    expect(issues.filter((i) => i.kind === 'no_producer')).toHaveLength(0);
  });

  it('no_producer has error severity', () => {
    const nodes = [makeStage('s1'), makeGate('g1')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    for (const issue of issues.filter((i) => i.kind === 'no_producer')) {
      expect(issue.severity).toBe('error');
    }
  });
});

// ---------------------------------------------------------------------------
// Rule 7: no_consumer
// ---------------------------------------------------------------------------

describe('validateWorkflowFull — no_consumer', () => {
  it('reports an error for a passive wait with no outgoing edge', () => {
    const nodes = [makeStage('s1'), makeWait('w1')];
    const edges = [makeEdge('e1', 's1', 'w1', 'custom.build.waiting -> custom.build.waiting')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    expect(issues).toContainEqual(
      expect.objectContaining({ kind: 'no_consumer', nodeId: 'w1', severity: 'error' }),
    );
  });

  it('accepts a passive wait with incoming and outgoing event edges', () => {
    const nodes = [makeStage('s1'), makeWait('w1'), makeGate('g1')];
    const edges = [
      makeEdge('e1', 's1', 'w1', 'custom.build.waiting -> custom.build.waiting'),
      makeEdge('e2', 'w1', 'g1', 'custom.build.finished -> custom.build.finished'),
    ];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    expect(issues.filter((issue) => issue.nodeId === 'w1')).toHaveLength(0);
  });

  it('reports no_consumer for stage with no outgoing edges in multi-node workflow', () => {
    const nodes = [makeStage('s1'), makeStage('s2')];
    const edges: WorkflowEdge[] = []; // no edges at all
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    const nc = issues.filter((i) => i.kind === 'no_consumer');
    expect(nc.some((i) => i.nodeId === 's1')).toBe(true);
    expect(nc.some((i) => i.nodeId === 's2')).toBe(true);
  });

  it('does NOT report no_consumer for a single-node workflow', () => {
    const nodes = [makeStage('s1')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    expect(issues.filter((i) => i.kind === 'no_consumer')).toHaveLength(0);
  });

  it('does NOT report no_consumer for stage that connects to a gate', () => {
    const nodes = [makeStage('s1'), makeGate('g1')];
    const edges = [makeEdge('e1', 's1', 'g1')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    expect(issues.filter((i) => i.kind === 'no_consumer' && i.nodeId === 's1')).toHaveLength(0);
  });

  it('no_consumer has warning severity', () => {
    const nodes = [makeStage('s1'), makeStage('s2')];
    const issues = validateWorkflowFull(makeWorkflow(nodes, []));
    for (const issue of issues.filter((i) => i.kind === 'no_consumer')) {
      expect(issue.severity).toBe('warning');
    }
  });
});

describe('validateWorkflowFull — child workflow contract', () => {
  function validChildWorkflow(): Workflow {
    return {
      ...makeWorkflow(
        [makeSubworkflow('children'), makeGate('next')],
        [makeEdge('joined', 'children', 'next', 'children.completed -> children.completed')],
      ),
      workflowDependencies: {
        worker: {
          id: '10000000-0000-0000-0000-000000000001',
          revision: 'child-revision',
          digest: `sha256:${'a'.repeat(64)}`,
        },
      },
      personaDependencies: {
        coordinator: {
          id: 'coordinator',
          revision: 'persona-revision',
          digest: `sha256:${'b'.repeat(64)}`,
        },
      },
    };
  }

  it('accepts a pinned child with one edge-defined joined event', () => {
    const issues = validateWorkflowFull(validChildWorkflow());
    expect(issues.filter((issue) => issue.kind.startsWith('subworkflow_'))).toEqual([]);
  });

  it('requires declared dependency and coordinator aliases', () => {
    const workflow = validChildWorkflow();
    workflow.workflowDependencies = {};
    workflow.personaDependencies = {};

    const issues = validateWorkflowFull(workflow);

    expect(issues).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: 'subworkflow_dependency', severity: 'error' }),
        expect.objectContaining({ kind: 'subworkflow_coordinator', severity: 'error' }),
      ]),
    );
  });

  it('requires a blocked event and one distinct edge-defined joined event', () => {
    const workflow = validChildWorkflow();
    const child = workflow.nodes[0]!;
    if (child.kind !== 'subworkflow') throw new Error('expected subworkflow');
    child.blockedEvent = '';
    workflow.edges = [];

    const issues = validateWorkflowFull(workflow);

    expect(issues).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: 'subworkflow_blocked_event', severity: 'error' }),
        expect.objectContaining({ kind: 'subworkflow_joined_event', severity: 'error' }),
      ]),
    );
  });

  it.each([
    ['the blocked event', ['children.blocked -> next.input']],
    [
      'ambiguous joined events',
      ['children.completed -> next.input', 'children.other -> next.input'],
    ],
    ['a malformed contract', ['not a typed contract']],
  ])('rejects %s as the joined continuation', (_name, labels) => {
    const workflow = validChildWorkflow();
    workflow.edges = labels.map((label, index) =>
      makeEdge(`edge-${index}`, 'children', 'next', label),
    );

    const issues = validateWorkflowFull(workflow);

    expect(issues).toContainEqual(
      expect.objectContaining({ kind: 'subworkflow_joined_event', severity: 'error' }),
    );
  });

  it.each([
    { maxChildren: 0 },
    { maxChildren: 101 },
    { maxAttempts: 0 },
    { maxAttempts: 11 },
    { maxActiveChildren: 0 },
    { maxChildren: 3, maxActiveChildren: 4 },
  ])('rejects runtime-incompatible expansion limits %o', (limits) => {
    const workflow = validChildWorkflow();
    workflow.nodes[0] = { ...workflow.nodes[0]!, ...limits } as WorkflowNode;

    const issues = validateWorkflowFull(workflow);

    expect(issues).toContainEqual(
      expect.objectContaining({ kind: 'subworkflow_limits', severity: 'error' }),
    );
  });
});

describe('validateWorkflowFull — child workflow templates', () => {
  function multiTemplateWorkflow(): Workflow {
    return {
      ...makeWorkflow(
        [makeSubworkflow('children', { breadth: 'worker', depth: 'thread' }), makeGate('next')],
        [makeEdge('joined', 'children', 'next', 'children.completed -> children.completed')],
      ),
      workflowDependencies: {
        worker: {
          id: '10000000-0000-0000-0000-000000000001',
          revision: 'child-revision',
          digest: `sha256:${'a'.repeat(64)}`,
        },
        thread: {
          id: '10000000-0000-0000-0000-000000000002',
          revision: 'child-revision-2',
          digest: `sha256:${'c'.repeat(64)}`,
        },
      },
      personaDependencies: {
        coordinator: {
          id: 'coordinator',
          revision: 'persona-revision',
          digest: `sha256:${'b'.repeat(64)}`,
        },
      },
    };
  }

  it('accepts several templates each pinned to a declared dependency', () => {
    const issues = validateWorkflowFull(multiTemplateWorkflow());
    expect(issues.filter((issue) => issue.kind.startsWith('subworkflow_'))).toEqual([]);
  });

  it('flags a template whose alias is not declared', () => {
    const workflow = multiTemplateWorkflow();
    workflow.workflowDependencies = { worker: workflow.workflowDependencies!.worker! };

    const issues = validateWorkflowFull(workflow);

    expect(issues).toContainEqual(
      expect.objectContaining({
        kind: 'subworkflow_dependency',
        nodeId: 'children',
        message: expect.stringContaining("Template 'depth'"),
      }),
    );
  });

  it('requires a static child to name a template when the node offers several', () => {
    const workflow = multiTemplateWorkflow();
    const node = workflow.nodes[0]!;
    if (node.kind !== 'subworkflow') throw new Error('expected subworkflow');
    node.children = [{ key: 'first', objective: 'Map broadly' }];

    const issues = validateWorkflowFull(workflow);

    expect(issues).toContainEqual(
      expect.objectContaining({
        kind: 'subworkflow_template',
        nodeId: 'children',
        message: expect.stringContaining("Child 'first' must name a template"),
      }),
    );
  });

  it('does not require a template name from a static child when the node offers exactly one', () => {
    const workflow = validChildWorkflowWithOneTemplate();
    const node = workflow.nodes[0]!;
    if (node.kind !== 'subworkflow') throw new Error('expected subworkflow');
    node.children = [{ key: 'first', objective: 'Map broadly' }];

    const issues = validateWorkflowFull(workflow);

    expect(issues.filter((issue) => issue.kind === 'subworkflow_template')).toEqual([]);
  });

  it('flags a static child naming an unknown template', () => {
    const workflow = multiTemplateWorkflow();
    const node = workflow.nodes[0]!;
    if (node.kind !== 'subworkflow') throw new Error('expected subworkflow');
    node.children = [{ key: 'first', objective: 'Map broadly', template: 'unknown' }];

    const issues = validateWorkflowFull(workflow);

    expect(issues).toContainEqual(
      expect.objectContaining({
        kind: 'subworkflow_template',
        nodeId: 'children',
        message: expect.stringContaining("names unknown template 'unknown'"),
      }),
    );
  });

  function validChildWorkflowWithOneTemplate(): Workflow {
    return {
      ...makeWorkflow(
        [makeSubworkflow('children'), makeGate('next')],
        [makeEdge('joined', 'children', 'next', 'children.completed -> children.completed')],
      ),
      workflowDependencies: {
        worker: {
          id: '10000000-0000-0000-0000-000000000001',
          revision: 'child-revision',
          digest: `sha256:${'a'.repeat(64)}`,
        },
      },
      personaDependencies: {
        coordinator: {
          id: 'coordinator',
          revision: 'persona-revision',
          digest: `sha256:${'b'.repeat(64)}`,
        },
      },
    };
  }
});

// ---------------------------------------------------------------------------
// Multiple issues in one workflow
// ---------------------------------------------------------------------------

describe('validateWorkflowFull — multiple issues', () => {
  it('reports all applicable issues for a poorly formed workflow', () => {
    // cond with 1 out, orphan stage, mixed run mapping
    const nodes = [
      makeStage('s1', { runId: 'run-123' }), // orphan, missing_persona
      makeCond('c1'), // dangling_condition (only 1 out), no_producer
      makeStage('s2'), // missing_persona, confidence_underset
    ];
    const edges = [makeEdge('e1', 'c1', 's2')]; // s1 is orphan; c1 has no in
    const issues = validateWorkflowFull(makeWorkflow(nodes, edges));
    const kinds = new Set(issues.map((i) => i.kind));
    expect(kinds.has('orphan')).toBe(true);
    expect(kinds.has('dangling_condition')).toBe(true);
    expect(kinds.has('no_producer')).toBe(true);
    expect(kinds.has('missing_persona')).toBe(true);
    expect(kinds.has('confidence_underset')).toBe(true);
  });

  it('returns empty array for an empty workflow', () => {
    const issues = validateWorkflowFull(makeWorkflow([], []));
    expect(issues).toHaveLength(0);
  });
});
