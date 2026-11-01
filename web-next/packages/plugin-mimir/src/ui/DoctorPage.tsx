/**
 * DoctorPage — instance health checklist with scored summary.
 *
 * Header: score + worst-status badge + "Run fixes" (behind a confirm dialog).
 * Body: dense check rows with semantic pass/warn/fail badges, observed detail
 * and remediation hint. Optional backend capability — renders an empty state
 * when the backend has no doctor subsystem.
 */

import { useState } from 'react';
import { StateDot, Dialog, DialogContent, DialogClose } from '@niuulabs/ui';
import { useDoctor } from '../application/useDoctor';
import type { DoctorCheck, DoctorStatus } from '../domain/doctor';
import { fixableChecks } from '../domain/doctor';
import './mimir-views.css';

const STATUS_ROW = 'niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-secondary';

const BTN_PRIMARY =
  'niuu:py-1 niuu:px-3 niuu:rounded-md niuu:font-sans niuu:text-xs niuu:cursor-pointer niuu:whitespace-nowrap ' +
  'niuu:border niuu:bg-brand niuu:border-brand niuu:text-bg-primary niuu:font-medium niuu:hover:opacity-[0.88] ' +
  'niuu:disabled:opacity-50 niuu:disabled:cursor-not-allowed';

const BTN_GHOST =
  'niuu:bg-transparent niuu:border niuu:border-solid niuu:border-border-subtle niuu:rounded-md ' +
  'niuu:text-text-secondary niuu:font-sans niuu:text-xs niuu:py-1 niuu:px-3 niuu:cursor-pointer ' +
  'niuu:hover:border-border niuu:hover:text-text-primary';

const STATUS_CHIP: Record<DoctorStatus, string> = {
  pass: 'mm-chip ok',
  warn: 'mm-chip warn',
  fail: 'mm-chip err',
};

// ── Check row ────────────────────────────────────────────────────────────────

interface CheckRowProps {
  check: DoctorCheck;
}

function CheckRow({ check }: CheckRowProps) {
  return (
    <li
      className="niuu:flex niuu:items-start niuu:gap-3 niuu:py-3 niuu:px-4 niuu:border-0 niuu:border-b niuu:border-solid niuu:border-b-border-subtle niuu:last:border-b-0"
      data-testid="doctor-check"
    >
      <span className={STATUS_CHIP[check.status]} data-testid={`status-${check.status}`}>
        {check.status.toUpperCase()}
      </span>
      <div className="niuu:min-w-0 niuu:flex-1">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
            {check.title}
          </span>
          <span className="niuu:font-mono niuu:text-[10px] niuu:text-text-faint">{check.id}</span>
          {check.fixable && (
            <span className="niuu:font-mono niuu:text-[10px] niuu:uppercase niuu:text-text-muted">
              fixable
            </span>
          )}
        </div>
        <div className="niuu:text-xs niuu:text-text-secondary niuu:mt-0.5 niuu:font-mono">
          {check.detail}
        </div>
        {check.status !== 'pass' && check.remediation && (
          <div className="niuu:text-xs niuu:text-text-muted niuu:mt-0.5">
            remediation: {check.remediation}
          </div>
        )}
      </div>
    </li>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function DoctorPage() {
  const { report, isLoading, isError, error, runFixes, isFixing } = useDoctor();
  const [confirmOpen, setConfirmOpen] = useState(false);

  if (isLoading) {
    return (
      <div className={`${STATUS_ROW} niuu:p-6`}>
        <StateDot state="processing" pulse />
        <span>running doctor checks…</span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className={`${STATUS_ROW} niuu:p-6`}>
        <StateDot state="failed" />
        <span>{error instanceof Error ? error.message : 'doctor load failed'}</span>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="niuu:p-6">
        <h2 className="niuu:text-xl niuu:font-semibold niuu:m-0 niuu:mb-2">Doctor</h2>
        <p className="niuu:text-sm niuu:text-text-muted niuu:m-0" data-testid="doctor-empty">
          No doctor report — this backend does not expose health checks.
        </p>
      </div>
    );
  }

  const fixable = fixableChecks(report);

  return (
    <div className="niuu:p-6 niuu:max-w-[960px] niuu:flex niuu:flex-col niuu:gap-4">
      <div className="niuu:flex niuu:items-center niuu:gap-3 niuu:flex-wrap">
        <h2 className="niuu:text-xl niuu:font-semibold niuu:m-0">Doctor</h2>
        <span
          className="niuu:font-mono niuu:text-sm niuu:text-text-primary"
          data-testid="doctor-score"
        >
          {report.score}
        </span>
        <span className={STATUS_CHIP[report.worst]} data-testid="doctor-worst">
          {report.worst.toUpperCase()}
        </span>
        {fixable.length > 0 && (
          <button
            type="button"
            className={`${BTN_PRIMARY} niuu:ml-auto`}
            onClick={() => setConfirmOpen(true)}
            disabled={isFixing}
            data-testid="run-fixes-btn"
          >
            {isFixing ? 'fixing…' : `Run fixes (${fixable.length})`}
          </button>
        )}
      </div>

      <p className="niuu:text-sm niuu:text-text-secondary niuu:m-0">
        Self-checks for this Mímir instance — indexes, embeddings, sources, and mounts.
      </p>

      <ul
        className="niuu:list-none niuu:p-0 niuu:m-0 niuu:border niuu:border-solid niuu:border-border-subtle niuu:rounded-md niuu:overflow-hidden niuu:bg-bg-secondary"
        aria-label="Doctor checks"
      >
        {report.checks.map((check) => (
          <CheckRow key={check.id} check={check} />
        ))}
      </ul>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent
          title="Run automatic fixes?"
          description={`${fixable.length} check${fixable.length === 1 ? '' : 's'} can be remediated automatically. This writes to the knowledge base.`}
        >
          <div className="niuu:flex niuu:justify-end niuu:gap-2 niuu:mt-2">
            <DialogClose asChild>
              <button type="button" className={BTN_GHOST} data-testid="confirm-cancel">
                Cancel
              </button>
            </DialogClose>
            <button
              type="button"
              className={BTN_PRIMARY}
              onClick={() => {
                runFixes();
                setConfirmOpen(false);
              }}
              data-testid="confirm-fix"
            >
              Run fixes
            </button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
