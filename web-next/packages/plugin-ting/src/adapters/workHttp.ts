import type { ApiClient } from '@niuulabs/query';
import type { IWorkService } from '../ports';
import type { ReviewList, ReviewWidget, RunReviews, TicketReviews } from '../domain/review';
import type { WorkCollection, WorkDetail } from '../domain/work';
import type { RunGraph, RunGraphNodeState } from '../domain/runGraph';
import type { WorkflowEdge, WorkflowNode } from '../domain/workflow';
import { placeWorkflowGraph } from '../domain/workflowCanvas';

interface RawRunGraph {
  workflowName: string;
  graph: { nodes?: WorkflowNode[]; edges?: WorkflowEdge[] };
  nodes: RunGraphNodeState[];
}

/** The run's snapshot graph placed as the builder places a workflow, with its node states. */
export function toRunGraph(raw: RawRunGraph): RunGraph {
  const { nodes, edges } = placeWorkflowGraph(raw.graph.nodes ?? [], raw.graph.edges ?? []);
  return {
    workflowName: raw.workflowName,
    nodes,
    edges,
    states: new Map(raw.nodes.map((state) => [state.nodeId, state])),
  };
}

/** The Work API already uses the public camelCase read contract. */
export function buildWorkHttpAdapter(client: ApiClient): IWorkService {
  return {
    list(options) {
      const params = new URLSearchParams();
      if (options?.executionLimit !== undefined) {
        params.set('executionLimit', String(options.executionLimit));
      }
      if (options?.executionCursor !== undefined) {
        params.set('executionCursor', options.executionCursor);
      }
      const query = params.toString();
      return client.get<WorkCollection>(`/work${query ? `?${query}` : ''}`);
    },
    get(kind, id) {
      return client.get<WorkDetail>(`/work/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`);
    },
    async decideGate(projectId, issueId, decision) {
      await client.post(
        `/work/project/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(issueId)}/gate-decision`,
        decision,
      );
    },
    async startTicket(projectId, issueId, columnId) {
      await client.post(
        `/work/project/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(issueId)}/start`,
        columnId ? { columnId } : {},
      );
    },
    reviews() {
      return client.get<ReviewList>('/work/reviews');
    },
    reviewWidget(module, name) {
      return client.get<ReviewWidget>(
        `/work/review-widgets/${encodeURIComponent(module)}/${encodeURIComponent(name)}`,
      );
    },
    ticketReviews(projectId, issueId) {
      return client.get<TicketReviews>(
        `/work/project/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(issueId)}/reviews`,
      );
    },
    run(slug) {
      return client.get<WorkDetail>(`/work/runs/${encodeURIComponent(slug)}`);
    },
    async runGraph(slug) {
      const raw = await client.get<RawRunGraph>(`/work/runs/${encodeURIComponent(slug)}/graph`);
      return toRunGraph(raw);
    },
    runReviews(slug) {
      return client.get<RunReviews>(`/work/runs/${encodeURIComponent(slug)}/reviews`);
    },
    async decideRunGate(slug, decision) {
      await client.post(`/work/runs/${encodeURIComponent(slug)}/gate-decision`, decision);
    },
  };
}
