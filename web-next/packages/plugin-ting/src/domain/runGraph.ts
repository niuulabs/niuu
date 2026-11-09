import type { WorkflowEdge, WorkflowNode } from './workflow';

/** Where a run is at one node: someone must act, at work, failed, closed, or not reached. */
export type RunNodeState = 'waiting' | 'active' | 'failed' | 'done' | 'pending';

/** One member's part in a node's newest round of work. */
export interface RunGraphMember {
  member: string;
  state: RunNodeState;
  verdict: string | null;
  sessionId: string | null;
}

export interface RunGraphNodeState {
  nodeId: string;
  state: RunNodeState;
  members: RunGraphMember[];
}

/** A run drawn as its workflow: the graph it was launched with and where it is at each node. */
export interface RunGraph {
  workflowName: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  states: ReadonlyMap<string, RunGraphNodeState>;
}

/** Whether the run still has work going on or waiting, so its states can still change. */
export function runGraphIsMoving(graph: RunGraph): boolean {
  for (const state of graph.states.values()) {
    if (state.state === 'active' || state.state === 'waiting') return true;
  }
  return false;
}
