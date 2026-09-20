import { describe, expect, it } from 'vitest';
import { traceFixture, traceEvent } from './developerExecutionTrace.fixture';
import { projectDeveloperExecutionTrace } from './developerExecutionTrace';

describe('execution trace projection', () => {
  it('renders plan Markdown alongside the original structured outcome', () => {
    const graph = projectDeveloperExecutionTrace([
      {
        ...traceFixture,
        events: [
          {
            ...traceEvent,
            canonicalEventType: 'review.approved',
            fields: { plan: '# Accepted plan\n\nImplement parser validation.' },
          },
        ],
      },
    ]);
    expect(graph.nodes[0]?.outputs?.[0]).toMatchObject({
      format: 'markdown',
      value: '# Accepted plan\n\nImplement parser validation.',
    });
    expect(graph.nodes[0]?.outputs?.[1]).toMatchObject({ format: 'structured' });
    expect(graph.nodes[0]?.events?.[0]?.fields?.canonicalEventType).toBe('review.approved');
  });
  it('retains repairs in sequence order without claiming completion and preserves historical children', () => {
    const later = {
      ...traceEvent,
      eventId: 'outcome-2',
      seq: 2,
      verdict: 'pass',
      summary: 'Approved',
    };
    const graph = projectDeveloperExecutionTrace([
      traceFixture,
      { ...traceFixture, events: [later, traceEvent] },
    ]);
    expect(graph.nodes[0]?.status).toBe('recorded');
    expect(graph.nodes[0]?.events?.map((event) => event.verdict)).toEqual(['changes', 'pass']);
    expect(graph.nodes[1]?.status).toBe('unobserved');
    expect(graph.nodes[1]?.childWorkflows?.[0]?.detail).toContain('historical');
    expect(graph.edges).toEqual(traceFixture.workflow.graph.edges);
  });
  it('keeps ambiguous and missing-node outcomes unattached', () => {
    const graph = projectDeveloperExecutionTrace([
      {
        ...traceFixture,
        events: [
          { ...traceEvent, nodeIds: ['review', 'workers'] },
          { ...traceEvent, eventId: 'missing', nodeIds: ['unknown'] },
        ],
      },
    ]);
    expect(graph.unattached).toHaveLength(2);
    expect(graph.nodes.every((node) => node.status === 'unobserved')).toBe(true);
  });
  it('isolates session histories and does not put parent siblings inside a child graph', () => {
    const graph = projectDeveloperExecutionTrace([
      traceFixture,
      { ...traceFixture, selectedSessionId: 'child', selectedChildId: 'c1', events: [] },
    ]);
    expect(graph.nodes.every((node) => node.status === 'unobserved')).toBe(true);
    expect(graph.nodes.every((node) => node.childWorkflows?.length === 0)).toBe(true);
    expect(projectDeveloperExecutionTrace([])).toEqual({ nodes: [], edges: [], unattached: [] });
  });
  it('does not guess child membership when multiple expansion nodes exist', () => {
    const graph = projectDeveloperExecutionTrace([
      {
        ...traceFixture,
        workflow: {
          ...traceFixture.workflow,
          graph: {
            ...traceFixture.workflow.graph,
            nodes: [
              ...traceFixture.workflow.graph.nodes,
              { id: 'more', label: 'More', kind: 'subworkflow' },
            ],
          },
        },
        sessions: [{ ...traceFixture.sessions[1]!, parentNodeId: undefined }],
      },
    ]);
    expect(graph.nodes.every((node) => node.childWorkflows?.length === 0)).toBe(true);
  });
});
