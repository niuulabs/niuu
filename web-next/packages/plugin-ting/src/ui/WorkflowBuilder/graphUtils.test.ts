import { describe, it, expect } from 'vitest';
import {
  makeNodeId,
  makeEdgeId,
  defaultBezierCPs,
  normalizedStageMembers,
  nodeCentre,
  edgeToPath,
  workflowToYaml,
  STAGE_WIDTH,
  STAGE_HEIGHT,
  GATE_SIZE,
  COND_RADIUS,
  TRIGGER_WIDTH,
  TRIGGER_HEIGHT,
  END_RADIUS,
  RESOURCE_WIDTH,
  RESOURCE_HEIGHT,
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
      runId: null,
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
      mode: 'human_approval',
      pendingBehavior: 'help_needed',
      approvalEvent: 'gate.approved',
      changesRequestedEvent: 'gate.changes_requested',
      instructions: 'Approve or request changes.',
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

  it('returns centres for trigger, end, and resource nodes', () => {
    expect(
      nodeCentre({
        id: 'trigger-1',
        kind: 'trigger',
        label: 'Trigger',
        position: { x: 10, y: 20 },
      }),
    ).toEqual({ x: 10 + TRIGGER_WIDTH / 2, y: 20 + TRIGGER_HEIGHT / 2 });
    expect(
      nodeCentre({
        id: 'end-1',
        kind: 'end',
        label: 'End',
        position: { x: 30, y: 40 },
      }),
    ).toEqual({ x: 30 + END_RADIUS, y: 40 + END_RADIUS });
    expect(
      nodeCentre({
        id: 'resource-1',
        kind: 'resource',
        label: 'Resource',
        resourceType: 'mimir',
        bindingMode: 'registry',
        registryEntryId: null,
        seedFromRegistryId: null,
        categories: [],
        position: { x: 50, y: 60 },
      }),
    ).toEqual({ x: 50 + RESOURCE_WIDTH / 2, y: 60 + RESOURCE_HEIGHT / 2 });
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
    runId: null,
    personaIds: [],
    position: { x: 0, y: 0 },
  };
  const stageB: WorkflowNode = {
    id: 'b',
    kind: 'stage',
    label: 'B',
    runId: null,
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
        runId: 'run-123',
        personaIds: ['persona-build'],
        position: { x: 100, y: 100 },
      },
      {
        id: 'gate-1',
        kind: 'gate' as const,
        label: 'QA sign-off',
        condition: 'all tests pass',
        mode: 'human_approval' as const,
        pendingBehavior: 'help_needed' as const,
        approvalEvent: 'qa.approved',
        changesRequestedEvent: 'qa.changes_requested',
        instructions: 'Approve or request changes with notes.',
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

  it('includes gate pending behavior', () => {
    const yaml = workflowToYaml(workflow);
    expect(yaml).toContain('pendingBehavior: "help_needed"');
  });

  it('includes explicit gate config fields', () => {
    const yaml = workflowToYaml(workflow);
    expect(yaml).toContain('mode: "human_approval"');
    expect(yaml).toContain('approvalEvent: "qa.approved"');
    expect(yaml).toContain('changesRequestedEvent: "qa.changes_requested"');
    expect(yaml).toContain('instructions: "Approve or request changes with notes."');
  });

  it('includes cond predicate', () => {
    const yaml = workflowToYaml(workflow);
    expect(yaml).toContain('predicate:');
    expect(yaml).toContain('ci.exitCode === 0');
  });

  it('outputs "null" for null runId', () => {
    const wf = {
      ...workflow,
      nodes: [{ ...workflow.nodes[0]!, runId: null, personaIds: [] }],
      edges: [],
    };
    const yaml = workflowToYaml(wf);
    expect(yaml).toContain('runId: null');
  });

  it('normalizes stage members from legacy personaIds and explicit stageMembers', () => {
    expect(
      normalizedStageMembers({
        id: 'stage-legacy',
        kind: 'stage',
        label: 'Legacy',
        runId: null,
        personaIds: ['persona-a'],
        position: { x: 0, y: 0 },
      }),
    ).toEqual([
      {
        personaId: 'persona-a',
        model: '',
        budget: 40,
        consumesEventTypes: [],
        eventFilters: {},
      },
    ]);

    expect(
      normalizedStageMembers({
        id: 'stage-members',
        kind: 'stage',
        label: 'Members',
        runId: 'run-1',
        personaIds: [],
        stageMembers: [
          {
            personaId: 'persona-b',
            model: 'gpt-test',
            budget: 25,
            consumesEventTypes: ['code.requested'],
            eventFilters: { branch: 'main' },
          },
        ],
        position: { x: 0, y: 0 },
      }),
    ).toEqual([
      {
        personaId: 'persona-b',
        model: 'gpt-test',
        budget: 25,
        consumesEventTypes: ['code.requested'],
        eventFilters: { branch: 'main' },
      },
    ]);

    expect(
      normalizedStageMembers({
        id: 'stage-minimal-member',
        kind: 'stage',
        label: 'Minimal',
        runId: null,
        personaIds: [],
        stageMembers: [{ personaId: 'persona-c' }],
        position: { x: 0, y: 0 },
      }),
    ).toEqual([
      {
        personaId: 'persona-c',
        model: '',
        budget: 40,
        consumesEventTypes: [],
        eventFilters: {},
      },
    ]);
  });

  it('serializes tags, trigger nodes, resource nodes, and rich stage member details', () => {
    const yaml = workflowToYaml({
      id: 'wf-rich',
      name: 'Rich Workflow',
      tags: ['release', 'ops'],
      nodes: [
        {
          id: 'stage-rich',
          kind: 'stage',
          label: 'Stage',
          runId: null,
          personaIds: [],
          stageMembers: [
            {
              personaId: 'persona-1',
              model: 'gpt-test',
              budget: 30,
              consumesEventTypes: ['code.requested'],
              eventFilters: { repo: 'niuu' },
            },
          ],
          executionMode: 'serial',
          maxConcurrent: 1,
          joinMode: 'any',
          position: { x: 0, y: 0 },
        },
        {
          id: 'trigger-rich',
          kind: 'trigger',
          label: 'Trigger',
          source: 'slack',
          dispatchEvent: 'release.requested',
          position: { x: 10, y: 10 },
        },
        {
          id: 'resource-rich',
          kind: 'resource',
          label: 'Knowledge',
          resourceType: 'mimir',
          bindingMode: 'registry',
          registryEntryId: 'registry-1',
          seedFromRegistryId: 'seed-1',
          categories: ['docs'],
          path: '/workspace/docs',
          url: 'https://example.test/docs',
          position: { x: 20, y: 20 },
        },
      ],
      edges: [],
      resourceBindings: [],
    });

    expect(yaml).toContain('tags: ["release", "ops"]');
    expect(yaml).toContain('stageMembers:');
    expect(yaml).toContain('consumesEventTypes: ["code.requested"]');
    expect(yaml).toContain('eventFilters: {repo: "niuu"}');
    expect(yaml).toContain('executionMode: serial');
    expect(yaml).toContain('joinMode: any');
    expect(yaml).toContain('source: "slack"');
    expect(yaml).toContain('dispatchEvent: "release.requested"');
    expect(yaml).toContain('resourceType: "mimir"');
    expect(yaml).toContain('registryEntryId: "registry-1"');
    expect(yaml).toContain('seedFromRegistryId: "seed-1"');
    expect(yaml).toContain('categories: ["docs"]');
    expect(yaml).toContain('path: "/workspace/docs"');
    expect(yaml).toContain('url: "https://example.test/docs"');
  });

  it('omits blank optional gate fields and uses trigger/resource defaults', () => {
    const yaml = workflowToYaml({
      id: 'wf-defaults',
      name: 'Defaults',
      nodes: [
        {
          id: 'gate-defaults',
          kind: 'gate',
          label: 'Gate',
          condition: 'ready',
          mode: 'human_approval',
          pendingBehavior: 'help_needed',
          approvalEvent: '   ',
          changesRequestedEvent: '',
          instructions: ' ',
          autoForwardAfter: null,
          position: { x: 0, y: 0 },
        },
        {
          id: 'trigger-defaults',
          kind: 'trigger',
          label: 'Trigger',
          position: { x: 5, y: 5 },
        },
        {
          id: 'resource-defaults',
          kind: 'resource',
          label: 'Resource',
          position: { x: 10, y: 10 },
        },
      ],
      edges: [],
      resourceBindings: [],
    });

    expect(yaml).not.toContain('approvalEvent:');
    expect(yaml).not.toContain('changesRequestedEvent:');
    expect(yaml).not.toContain('instructions:');
    expect(yaml).toContain('autoForwardAfter: "30m"');
    expect(yaml).toContain('source: "manual dispatch"');
    expect(yaml).toContain('dispatchEvent: "code.requested"');
    expect(yaml).toContain('resourceType: "mimir"');
    expect(yaml).toContain('bindingMode: "registry"');
    expect(yaml).toContain('registryEntryId: undefined');
    expect(yaml).toContain('seedFromRegistryId: undefined');
  });

  it('serializes resource role/auth fields, edge labels, and resource bindings', () => {
    const yaml = workflowToYaml({
      id: 'wf-bindings',
      name: 'Bindings',
      nodes: [
        {
          id: 'resource-auth',
          kind: 'resource',
          label: 'Resource',
          resourceType: 'http',
          bindingMode: 'external',
          registryEntryId: null,
          seedFromRegistryId: null,
          categories: ['api'],
          role: 'reader',
          authRef: 'AUTH_TOKEN',
          defaultReadPriority: 7,
          position: { x: 0, y: 0 },
        },
      ],
      edges: [
        {
          id: 'edge-labeled',
          source: 'resource-auth',
          target: 'resource-auth',
          label: 'loop',
          cp1: { x: 10, y: 20 },
          cp2: { x: -10, y: -20 },
        },
      ],
      resourceBindings: [
        {
          id: 'binding-1',
          resourceNodeId: 'resource-auth',
          targetType: 'stage',
          targetId: 'stage-1',
          access: 'write',
          writePrefixes: ['/docs'],
          readPriority: 2,
        },
      ],
    });

    expect(yaml).toContain('role: "reader"');
    expect(yaml).toContain('authRef: "AUTH_TOKEN"');
    expect(yaml).toContain('defaultReadPriority: 7');
    expect(yaml).toContain('label: "loop"');
    expect(yaml).toContain('resourceBindings:');
    expect(yaml).toContain('writePrefixes: ["/docs"]');
    expect(yaml).toContain('readPriority: 2');
  });
});
