/**
 * Mimir HTTP-level domain types.
 *
 * Copied from web/src/modules/mimir/api/types.ts — these match the existing
 * Mimir HTTP API wire format (snake_case → camelCase mapping done in api/client.ts).
 */

// ---------------------------------------------------------------------------
// Pages & knowledge base
// ---------------------------------------------------------------------------

export interface MimirPageMeta {
  path: string;
  title: string;
  summary: string;
  category: string;
  updatedAt: string;
  sourceIds: string[];
}

export interface MimirPage extends MimirPageMeta {
  content: string;
}

export interface MimirStats {
  pageCount: number;
  categories: string[];
  healthy: boolean;
}

export interface MimirSearchResult {
  path: string;
  title: string;
  summary: string;
  category: string;
}

export type LintSeverity = 'error' | 'warning' | 'info';

export interface LintIssueHttp {
  id: string;
  severity: LintSeverity;
  message: string;
  pagePath: string;
  autoFixable: boolean;
}

export interface MimirLintReport {
  issues: LintIssueHttp[];
  pagesChecked: number;
  issuesFound: boolean;
  summary: { error: number; warning: number; info: number };
}

export interface MimirLogEntry {
  raw: string;
  entries: string[];
}

// ---------------------------------------------------------------------------
// Knowledge graph
// ---------------------------------------------------------------------------

export interface GraphNode {
  id: string;
  title: string;
  category: string;
  /** Number of inbound edges -- set during graph processing. */
  inboundCount?: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  type?: string;
}

export interface MimirGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// ---------------------------------------------------------------------------
// Ingest
// ---------------------------------------------------------------------------

export type IngestStatus = 'queued' | 'running' | 'complete' | 'failed';

export type IngestSourceType = 'document' | 'web' | 'conversation' | 'text';

export interface IngestJob {
  id: string;
  title: string;
  sourceType: IngestSourceType;
  status: IngestStatus;
  instanceName: string;
  /** Pages written during run. */
  pagesUpdated: string[];
  /** Current activity message when running. */
  currentActivity?: string;
  startedAt?: string;
  completedAt?: string;
  errorMessage?: string;
}

export interface IngestRequest {
  title: string;
  content: string;
  sourceType: IngestSourceType;
  originUrl?: string;
}

export interface IngestResponse {
  sourceId: string;
  pagesUpdated: string[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Transition the job to the next state. */
export function transitionJob(job: IngestJob, update: Partial<IngestJob>): IngestJob {
  return { ...job, ...update };
}

/** Returns true if the job is in a terminal state. */
export function isTerminal(status: IngestStatus): boolean {
  return status === 'complete' || status === 'failed';
}
