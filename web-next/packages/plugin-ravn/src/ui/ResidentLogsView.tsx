import { ErrorState, LoadingState, cn } from '@niuulabs/ui';
import type { Ravn } from '../domain/ravn';
import { useResidentLogs } from './hooks/useResidentControl';
import './ResidentLogsView.css';

export function ResidentLogsView({
  ravn,
  enabled = true,
  fill = false,
}: {
  ravn: Ravn;
  enabled?: boolean;
  fill?: boolean;
}) {
  const logs = useResidentLogs(ravn, enabled);

  if (!enabled) return null;
  if (logs.isLoading) return <LoadingState label="Loading resident logs…" />;
  if (logs.isError) {
    return (
      <ErrorState
        message={logs.error instanceof Error ? logs.error.message : 'Failed to load resident logs'}
      />
    );
  }
  if (!logs.data?.entries.length) {
    return <div className="rv-resident-logs-view__empty">No log entries reported.</div>;
  }

  return (
    <div
      className={cn('rv-resident-logs-view', fill && 'rv-resident-logs-view--fill')}
      role="log"
      aria-label="Resident runtime logs"
      data-testid="resident-log-entries"
    >
      {logs.data.entries.map((entry, index) => (
        <div
          key={`${entry.timestampMs}:${entry.source}:${index}`}
          className="rv-resident-logs-view__row"
        >
          <time>{new Date(entry.timestampMs).toLocaleTimeString()}</time>
          <strong>{entry.level || 'info'}</strong>
          <span>{entry.source || entry.target}</span>
          <p>{entry.message}</p>
        </div>
      ))}
    </div>
  );
}
