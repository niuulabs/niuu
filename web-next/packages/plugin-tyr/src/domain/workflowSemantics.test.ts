import { describe, expect, it } from 'vitest';
import type { Workflow, WorkflowEdge, WorkflowStageNode } from './workflow';
import {
  inferModelVendor,
  isReentryEdge,
  isReentryEventType,
  normalizeStageNode,
  normalizeWorkflowGraph,
  parseWorkflowEdgeLabel,
  stagePersonaIds,
  structuralWorkflowEdges,
  workflowModelVendors,
  workflowPersonaModelConflicts,
  workflowStageModels,
} from './workflowSemantics';

function makeEdge(
  id: string,
  label?: string,
  source = 'a',
  target = 'b',
): WorkflowEdge {
  return {
    id,
    source,
    target,
    label,
    cp1: { x: 0, y: 0 },
    cp2: { x: 0, y: 0 },
  };
}

function makeStage(overrides: Partial<WorkflowStageNode> = {}): WorkflowStageNode {
  return {
    id: 'stage-1',
    kind: 'stage',
    label: 'Stage',
    raidId: null,
    personaIds: [],
    stageMembers: [],
    position: { x: 0, y: 0 },
    ...overrides,
  };
}

describe('workflowSemantics', () => {
  it('parses workflow edge labels and rejects invalid forms', () => {
    expect(parseWorkflowEdgeLabel('review.completed -> code.requested')).toEqual({
      sourceEventType: 'review.completed',
      targetEventType: 'code.requested',
    });
    expect(parseWorkflowEdgeLabel('review.completed')).toBeNull();
    expect(parseWorkflowEdgeLabel(' -> code.requested')).toBeNull();
    expect(parseWorkflowEdgeLabel(undefined)).toBeNull();
  });

  it('detects re-entry event types and edges', () => {
    expect(isReentryEventType('review.changes_requested')).toBe(true);
    expect(isReentryEventType('security-retry')).toBe(true);
    expect(isReentryEventType('review.completed')).toBe(false);
    expect(isReentryEventType(undefined)).toBe(false);

    expect(isReentryEdge(makeEdge('retry', 'review.changes_requested -> code.requested'))).toBe(
      true,
    );
    expect(isReentryEdge(makeEdge('normal', 'code.changed -> review.requested'))).toBe(false);
    expect(isReentryEdge(makeEdge('missing'))).toBe(false);
  });

  it('prefers stage members over personaIds and deduplicates both', () => {
    expect(
      stagePersonaIds(
        makeStage({
          personaIds: ['reviewer', 'coder', 'reviewer'],
          stageMembers: [
            { personaId: 'security-auditor', model: '', budget: 40, consumesEventTypes: [] },
            { personaId: 'security-auditor', model: '', budget: 40, consumesEventTypes: [] },
            { personaId: 'coder', model: '', budget: 40, consumesEventTypes: [] },
          ],
        }),
      ),
    ).toEqual(['security-auditor', 'coder']);

    expect(stagePersonaIds(makeStage({ personaIds: ['coder', 'coder', 'reviewer'] }))).toEqual([
      'coder',
      'reviewer',
    ]);
  });

  it('infers model vendors from explicit ids', () => {
    expect(inferModelVendor('claude-sonnet-4-6')).toBe('anthropic');
    expect(inferModelVendor('gpt-5.5')).toBe('openai');
    expect(inferModelVendor('codex-mini')).toBe('openai');
    expect(inferModelVendor('o3')).toBe('openai');
    expect(inferModelVendor('llama3.2:latest')).toBe('local');
    expect(inferModelVendor('')).toBe('');
    expect(inferModelVendor('mystery-model')).toBe('');
  });

  it('collects workflow stage models and vendors with catalog overrides', () => {
    const workflow: Workflow = {
      id: '00000000-0000-0000-0000-000000000001',
      name: 'Runtime Mix',
      nodes: [
        makeStage({
          stageMembers: [
            { personaId: 'coder', model: 'claude-sonnet-4-6', budget: 40, consumesEventTypes: [] },
            { personaId: 'reviewer', model: 'custom-managed', budget: 30, consumesEventTypes: [] },
            { personaId: 'security-auditor', model: 'llama3.2:latest', budget: 20, consumesEventTypes: [] },
            { personaId: 'mimir-memory-curator', model: '   ', budget: 20, consumesEventTypes: [] },
          ],
        }),
      ],
      edges: [],
      resourceBindings: [],
    };

    expect(workflowStageModels(workflow)).toEqual([
      'claude-sonnet-4-6',
      'custom-managed',
      'llama3.2:latest',
    ]);
    expect(
      workflowModelVendors(workflow, {
        'custom-managed': { vendor: 'custom-cloud' },
      }),
    ).toEqual(new Set(['anthropic', 'custom-cloud', 'local']));
  });

  it('reports persona/model conflicts only when a persona uses multiple models', () => {
    const workflow: Workflow = {
      id: '00000000-0000-0000-0000-000000000002',
      name: 'Conflict Scan',
      nodes: [
        makeStage({
          id: 'stage-a',
          stageMembers: [
            { personaId: 'coder', model: 'claude-sonnet-4-6', budget: 40, consumesEventTypes: [] },
            { personaId: 'reviewer', model: 'gpt-5.5', budget: 30, consumesEventTypes: [] },
          ],
        }),
        makeStage({
          id: 'stage-b',
          stageMembers: [
            { personaId: 'coder', model: 'gpt-5.5', budget: 40, consumesEventTypes: [] },
            { personaId: 'reviewer', model: 'gpt-5.5', budget: 30, consumesEventTypes: [] },
            { personaId: '', model: 'claude-sonnet-4-6', budget: 10, consumesEventTypes: [] },
          ],
        }),
      ],
      edges: [],
      resourceBindings: [],
    };

    expect(workflowPersonaModelConflicts(workflow)).toEqual({
      coder: ['claude-sonnet-4-6', 'gpt-5.5'],
    });
  });

  it('normalizes stage nodes and preserves already normalized ones by identity', () => {
    const raw = makeStage({
      personaIds: ['legacy-coder'],
      stageMembers: [{ personaId: 'coder', budget: undefined, model: undefined, consumesEventTypes: undefined, eventFilters: undefined }],
    });
    const normalized = normalizeStageNode(raw);

    expect(normalized.personaIds).toEqual(['coder']);
    expect(normalized.stageMembers).toEqual([
      {
        personaId: 'coder',
        model: '',
        budget: 40,
        consumesEventTypes: [],
        eventFilters: {},
      },
    ]);
    expect(normalized.executionMode).toBe('parallel');
    expect(normalized.maxConcurrent).toBe(3);
    expect(normalized.joinMode).toBe('all');

    const alreadyNormalized = makeStage({
      personaIds: ['coder'],
      stageMembers: [
        {
          personaId: 'coder',
          model: 'gpt-5.5',
          budget: 40,
          consumesEventTypes: ['code.requested'],
          eventFilters: {},
        },
      ],
      executionMode: 'parallel',
      maxConcurrent: 3,
      joinMode: 'all',
    });
    expect(normalizeStageNode(alreadyNormalized)).toBe(alreadyNormalized);
  });

  it('normalizes workflow graphs only when a stage node changes', () => {
    const unchangedWorkflow: Workflow = {
      id: '00000000-0000-0000-0000-000000000003',
      name: 'Normalize',
      nodes: [
        {
          id: 'trigger-1',
          kind: 'trigger',
          label: 'Start',
          source: 'manual dispatch',
          dispatchEvent: 'code.requested',
          position: { x: 0, y: 0 },
        },
      ],
      edges: [],
      resourceBindings: [],
    };
    expect(normalizeWorkflowGraph(unchangedWorkflow)).toBe(unchangedWorkflow);

    const legacyWorkflow: Workflow = {
      ...unchangedWorkflow,
      nodes: [
        makeStage({
          personaIds: [],
          stageMembers: [
            {
              personaId: 'reviewer',
              budget: 40,
              model: '',
              consumesEventTypes: [],
              eventFilters: {},
            },
          ],
        }),
      ],
    };
    const normalized = normalizeWorkflowGraph(legacyWorkflow);
    expect(normalized).not.toBe(legacyWorkflow);
    expect(normalized.nodes[0]).toMatchObject({ personaIds: ['reviewer'] });
  });

  it('filters structural edges by removing re-entry loops', () => {
    const normal = makeEdge('normal', 'code.changed -> review.requested');
    const reentry = makeEdge('reentry', 'review.changes_requested -> code.requested');
    const retryTarget = makeEdge('retry-target', 'code.changed -> security.retry');

    expect(structuralWorkflowEdges([normal, reentry, retryTarget])).toEqual([normal]);
  });
});
