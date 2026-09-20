import type { WorkflowExecutionTrace, ExecutionTraceEvent } from '../domain/workflowExecutionTrace';
import type {
  WorkflowExecutionNode,
  WorkflowExecutionEdge,
  WorkflowExecutionOutput,
} from '../domain/workflowExecutionGraph';

/** Projection of observed history only; an outcome is not a lifecycle receipt. */
export function projectWorkflowExecutionTrace(pages: readonly WorkflowExecutionTrace[]): {
  nodes: WorkflowExecutionNode[];
  edges: WorkflowExecutionEdge[];
  unattached: ExecutionTraceEvent[];
} {
  const latest = pages.at(-1);
  if (!latest) return { nodes: [], edges: [], unattached: [] };
  const sameSession = pages.filter((page) => page.selectedSessionId === latest.selectedSessionId);
  const events = [
    ...new Map(
      sameSession.flatMap((page) => page.events).map((event) => [event.eventId, event]),
    ).values(),
  ].sort((a, b) => a.seq - b.seq);
  const isParent = !latest.selectedChildId;
  const fanouts = latest.workflow.graph.nodes.filter((node) => node.kind === 'subworkflow');
  const evidence = [{ id: 'run-evidence', label: 'Open run evidence' }];
  const nodes: WorkflowExecutionNode[] = latest.workflow.graph.nodes.map((node) => {
    const history = events.filter(
      (event) => event.nodeIds.length === 1 && event.nodeIds[0] === node.id,
    );
    const children =
      isParent && node.kind === 'subworkflow'
        ? latest.sessions.filter(
            (session) =>
              session.kind === 'child' &&
              session.childId &&
              (session.parentNodeId === node.id || (!session.parentNodeId && fanouts.length === 1)),
          )
        : [];
    return {
      id: node.id,
      label: node.label,
      kind: node.kind,
      position: node.position,
      status: history.length ? 'recorded' : 'unobserved',
      detail: history.length
        ? `${history.length} public outcome${history.length === 1 ? '' : 's'}`
        : 'No stage outcome in loaded history',
      outputs: history.flatMap((event): WorkflowExecutionOutput[] => [
        ...(typeof event.fields.plan === 'string'
          ? [
              {
                id: `${event.eventId}:plan`,
                label: `#${event.seq} · Plan`,
                format: 'markdown' as const,
                value: event.fields.plan,
                isPublic: true,
                evidence,
              },
            ]
          : []),
        {
          id: event.eventId,
          label: `#${event.seq} · ${event.verdict || event.eventType}`,
          format: 'structured',
          value: event.fields,
          isPublic: true,
          evidence,
        },
      ]),
      events: history.map((event) => ({
        id: event.eventId,
        eventType: event.eventType,
        persona: event.persona,
        verdict: event.verdict,
        timestamp: event.occurredAt,
        summary: event.summary || `${event.persona} · ${event.verdict || event.eventType}`,
        fields: {
          ...event.fields,
          traceMapping: event.mapping,
          ...(event.canonicalEventType ? { canonicalEventType: event.canonicalEventType } : {}),
          valid: event.valid,
          sequence: event.seq,
        },
        evidence,
      })),
      childWorkflows: children.map((session) => ({
        id: session.childId!,
        label: `${session.childKey} · generation ${session.generation} · attempt ${session.attempt}`,
        detail: `${session.state}${session.generation !== latest.execution.currentGeneration ? ' · historical generation' : ''}`,
      })),
      evidence,
    };
  });
  const ids = new Set(nodes.map((node) => node.id));
  return {
    nodes,
    edges: latest.workflow.graph.edges,
    unattached: events.filter((event) => event.nodeIds.length !== 1 || !ids.has(event.nodeIds[0]!)),
  };
}
