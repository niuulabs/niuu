/** Public, cursor-paged outcomes associated with a run's frozen workflow. */
export interface DeveloperTraceSession {
  sessionId: string | null;
  kind: 'parent' | 'child';
  childId?: string;
  childKey?: string;
  generation?: number;
  attempt?: number;
  taskId?: string | null;
  state?: string;
  parentNodeId?: string | null;
}

export interface DeveloperTraceEvent {
  eventId: string;
  sessionId: string;
  seq: number;
  occurredAt: string;
  eventType: string;
  canonicalEventType?: string;
  persona: string;
  summary: string;
  verdict: string;
  valid: boolean;
  taskId?: string;
  sourceEventId?: string;
  fields: Record<string, unknown>;
  nodeIds: string[];
  mapping: string;
  candidateNodeIds?: string[];
}

export interface DeveloperExecutionTrace {
  schemaVersion: number;
  execution: {
    executionId: string;
    state: string;
    currentGeneration: number;
    parentSessionId: string | null;
  };
  workflow: {
    workflowId: string;
    revision: string;
    digest: string;
    name: string;
    graph: {
      nodes: {
        id: string;
        label: string;
        kind: string;
        position?: { x: number; y: number };
      }[];
      edges: { id: string; source: string; target: string; label?: string }[];
    };
  };
  sessions: DeveloperTraceSession[];
  selectedSessionId: string | null;
  selectedChildId: string | null;
  page: { after: number; scannedThrough: number; hasMore: boolean };
  events: DeveloperTraceEvent[];
  nodeHistory: Record<string, string[]>;
  unattachedEventIds: string[];
}

export interface DeveloperTraceOptions {
  childId?: string;
  after?: number;
  limit?: number;
}
