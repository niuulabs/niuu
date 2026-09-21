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

export interface WorkTask {
  source: TrackerProjectRef & {
    issueId: string;
    identifier: string;
    statusType: string;
  };
  title: string;
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
}
