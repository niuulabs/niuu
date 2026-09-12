import { formatGib, formatGpu, type SystemCheck, type SystemReport } from '../domain/setup';
import { AlertIcon, CheckIcon, CrossIcon } from './icons';

export interface SystemStepProps {
  report: SystemReport | undefined;
  loading: boolean;
  error: Error | null;
  onRerun: () => void;
}

function tone(check: SystemCheck): 'ok' | 'warn' | 'fail' {
  if (check.passed) return 'ok';
  return check.warnOnly ? 'warn' : 'fail';
}

function CheckRow({ check }: { check: SystemCheck }) {
  const kind = tone(check);
  return (
    <div className="setup-row" data-testid={`setup-check-${check.name.replace(/\s+/g, '-')}`}>
      <span className={`setup-row__icon setup-row__icon--${kind}`}>
        {kind === 'ok' ? <CheckIcon /> : kind === 'warn' ? <AlertIcon /> : <CrossIcon />}
      </span>
      <div className="setup-row__body">
        <span className="setup-row__title">{check.name}</span>
        <span className="setup-row__detail">{check.message}</span>
      </div>
    </div>
  );
}

export function SystemStep({ report, loading, error, onRerun }: SystemStepProps) {
  const host = report?.host ?? null;
  const failures = report ? report.checks.filter((c) => !c.passed && !c.warnOnly).length : 0;
  const warnings = report ? report.checks.filter((c) => c.warnOnly && !c.passed).length : 0;
  return (
    <div className="setup-col" data-testid="setup-system">
      {host ? (
        <div className="setup-card">
          <div className="setup-card__head">
            <div>
              <h3 className="setup-card__title">{host.hostname}</h3>
              <p className="setup-card__desc">
                {host.os_name} {host.os_version} · {host.arch} · {host.cpu_count} CPUs ·{' '}
                {formatGib(host.memory_total_bytes)} memory
              </p>
            </div>
          </div>
          <div className="setup-chips">
            {host.gpus.map((gpu) => (
              <span key={gpu.name + gpu.driver_version} className="setup-chip setup-chip--ok">
                {formatGpu(gpu)}
              </span>
            ))}
            {host.gpus.length === 0 ? <span className="setup-chip">No NVIDIA GPU</span> : null}
            <span className="setup-chip">Docker {host.docker_version || 'unknown'}</span>
            <span className="setup-chip">
              {formatGib(host.disk_free_bytes)} free of {formatGib(host.disk_total_bytes)} on{' '}
              {host.data_dir}
            </span>
          </div>
        </div>
      ) : null}
      <div className="setup-card">
        <div className="setup-card__head">
          <div>
            <h3 className="setup-card__title">
              {loading
                ? 'Checking…'
                : report
                  ? `${report.checks.length} checks · ${failures} failed · ${warnings} warnings`
                  : 'Platform checks'}
            </h3>
            <p className="setup-card__desc">
              Checks run inside the platform: database, container runtime, tooling.
            </p>
          </div>
          <button
            type="button"
            className="setup-btn"
            onClick={onRerun}
            disabled={loading}
            data-testid="setup-system-rerun"
          >
            Re-run checks
          </button>
        </div>
        {error ? (
          <div className="setup-error" role="alert">
            Could not run checks: {error.message}
          </div>
        ) : null}
        {report ? report.checks.map((check) => <CheckRow key={check.name} check={check} />) : null}
      </div>
      {report && failures > 0 ? (
        <div className="setup-note setup-note--warn" data-testid="setup-system-blocked">
          <AlertIcon /> Fix the failed checks before continuing.
        </div>
      ) : null}
    </div>
  );
}
