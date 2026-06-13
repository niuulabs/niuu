/**
 * HealthPage — one surface for "is this knowledge base healthy?".
 *
 * Composes the doctor checklist (D01–D08 self-checks with safe auto-fixes)
 * on top of the lint detail (L01–L12 issue list with assignment and
 * per-issue fixes). Doctor's lint-summary check (D06) is the headline;
 * the lint section below is where the individual issues get worked.
 */

import { DoctorPage } from './DoctorPage';
import { LintPage } from './LintPage';

export function HealthPage() {
  return (
    <div data-testid="health-page">
      <DoctorPage />
      <div className="niuu:border-t niuu:border-border niuu:mt-2" />
      <LintPage />
    </div>
  );
}
