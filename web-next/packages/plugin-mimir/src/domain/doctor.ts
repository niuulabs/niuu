/**
 * Mímir doctor domain — instance health checklist.
 *
 * The doctor runs a battery of self-checks (index freshness, embedding
 * coverage, orphaned sources, …) and reports a scored checklist. Optional
 * capability: a backend without the doctor subsystem returns null.
 */

export type DoctorStatus = 'pass' | 'warn' | 'fail';

/** One self-check result. */
export interface DoctorCheck {
  id: string;
  title: string;
  status: DoctorStatus;
  /** What the check observed. */
  detail: string;
  /** How to fix it when not passing. */
  remediation: string;
  /** Whether `POST /doctor/fix` can remediate this check automatically. */
  fixable: boolean;
}

/** Full doctor report. */
export interface DoctorReport {
  /** Display score, e.g. "9/12". */
  score: string;
  /** Worst status across all checks. */
  worst: DoctorStatus;
  checks: DoctorCheck[];
}

/** Checks that are fixable and not currently passing. */
export function fixableChecks(report: DoctorReport): DoctorCheck[] {
  return report.checks.filter((check) => check.fixable && check.status !== 'pass');
}
