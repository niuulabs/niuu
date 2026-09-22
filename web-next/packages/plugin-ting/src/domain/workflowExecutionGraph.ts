/** Generic, provider-neutral projection consumed by the workflow execution graph. */
export type WorkflowExecutionStatus =
  'unobserved' | 'recorded' | 'running' | 'completed' | 'failed' | 'waiting';

export interface WorkflowExecutionPosition {
  x: number;
  y: number;
}

export interface WorkflowExecutionEvidence {
  id: string;
  label: string;
  kind?: string;
  href?: string;
}

export interface WorkflowExecutionMarkdownOutput {
  id: string;
  label: string;
  format: 'markdown';
  value: string;
  isPublic?: boolean;
  evidence?: WorkflowExecutionEvidence[];
}

export interface WorkflowExecutionStructuredOutput {
  id: string;
  label: string;
  format: 'structured';
  value: unknown;
  isPublic?: boolean;
  evidence?: WorkflowExecutionEvidence[];
}

export type WorkflowExecutionOutput =
  WorkflowExecutionMarkdownOutput | WorkflowExecutionStructuredOutput;

export interface WorkflowExecutionEvent {
  id: string;
  eventType: string;
  persona?: string;
  verdict?: string;
  timestamp: string;
  summary: string;
  fields?: Record<string, unknown>;
  evidence?: WorkflowExecutionEvidence[];
}

export interface WorkflowExecutionChild {
  id: string;
  label: string;
  status?: WorkflowExecutionStatus;
  detail?: string;
}

export interface WorkflowExecutionNode {
  id: string;
  label: string;
  kind: string;
  status: WorkflowExecutionStatus;
  position?: WorkflowExecutionPosition;
  detail?: string;
  outputs?: WorkflowExecutionOutput[];
  events?: WorkflowExecutionEvent[];
  childWorkflows?: WorkflowExecutionChild[];
  evidence?: WorkflowExecutionEvidence[];
}

export interface WorkflowExecutionEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  status?: WorkflowExecutionStatus;
}

export interface WorkflowExecutionEvidenceContext {
  nodeId: string;
  eventId?: string;
  outputId?: string;
}
