import type { ReviewItem, ValkyrieEventTelemetry } from '../domain';
import { KIND_LABELS, STATUS_LABELS, statusClasses, timeAgo } from './reviewFormat';

const PANEL =
  'niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:rounded-md';
const MUTED = 'niuu:text-text-muted';

export function eventClasses(kind: ValkyrieEventTelemetry['kind']): string {
  if (kind === 'action') return 'niuu:text-state-warn';
  if (kind === 'judgment') return 'niuu:text-brand';
  if (kind === 'signal') return 'niuu:text-state-ok';
  if (kind === 'log') return 'niuu:text-text-muted';
  return 'niuu:text-text-secondary';
}

function TelemetryRow({ event }: { event: ValkyrieEventTelemetry }) {
  return (
    <li data-testid="activity-row" className={`${PANEL} niuu:p-3`}>
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-2">
          <span className={`niuu:text-xs ${eventClasses(event.kind)}`}>{event.kind}</span>
          <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">
            {event.summary || event.eventType}
          </span>
          <span
            className={`niuu:shrink-0 niuu:rounded-md niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-xs ${MUTED}`}
          >
            {event.eventType}
          </span>
        </div>
        <span className={`niuu:shrink-0 niuu:text-xs ${MUTED}`}>
          {timeAgo(event.observedAt)} ago
        </span>
      </div>
      <p className={`niuu:mt-1 niuu:text-xs ${MUTED}`}>
        {event.environmentId}
        {event.valkyrieId ? ` · ${event.valkyrieId}` : ''}
        {event.source ? ` · ${event.source}` : ''}
        {event.correlationId ? ` · corr ${event.correlationId}` : ''}
      </p>
    </li>
  );
}

/**
 * The raw flat event tail (plus settled reviews) that used to be the whole
 * activity page. Kept behind the "debug view" toggle — still the fastest way
 * to see exactly what telemetry reached the dashboard.
 */
export function ActivityDebugList({
  events,
  settled,
}: {
  events: ValkyrieEventTelemetry[];
  settled: ReviewItem[];
}) {
  return (
    <ol
      data-testid="activity-debug-list"
      className="niuu:flex niuu:min-h-0 niuu:flex-1 niuu:flex-col niuu:gap-2 niuu:overflow-auto"
    >
      {events.map((event) => (
        <TelemetryRow key={event.id} event={event} />
      ))}
      {settled.map((item) => (
        <li key={item.itemId} data-testid="activity-row" className={`${PANEL} niuu:p-3`}>
          <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
            <div className="niuu:flex niuu:items-center niuu:gap-2">
              <span className={`niuu:text-xs ${statusClasses(item.status)}`}>
                {STATUS_LABELS[item.status]}
              </span>
              <span className="niuu:text-sm niuu:text-text-primary">{item.title}</span>
              <span
                className={`niuu:rounded-md niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-xs ${MUTED}`}
              >
                {KIND_LABELS[item.kind]}
              </span>
            </div>
            <span className={`niuu:text-xs ${MUTED}`}>
              {timeAgo(item.decidedAt || item.resolvedAt || item.requestedAt)} ago
            </span>
          </div>
          <p className={`niuu:mt-1 niuu:text-xs ${MUTED}`}>
            {item.environmentId} · decided by {item.decidedBy || 'operator'}
            {item.decisionReason ? ` — ${item.decisionReason}` : ''}
            {item.applyDetail ? ` · ${item.applyDetail}` : ''}
          </p>
        </li>
      ))}
    </ol>
  );
}
