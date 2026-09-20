import type { WorkflowExecutionTrace, ExecutionTraceEvent } from '../domain/workflowExecutionTrace';

export const traceEvent: ExecutionTraceEvent = {
  eventId: 'outcome-1',
  sessionId: 'parent',
  seq: 1,
  occurredAt: '2026-09-19T12:00:00Z',
  eventType: 'review.completed',
  persona: 'reviewer',
  summary: 'Request changes',
  verdict: 'changes',
  valid: true,
  fields: { findings: ['Reject invalid input'] },
  nodeIds: ['review'],
  mapping: 'explicit',
};
export const traceFixture: WorkflowExecutionTrace = {
  schemaVersion: 1,
  execution: {
    executionId: 'run',
    state: 'completed',
    currentGeneration: 2,
    parentSessionId: 'parent',
  },
  workflow: {
    workflowId: 'workflow',
    revision: 'r1',
    digest: 'sha256:workflow',
    name: 'Delivery',
    graph: {
      nodes: [
        { id: 'review', label: 'Review', kind: 'stage' },
        { id: 'workers', label: 'Workers', kind: 'subworkflow' },
      ],
      edges: [{ id: 'edge', source: 'review', target: 'workers' }],
    },
  },
  sessions: [
    { sessionId: 'parent', kind: 'parent' },
    {
      sessionId: 'child',
      kind: 'child',
      childId: 'c1',
      taskId: 'task1',
      childKey: 'parser',
      generation: 1,
      attempt: 1,
      state: 'completed',
      parentNodeId: 'workers',
    },
  ],
  selectedSessionId: 'parent',
  selectedChildId: null,
  page: { after: 0, scannedThrough: 1, hasMore: false },
  events: [traceEvent],
  nodeHistory: { review: ['outcome-1'] },
  unattachedEventIds: [],
};
