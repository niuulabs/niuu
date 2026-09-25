import { useEffect, useMemo, useRef, useState } from 'react';
import { RefreshCw, Search } from 'lucide-react';
import { ErrorState, LoadingState } from '@niuulabs/ui';
import { useOptionalService } from '@niuulabs/plugin-sdk';
import { LiveLogsTab, type IVolundrService } from '@niuulabs/plugin-volundr';
import type { Ravn } from '../../domain/ravn';
import { isSessionRavn, ravnLifeState } from '../../application/ravnWorkbench';
import {
  LOG_SEVERITIES,
  filterResidentLogs,
  formatLogFields,
  normalizeLevel,
  type LogSeverity,
} from '../../application/residentLogFilter';
import { useResidentLogs } from '../hooks/useResidentControl';
import { useRavnActivity } from '../hooks/useSessions';
import { MessageRow } from '../MessageRow';
import { errorText } from './errorText';

/** How often the log buffer is re-read while following a live runtime. */
const LOG_FOLLOW_POLL_MS = 5_000;

function ResidentLogs({ ravn }: { ravn: Ravn }) {
  const state = ravnLifeState(ravn);
  const live = state === 'running' || state === 'starting';
  const [follow, setFollow] = useState(live);
  const [severity, setSeverity] = useState<LogSeverity>('all');
  const [query, setQuery] = useState('');
  const logs = useResidentLogs(ravn, true, follow && live ? LOG_FOLLOW_POLL_MS : false);
  const entries = useMemo(
    () => filterResidentLogs(logs.data?.entries ?? [], severity, query),
    [logs.data, severity, query],
  );
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!follow) return;
    bottomRef.current?.scrollIntoView?.({ block: 'end' });
  }, [entries.length, follow]);

  return (
    <div data-testid="ravn-activity-logs">
      <div className="rw-logbar">
        <div className="rw-seg" role="group" aria-label="Severity">
          {LOG_SEVERITIES.map((option) => (
            <button
              key={option.id}
              type="button"
              aria-pressed={severity === option.id}
              onClick={() => setSeverity(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <label className="rw-search rw-search--narrow">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search logs"
            aria-label="Search logs"
          />
        </label>
        <span className="rw-logbar__spacer" />
        {logs.data && (
          <span className="rw-logbar__meta">
            {entries.length} of {logs.data.bufferTotal || logs.data.entries.length} lines
          </span>
        )}
        {live && (
          <button
            type="button"
            className="rw-btn rw-btn--small"
            aria-pressed={follow}
            onClick={() => setFollow((value) => !value)}
            data-testid="ravn-logs-follow"
          >
            {follow ? 'Following' : 'Follow'}
          </button>
        )}
        <button
          type="button"
          className="rw-icon-btn"
          onClick={() => void logs.refetch()}
          disabled={logs.isFetching}
          aria-label="Refresh logs"
          title="Refresh logs"
        >
          <RefreshCw size={14} aria-hidden="true" />
        </button>
      </div>

      {logs.isLoading ? (
        <LoadingState label="Loading logs…" />
      ) : logs.isError ? (
        <div className="rw-empty" data-testid="ravn-logs-error">
          <div className="rw-empty__inner">
            <h3 className="rw-empty__title">Logs can’t be read right now</h3>
            <p className="rw-empty__text">{errorText(logs.error, 'Failed to read logs')}</p>
            <div className="rw-empty__acts">
              <button type="button" className="rw-btn" onClick={() => void logs.refetch()}>
                Try again
              </button>
            </div>
          </div>
        </div>
      ) : entries.length === 0 ? (
        <p className="rw-muted rw-muted--pad">
          {logs.data?.entries.length ? 'No log lines match.' : 'The runtime reported no log lines.'}
        </p>
      ) : (
        <div className="rw-logs" role="log" aria-label="Runtime logs">
          {entries.map((entry, index) => (
            <div key={`${entry.timestampMs}:${index}`} className="rw-logline">
              <span className="rw-logline__ts">
                {new Date(entry.timestampMs).toLocaleTimeString()}
              </span>
              <span className="rw-logline__lv" data-level={normalizeLevel(entry.level)}>
                {normalizeLevel(entry.level)}
              </span>
              <span className="rw-logline__src" title={entry.target || entry.source}>
                {entry.target || entry.source}
              </span>
              <span className="rw-logline__msg">
                {entry.message}{' '}
                <span className="rw-logline__fields">{formatLogFields(entry.fields)}</span>
              </span>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  );
}

function SessionActivity({ ravn }: { ravn: Ravn }) {
  const activity = useRavnActivity(ravn.id);
  if (activity.isLoading) return <LoadingState label="Loading activity…" />;
  if (activity.data.length === 0) {
    return <p className="rw-muted rw-muted--pad">No recorded activity for this ravn.</p>;
  }
  return (
    <div className="rw-transcript" role="log" aria-label="Ravn activity">
      {activity.data.map((message) => (
        <MessageRow key={message.id} message={message} />
      ))}
    </div>
  );
}

function ForgeSessionLogs({ ravn }: { ravn: Ravn }) {
  const volundr = useOptionalService<IVolundrService>('volundr');
  if (!volundr) {
    return (
      <ErrorState
        title="No session logs here"
        message="This host wires no Forge service, and a session-backed ravn's logs live in Forge."
      />
    );
  }
  return (
    <div className="rw-forge-logs" data-testid="ravn-activity-session-logs">
      <LiveLogsTab sessionId={ravn.sessionId ?? ravn.id} volundr={volundr} />
    </div>
  );
}

/**
 * What a ravn has been doing. Managed runtimes that expose logs show their
 * log buffer, Forge-backed ravens show their session's logs, and other ravens
 * show the messages of their recorded sessions.
 */
export function ActivityTab({ ravn }: { ravn: Ravn }) {
  if (isSessionRavn(ravn)) return <ForgeSessionLogs ravn={ravn} />;
  if (ravn.managed && ravn.capabilities?.includes('logs')) return <ResidentLogs ravn={ravn} />;
  if (ravn.managed) {
    return (
      <ErrorState
        title="No logs from this runtime"
        message={`The ${ravn.profileId ?? 'deployment'} profile does not expose runtime logs.`}
      />
    );
  }
  return <SessionActivity ravn={ravn} />;
}
