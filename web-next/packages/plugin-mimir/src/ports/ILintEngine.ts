import type { LintReport, ActivityEvent } from '../domain/lint';

/**
 * Port: ILintEngine
 *
 * Runs lint rules (L01–L12) across mounted pages and surfaces issues.
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
   * Fetch recent activity events across all mounts, most-recent-first.
   *
   * @param limit Maximum records to return (default: 50).
   */
  getActivityLog(limit?: number): Promise<ActivityEvent[]>;
}
