import { describe, expect, it, vi } from 'vitest';
import type { WorkflowEdge, WorkflowNode } from './workflow';
import { nodePortCatalog, resolveIncludeEndpoint, resolveWorkflowEdgePorts } from './workflowPorts';

const pos = { x: 0, y: 0 };

function edge(id: string, source: string, target: string, label?: string): WorkflowEdge {
  return { id, source, target, label, cp1: pos, cp2: pos };
}

describe('workflow port contracts', () => {
  it('retains both unresolved sockets for a malformed historical self-connection', () => {
    const node: WorkflowNode = { id: 'retry', kind: 'wait', label: 'Retry', position: pos };
    const saved = edge('historical-loop', node.id, node.id, 'invalid contract');
    const resolved = resolveWorkflowEdgePorts(saved, new Map([[node.id, node]]));
    expect(resolved.resolved).toBe(false);
    expect(resolved.sourcePort).toMatchObject({ direction: 'output', resolved: false });
    expect(resolved.targetPort).toMatchObject({ direction: 'input', resolved: false });
    expect(resolved.sourcePort?.eventType).toBe(resolved.targetPort?.eventType);
    expect(resolved.reason).toContain('no valid source');
  });

  it('resolves Developer Delivery stage-scoped inputs and every coordinator outcome', () => {
    const node: WorkflowNode = {
      id: 'delivery-publish',
      kind: 'stage',
      label: 'Validate remote candidate and conditionally merge',
      runId: null,
      personaIds: ['developer-coordinator'],
      stageMembers: [
        {
          personaId: 'developer-coordinator',
          model: '',
          budget: 36,
          consumesEventTypes: ['developer.integration.approved', 'developer.delivery.observed'],
          eventFilters: {},
        },
      ],
      executionMode: 'parallel',
      maxConcurrent: 1,
      joinMode: 'all',
      position: pos,
    };
    const catalog = nodePortCatalog(node, {
      personas: [
        {
          id: 'developer-coordinator',
          consumes: ['delivery.requested'],
          produces: ['developer.coordination.decision'],
          outcomeEvents: {
            wait_delivery: 'developer.delivery.waiting',
            repair: 'developer.workstream.repair_requested',
            publish: 'developer.delivery.completed',
            blocked: 'developer.delivery.blocked',
          },
        },
      ],
    });

    expect(catalog.inputs.map((port) => port.eventType)).toEqual([
      'developer.delivery.observed',
      'developer.integration.approved',
    ]);
    expect(catalog.inputs.every((port) => port.origin === 'stage-binding')).toBe(true);
    expect(catalog.outputs.map((port) => port.eventType)).toEqual([
      'developer.coordination.decision',
      'developer.delivery.blocked',
      'developer.delivery.completed',
      'developer.delivery.waiting',
      'developer.workstream.repair_requested',
    ]);
    expect(catalog.outputs.find((port) => port.eventType.endsWith('.blocked'))?.label).toBe(
      'blocked',
    );
  });

  it('uses Research Campaign edge-defined subworkflow contracts', () => {
    const node: WorkflowNode = {
      id: 'research-threads',
      kind: 'subworkflow',
      label: 'Explore the framed question across parallel threads',
      position: pos,
      templates: { default: 'research-thread' },
      allowedCoordinator: 'research-coordinator',
      inputSchema: { type: 'object' },
      resultSchema: { type: 'object' },
      maxChildren: 8,
      maxAttempts: 2,
      joinMode: 'all',
      blockedEvent: 'research.threads.blocked',
    };
    const edges = [
      edge(
        'coordinate-threads',
        'research-coordinate',
        node.id,
        'research.threads.waiting -> research.threads.waiting',
      ),
      edge(
        'threads-analysis',
        node.id,
        'research-analysis',
        'research.threads.joined -> research.threads.joined',
      ),
    ];
    const catalog = nodePortCatalog(node, { edges });
    expect(catalog.inputs.map((port) => port.eventType)).toEqual([
      'children.requested',
      'research.threads.waiting',
    ]);
    expect(catalog.outputs.map((port) => port.eventType)).toEqual(['research.threads.joined']);
    expect(catalog.inputs.every((port) => port.resolved)).toBe(true);
    expect(catalog.outputs.every((port) => port.resolved)).toBe(true);
  });

  it('offers a generic joined socket for a new subworkflow without treating blocked as flow', () => {
    const node: WorkflowNode = {
      id: 'children',
      kind: 'subworkflow',
      label: 'Child workflows',
      position: pos,
      templates: { default: 'worker' },
      allowedCoordinator: 'coordinator',
      inputSchema: { type: 'object' },
      resultSchema: { type: 'object' },
      maxChildren: 10,
      maxAttempts: 3,
      joinMode: 'all',
      blockedEvent: 'children.blocked',
    };

    const catalog = nodePortCatalog(node);

    expect(catalog.outputs).toEqual([
      expect.objectContaining({
        eventType: 'children.completed',
        label: 'completed',
        origin: 'node',
        resolved: true,
      }),
    ]);
    expect(catalog.outputs.some((port) => port.eventType === node.blockedEvent)).toBe(false);
  });

  it('preserves a malformed historical subworkflow edge beside the usable joined fallback', () => {
    const node: WorkflowNode = {
      id: 'children',
      kind: 'subworkflow',
      label: 'Child workflows',
      position: pos,
      templates: { default: 'worker' },
      allowedCoordinator: 'coordinator',
      inputSchema: { type: 'object' },
      resultSchema: { type: 'object' },
      maxChildren: 10,
      maxAttempts: 3,
      joinMode: 'all',
      blockedEvent: 'children.blocked',
    };

    const catalog = nodePortCatalog(node, {
      edges: [edge('malformed', node.id, 'done', 'not a typed contract')],
    });

    expect(catalog.outputs.map((port) => port.eventType)).toEqual([
      'children.completed',
      'unresolved-edge:malformed',
    ]);
    expect(catalog.outputs[1]).toMatchObject({ resolved: false, origin: 'edge' });
  });

  it('keeps edge resolution index/count aligned with a full multi-edge node catalog', () => {
    const source: WorkflowNode = {
      id: 'decision',
      kind: 'cond',
      label: 'Decision',
      predicate: 'result.ready',
      position: pos,
    };
    const firstTarget: WorkflowNode = { id: 'ready', kind: 'end', label: 'Ready', position: pos };
    const secondTarget: WorkflowNode = { id: 'retry', kind: 'wait', label: 'Retry', position: pos };
    const edges = [
      edge('ready-edge', source.id, firstTarget.id, 'result.ready -> result.ready'),
      edge('retry-edge', source.id, secondTarget.id, 'result.retry -> result.retry'),
    ];
    const nodes = new Map([
      [source.id, source],
      [firstTarget.id, firstTarget],
      [secondTarget.id, secondTarget],
    ]);
    const catalog = nodePortCatalog(source, { edges });
    const resolved = resolveWorkflowEdgePorts(edges[1]!, nodes, { edges });
    expect(resolved.sourcePort).toEqual(
      catalog.outputs.find((port) => port.eventType === 'result.retry'),
    );
    expect(resolved.sourcePort).toMatchObject({ index: 1, count: 2 });
  });

  it('keeps a stale stage edge visible as an explicitly unresolved socket', () => {
    const source: WorkflowNode = {
      id: 'review',
      kind: 'stage',
      label: 'Review',
      runId: null,
      personaIds: ['reviewer'],
      stageMembers: [{ personaId: 'reviewer', model: '', budget: 40 }],
      executionMode: 'parallel',
      maxConcurrent: 1,
      joinMode: 'all',
      position: pos,
    };
    const target: WorkflowNode = { id: 'done', kind: 'end', label: 'Done', position: pos };
    const stale = edge('stale', source.id, target.id, 'review.old_outcome -> review.completed');
    const nodes = new Map([
      [source.id, source],
      [target.id, target],
    ]);
    const result = resolveWorkflowEdgePorts(stale, nodes, {
      personas: [{ id: 'reviewer', produces: ['review.completed'] }],
    });
    expect(result.sourcePort).toMatchObject({
      eventType: 'review.old_outcome',
      resolved: false,
      side: 'right',
    });
    expect(result.targetPort).toMatchObject({ eventType: 'review.completed', resolved: true });
    expect(result.resolved).toBe(false);
  });

  it('creates dedicated unresolved sockets for malformed historical labels', () => {
    const source: WorkflowNode = {
      id: 'start',
      kind: 'trigger',
      label: 'Start',
      source: 'manual',
      dispatchEvent: 'research.requested',
      position: pos,
    };
    const target: WorkflowNode = { id: 'done', kind: 'end', label: 'Done', position: pos };
    const malformed = edge('legacy', source.id, target.id, 'old freeform label');
    const result = resolveWorkflowEdgePorts(
      malformed,
      new Map([
        [source.id, source],
        [target.id, target],
      ]),
    );
    expect(result.sourcePort?.eventType).toBe('unresolved-edge:legacy');
    expect(result.targetPort?.eventType).toBe('unresolved-edge:legacy');
    expect(result.resolved).toBe(false);
  });

  it('does not invent event-flow ports for resource bindings', () => {
    const resource: WorkflowNode = {
      id: 'memory',
      kind: 'resource',
      label: 'Research Memory',
      resourceType: 'mimir',
      bindingMode: 'registry',
      registryEntryId: 'research',
      seedFromRegistryId: null,
      categories: [],
      position: pos,
    };
    expect(nodePortCatalog(resource)).toEqual({ inputs: [], outputs: [] });
  });

  describe('include nodes', () => {
    const includeNode: WorkflowNode = {
      id: 'delivery-planning',
      kind: 'include',
      label: 'Plan the delivery',
      workflow: 'planning',
      nodes: {
        'planning-analysis': 'delivery-plan-author',
        'planning-reviews': 'delivery-plan-reviews',
      },
      position: pos,
    };

    it('has no fixed ports before any edge touches a provided id', () => {
      expect(nodePortCatalog(includeNode)).toEqual({ inputs: [], outputs: [] });
    });

    it('derives a port from an edge whose source is a provided local id', () => {
      const edges = [edge('e1', 'delivery-plan-author', 'gate-1', 'plan.ready -> plan.ready')];
      const catalog = nodePortCatalog(includeNode, { edges });
      expect(catalog.outputs).toEqual([
        expect.objectContaining({ eventType: 'plan.ready', resolved: true, origin: 'edge' }),
      ]);
      expect(catalog.inputs).toEqual([]);
    });

    it('derives a port from an edge whose target is a provided local id', () => {
      const edges = [
        edge('e1', 'trigger-1', 'delivery-plan-reviews', 'plan.requested -> plan.requested'),
      ];
      const catalog = nodePortCatalog(includeNode, { edges });
      expect(catalog.inputs).toEqual([
        expect.objectContaining({ eventType: 'plan.requested', resolved: true, origin: 'edge' }),
      ]);
      expect(catalog.outputs).toEqual([]);
    });

    it('combines ports contributed by different provided ids into one catalog', () => {
      const edges = [
        edge('e1', 'trigger-1', 'delivery-plan-author', 'plan.requested -> plan.requested'),
        edge('e2', 'delivery-plan-reviews', 'gate-1', 'plan.reviewed -> plan.reviewed'),
      ];
      const catalog = nodePortCatalog(includeNode, { edges });
      expect(catalog.inputs.map((port) => port.eventType)).toEqual(['plan.requested']);
      expect(catalog.outputs.map((port) => port.eventType)).toEqual(['plan.reviewed']);
    });

    it('ignores edges that reference neither the include nor any of its provided ids', () => {
      const edges = [edge('e1', 'unrelated-a', 'unrelated-b', 'x -> x')];
      expect(nodePortCatalog(includeNode, { edges })).toEqual({ inputs: [], outputs: [] });
    });

    it('resolves edge ports through an extended node map keyed by provided id', () => {
      // A `cond` node's outputs are always "open", so this edge's source
      // side resolves regardless of contract details unrelated to the
      // include target this test actually exercises.
      const upstream: WorkflowNode = {
        id: 'trigger-1',
        kind: 'cond',
        label: 'Decision',
        predicate: '',
        position: pos,
      };
      const saved = edge(
        'plan-edge',
        'trigger-1',
        'delivery-plan-reviews',
        'plan.requested -> plan.requested',
      );
      const nodes = new Map<string, WorkflowNode>([
        ['trigger-1', upstream],
        ['delivery-planning', includeNode],
        // The canvas aliases every provided id to the owning include node so
        // an edge's target resolves to a real WorkflowNode.
        ['delivery-plan-reviews', includeNode],
      ]);
      const resolved = resolveWorkflowEdgePorts(saved, nodes, { edges: [saved] });
      expect(resolved.resolved).toBe(true);
      expect(resolved.targetPort).toMatchObject({ eventType: 'plan.requested', resolved: true });
    });
  });

  describe('the endpoint an edge uses on an include node', () => {
    const planning: WorkflowNode = {
      id: 'delivery-planning',
      kind: 'include',
      label: 'Plan the delivery',
      workflow: 'planning',
      nodes: {
        'planning-analysis': 'delivery-plan-author',
        'planning-reviews': 'delivery-plan-reviews',
      },
      position: pos,
    };
    const single: WorkflowNode = { ...planning, nodes: { 'planning-analysis': 'only-stage' } };
    const never = vi.fn(() => null);

    it('is the node itself for every other kind', () => {
      const wait: WorkflowNode = { id: 'retry', kind: 'wait', label: 'Retry', position: pos };

      expect(resolveIncludeEndpoint(wait, 'plan.ready', 'source', [], never)).toBe('retry');
      expect(never).not.toHaveBeenCalled();
    });

    it('is nothing when the include provides no nodes', () => {
      const empty = { ...planning, nodes: {} } as WorkflowNode;

      expect(resolveIncludeEndpoint(empty, 'plan.ready', 'source', [], never)).toBeNull();
    });

    it('keeps the provided id an outgoing edge already uses for that event', () => {
      const edges = [edge('e1', 'delivery-plan-reviews', 'gate', 'plan.ready -> plan.ready')];

      expect(resolveIncludeEndpoint(planning, 'plan.ready', 'source', edges, never)).toBe(
        'delivery-plan-reviews',
      );
    });

    it('keeps the provided id an incoming edge already uses for that event', () => {
      const edges = [edge('e1', 'start', 'delivery-plan-author', 'plan.asked -> plan.requested')];

      expect(resolveIncludeEndpoint(planning, 'plan.requested', 'target', edges, never)).toBe(
        'delivery-plan-author',
      );
    });

    it('ignores edges for another event, the other direction, or without a label', () => {
      const edges = [
        edge('other-event', 'delivery-plan-reviews', 'gate', 'plan.rejected -> plan.rejected'),
        edge('other-direction', 'start', 'delivery-plan-reviews', 'plan.ready -> plan.ready'),
        edge('unlabelled', 'delivery-plan-reviews', 'gate'),
      ];
      const choose = vi.fn(() => 'delivery-plan-author');

      expect(resolveIncludeEndpoint(planning, 'plan.ready', 'source', edges, choose)).toBe(
        'delivery-plan-author',
      );
      expect(choose).toHaveBeenCalledWith('plan.ready', [
        'delivery-plan-author',
        'delivery-plan-reviews',
      ]);
    });

    it('needs no question when only one node is provided', () => {
      expect(resolveIncludeEndpoint(single, 'plan.ready', 'source', [], never)).toBe('only-stage');
    });

    it('accepts the chosen node, ignoring surrounding whitespace', () => {
      const choose = vi.fn(() => '  delivery-plan-reviews ');

      expect(resolveIncludeEndpoint(planning, 'plan.ready', 'source', [], choose)).toBe(
        'delivery-plan-reviews',
      );
    });

    it('is nothing when the choice is declined or is not a provided node', () => {
      expect(resolveIncludeEndpoint(planning, 'plan.ready', 'source', [], () => null)).toBeNull();
      expect(resolveIncludeEndpoint(planning, 'plan.ready', 'source', [], () => '')).toBeNull();
      expect(
        resolveIncludeEndpoint(planning, 'plan.ready', 'source', [], () => 'somewhere-else'),
      ).toBeNull();
    });
  });

  it('does not resolve an edge whose endpoints are not in the graph', () => {
    const resolved = resolveWorkflowEdgePorts(edge('dangling', 'gone', 'also-gone'), new Map());

    expect(resolved).toMatchObject({
      edgeId: 'dangling',
      sourcePort: null,
      targetPort: null,
      resolved: false,
    });
  });
});
