import { describe, expect, it } from 'vitest';
import type { WorkflowEdge, WorkflowNode } from './workflow';
import { nodePortCatalog, resolveWorkflowEdgePorts } from './workflowPorts';

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
});
