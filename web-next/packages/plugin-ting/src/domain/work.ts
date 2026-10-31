/** Read projections over existing Ting records; commands remain on their owning ports. */
export type WorkResourceKind = 'project' | 'campaign' | 'execution';

export interface TrackerProjectRef {
  connectionId: string;
  provider: string;
  projectId: string;
  url: string;
  status: string;
}

export interface SourceCoverage {
  source: string;
  connectionId: string | null;
  status: 'complete' | 'partial' | 'unavailable';
  error: { code: string; message: string } | null;
}

export interface WorkSummary {
  id: `${WorkResourceKind}:${string}`;
  kind:
    'project' | 'research' | 'specification' | 'planning' | 'workflowLaunch' | 'workflowExecution';
  title: string;
  campaignSlug?: string | null;
  condition: 'needsAttention' | 'active' | 'waiting' | 'completed' | 'failed' | 'unknown';
  rawState: { source: string; status: string };
  currentStep: string | null;
  attention: { kind: string; message: string } | null;
  createdAt: string | null;
  updatedAt: string | null;
  source: TrackerProjectRef | null;
  workflow: { id: string; version: string; documentRevision: string | null } | null;
  session: { id: string; connectionId: string | null } | null;
  links: { self: string; specialist: string; source?: string | null };
  actions: string[];
}

export interface BoardGate {
  id: string;
  activationId: string;
  nodeId: string;
  label: string;
  condition: string;
  instructions: string;
  summary: string;
  requestedAt: string;
  attempt: number;
}

export type BoardTicketState =
  | 'notStarted'
  | 'ready'
  | 'starting'
  | 'running'
  | 'needsDecision'
  | 'blocked'
  | 'done'
  | 'unknown';

/** The last answer a person gave at a gate on this ticket's run. */
export interface BoardDecision {
  label: string;
  status: string;
  decidedBy: string;
  notes: string;
  decidedAt: string;
}

export interface BoardPlacement {
  columnId: string | null;
  state: BoardTicketState;
  gate: BoardGate | null;
  reason?: string | null;
  /** Workflows this ticket has finished, so a drag can tell what it would skip. */
  passedWorkflowIds?: string[];
  lastDecision?: BoardDecision | null;
}

/** The persistent session a ticket works through for as long as it is open. */
export interface TicketCoordinatorRef {
  sessionId: string;
  status: 'active' | 'closed';
  connectionId: string | null;
  chatEndpoint: string | null;
  startError?: string | null;
}

export interface TicketWorkflowRun {
  taskId: string;
  workflowName: string;
  status: string;
  sessionId: string | null;
  currentStep?: string | null;
  prUrl?: string | null;
}

/** One thing a person reviews at a gate, as the workflow declares it. */
export interface ReviewPanel {
  widget: string;
  title: string;
  field?: string | null;
}

export interface WorkBoardColumn {
  id: string;
  title: string;
  kind: 'tracker' | 'work' | 'gate';
  review: 'plan' | 'code' | 'output' | null;
  panels?: ReviewPanel[];
  approveColumnId: string | null;
  changesColumnId: string | null;
  /** The workflow that declares this column; the tracker's own columns have none. */
  workflowId?: string | null;
  workflowName?: string | null;
}

/** Read from the project's workflows; the board never defines a column or a gate itself. */
export interface WorkBoardLayout {
  columns: WorkBoardColumn[];
}

export interface GateDecision {
  gateId: string;
  activationId: string;
  decision: 'approve' | 'request_changes';
  notes: string;
}

export interface WorkTask {
  source: TrackerProjectRef & {
    issueId: string;
    identifier: string;
    statusType: string;
  };
  title: string;
  description?: string;
  operationalRun: {
    id: string;
    status: string;
    sessionId: string | null;
    reviewerSessionId: string | null;
    branch: string | null;
    prUrl: string | null;
    updatedAt: string | null;
  } | null;
  dispatch: {
    sagaId: string;
    issueId: string;
    repo: string;
    connectionId: string | null;
    workflowId: string | null;
  } | null;
  actions: string[];
  board?: BoardPlacement | null;
  coordinator?: TicketCoordinatorRef | null;
  workflowRun?: TicketWorkflowRun | null;
}

/** Only the execution section is cursor-paged; do not infer global totals from it. */
export interface WorkCollection {
  projects: WorkSummary[];
  campaigns: WorkSummary[];
  executions: WorkSummary[];
  executionNextCursor: string | null;
  coverage: SourceCoverage[];
}

export interface WorkDetail {
  item: WorkSummary;
  tasks: WorkTask[];
  coverage: SourceCoverage[];
  board?: WorkBoardLayout | null;
}
