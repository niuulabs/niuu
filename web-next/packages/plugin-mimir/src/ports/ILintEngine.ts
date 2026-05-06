import type { LintReport, DreamCycle, ActivityEvent } from '../domain/lint';

/**
 * Port: ILintEngine
 *
 * Runs lint rules (L01–L12) across mounted pages, surfaces issues, and
 * tracks dream-cycle history.
 */
export interface ILintEngine {
  /**
   * Return the current lint report for one or all mounts.
   *
   * @param mountName Scope to a specific mount; omit for fleet-wide.
   */
  getLintReport(mountName?: string): Promise<LintReport>;

  /**
   * Apply auto-fixes for the given issue IDs (or all auto-fixable issues
   * when omitted) and return the updated lint report.
   */
  runAutoFix(issueIds?: string[]): Promise<LintReport>;

  /**
   * Assign one or more issues to a ravn and return the updated lint report.
   *
   * @param issueIds IDs of the issues to reassign.
   * @param assignee Ravn ID to assign the issues to.
   */
  reassignIssues(issueIds: string[], assignee: string): Promise<LintReport>;

  /**
   * Fetch recent dream-cycle run records, most-recent-first.
   *
   * @param limit Maximum records to return (default: 20).
   */
  getDreamCycles(limit?: number): Promise<DreamCycle[]>;

  /**
   * Fetch recent activity events across all mounts, most-recent-first.
   *
   * @param limit Maximum records to return (default: 50).
   */
  getActivityLog(limit?: number): Promise<ActivityEvent[]>;
}
