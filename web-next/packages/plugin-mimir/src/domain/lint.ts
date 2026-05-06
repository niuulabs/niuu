/**
 * Mímir lint domain — issue catalog and dream-cycle records.
 *
 * The lint engine runs rules L01–L12 across all mounts and surfaces issues
 * for human review or automated fixing. Dream cycles are the idle-time
 * synthesis passes that update pages, create entities, and apply lint fixes.
 */

// ---------------------------------------------------------------------------
// Lint rules and issues
// ---------------------------------------------------------------------------

/** Canonical lint rule identifiers. */
export type LintRule =
  | 'L01' // Contradiction between pages
  | 'L02' // Stale source (source updated, page not recompiled)
  | 'L05' // Broken wikilink
  | 'L07' // Orphan page (no inbound links)
  | 'L11' // Stale index (mount index out of sync)
  | 'L12'; // Invalid frontmatter

export type IssueSeverity = 'info' | 'warn' | 'error';

export interface LintIssue {
  id: string;
  rule: LintRule;
  severity: IssueSeverity;
  /** Path of the page with the issue. */
  page: string;
  /** Name of the mount the page lives on. */
  mount: string;
  /** Ravn assigned to resolve this issue. */
  assignee?: string;
  /** Whether the engine can apply a fix automatically. */
  autoFix: boolean;
  message: string;
}

export interface LintReport {
  issues: LintIssue[];
  /** Number of pages scanned during this lint run. */
  pagesChecked: number;
  summary: { error: number; warn: number; info: number };
}

// ---------------------------------------------------------------------------
// Activity events
// ---------------------------------------------------------------------------

export type ActivityEventKind = 'write' | 'ingest' | 'lint' | 'dream' | 'query';

export interface ActivityEvent {
  id: string;
  /** ISO-8601 timestamp. */
  timestamp: string;
  kind: ActivityEventKind;
  mount: string;
  /** Ravn that performed the action. */
  ravn: string;
  /** Human-readable event message. */
  message: string;
  /** Page path, if the event relates to a specific page. */
  page?: string;
}

// ---------------------------------------------------------------------------
// Dream cycles
// ---------------------------------------------------------------------------

export interface DreamCycle {
  id: string;
  /** ISO-8601 timestamp when the cycle ran. */
  timestamp: string;
  /** ID of the ravn that ran the cycle. */
  ravn: string;
  /** Names of the mounts touched during the cycle. */
  mounts: string[];
  pagesUpdated: number;
  entitiesCreated: number;
  lintFixes: number;
  /** Wall-clock duration of the cycle in milliseconds. */
  durationMs: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Returns true if the issue can be auto-fixed by the lint engine. */
export function isAutoFixable(issue: LintIssue): boolean {
  return issue.autoFix;
}

/** Tally issues by severity. */
export function tallySeverity(issues: LintIssue[]): { error: number; warn: number; info: number } {
  return {
    error: issues.filter((i) => i.severity === 'error').length,
    warn: issues.filter((i) => i.severity === 'warn').length,
    info: issues.filter((i) => i.severity === 'info').length,
  };
}
