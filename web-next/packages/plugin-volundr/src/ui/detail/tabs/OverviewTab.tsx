import { LifecycleBadge } from '@niuulabs/ui';
import { toLifecycleState } from '../../utils/toLifecycleState';
import type { Session } from '../../../domain/session';

interface OverviewTabProps {
  session: Session;
}

function ResourceRow({
  label,
  used,
  limit,
  unit,
}: {
  label: string;
  used: number;
  limit: number;
  unit: string;
}) {
  const pct = limit > 0 ? Math.min(1, used / limit) : 0;
  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-1">
      <div className="niuu-flex niuu-items-center niuu-justify-between">
        <span className="niuu-text-xs niuu-text-text-secondary">{label}</span>
        <span className="niuu-font-mono niuu-text-xs niuu-text-text-primary">
          {used.toFixed(1)} / {limit} {unit}
        </span>
      </div>
      <div
        className="niuu-h-1.5 niuu-rounded-full niuu-bg-bg-elevated"
        role="progressbar"
        aria-valuenow={used}
        aria-valuemax={limit}
        aria-label={`${label} usage`}
      >
        <div
          className="niuu-h-full niuu-rounded-full niuu-bg-brand"
          style={{ width: `${(pct * 100).toFixed(1)}%` }}
        />
      </div>
    </div>
  );
}

/** Overview tab — session metadata + resource utilisation bars. */
export function OverviewTab({ session }: OverviewTabProps) {
  const { resources } = session;

  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-6 niuu-p-4" data-testid="overview-tab">
      {/* Identity */}
      <section className="niuu-flex niuu-flex-col niuu-gap-3">
        <h3 className="niuu-text-sm niuu-font-medium niuu-text-text-secondary">Identity</h3>
        <dl className="niuu-grid niuu-grid-cols-2 niuu-gap-x-6 niuu-gap-y-2 niuu-text-sm">
          <dt className="niuu-text-text-muted">Session ID</dt>
          <dd className="niuu-font-mono niuu-text-text-primary">{session.id}</dd>
          <dt className="niuu-text-text-muted">Persona</dt>
          <dd className="niuu-text-text-primary">{session.personaName}</dd>
          <dt className="niuu-text-text-muted">Ravn</dt>
          <dd className="niuu-font-mono niuu-text-text-primary">{session.ravnId}</dd>
          <dt className="niuu-text-text-muted">Cluster</dt>
          <dd className="niuu-font-mono niuu-text-text-primary">{session.clusterId}</dd>
          <dt className="niuu-text-text-muted">Template</dt>
          <dd className="niuu-font-mono niuu-text-text-primary">{session.templateId}</dd>
          <dt className="niuu-text-text-muted">State</dt>
          <dd>
            <LifecycleBadge state={toLifecycleState(session.state)} />
          </dd>
        </dl>
      </section>

      {/* Timings */}
      <section className="niuu-flex niuu-flex-col niuu-gap-3">
        <h3 className="niuu-text-sm niuu-font-medium niuu-text-text-secondary">Timings</h3>
        <dl className="niuu-grid niuu-grid-cols-2 niuu-gap-x-6 niuu-gap-y-2 niuu-text-sm">
          <dt className="niuu-text-text-muted">Started</dt>
          <dd className="niuu-font-mono niuu-text-text-primary">
            {new Date(session.startedAt).toLocaleString()}
          </dd>
          {session.readyAt && (
            <>
              <dt className="niuu-text-text-muted">Ready</dt>
              <dd className="niuu-font-mono niuu-text-text-primary">
                {new Date(session.readyAt).toLocaleString()}
              </dd>
            </>
          )}
          {session.lastActivityAt && (
            <>
              <dt className="niuu-text-text-muted">Last activity</dt>
              <dd className="niuu-font-mono niuu-text-text-primary">
                {new Date(session.lastActivityAt).toLocaleString()}
              </dd>
            </>
          )}
          {session.terminatedAt && (
            <>
              <dt className="niuu-text-text-muted">Terminated</dt>
              <dd className="niuu-font-mono niuu-text-text-primary">
                {new Date(session.terminatedAt).toLocaleString()}
              </dd>
            </>
          )}
        </dl>
      </section>

      {/* Resources */}
      <section className="niuu-flex niuu-flex-col niuu-gap-3">
        <h3 className="niuu-text-sm niuu-font-medium niuu-text-text-secondary">Resources</h3>
        <div className="niuu-flex niuu-flex-col niuu-gap-3">
          <ResourceRow
            label="CPU"
            used={resources.cpuUsed}
            limit={resources.cpuLimit}
            unit="cores"
          />
          <ResourceRow
            label="Memory"
            used={resources.memUsedMi}
            limit={resources.memLimitMi}
            unit="Mi"
          />
          {resources.gpuCount > 0 && (
            <div className="niuu-flex niuu-items-center niuu-justify-between niuu-text-sm">
              <span className="niuu-text-text-muted">GPU</span>
              <span className="niuu-font-mono niuu-text-text-primary">
                {resources.gpuCount} allocated
              </span>
            </div>
          )}
        </div>
      </section>

      {/* Environment */}
      {Object.keys(session.env).length > 0 && (
        <section className="niuu-flex niuu-flex-col niuu-gap-3">
          <h3 className="niuu-text-sm niuu-font-medium niuu-text-text-secondary">Environment</h3>
          <ul className="niuu-flex niuu-flex-col niuu-gap-1">
            {Object.entries(session.env).map(([k, v]) => (
              <li key={k} className="niuu-font-mono niuu-text-xs niuu-text-text-secondary">
                <span className="niuu-text-brand">{k}</span>=
                <span className="niuu-text-text-primary">{v}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
