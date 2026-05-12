import { useEffect, useMemo, useState } from 'react';
import { cn } from '@niuulabs/ui';
import type { VolundrAggregatedLog, VolundrLogParticipant } from '../../models/volundr.model';

interface StructuredLogViewerProps {
  logs: VolundrAggregatedLog[];
  participants?: VolundrLogParticipant[];
  loading?: boolean;
  emptyText?: string;
  initialLevel?: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR';
  level?: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR';
  onLevelChange?: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR') => void;
  showParticipantFilters?: boolean;
  showDownload?: boolean;
  downloadFilename?: string;
  containerClassName?: string;
  bodyClassName?: string;
  toolbarTestId?: string;
}

function downloadText(filename: string, text: string): void {
  if (typeof window === 'undefined' || typeof document === 'undefined') return;
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.URL.revokeObjectURL(url);
}

function formatLogTimestamp(value: number): string {
  const date = new Date(value);
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(
    2,
    '0',
  )}:${String(date.getSeconds()).padStart(2, '0')}`;
}

function serializeAggregatedLogs(lines: VolundrAggregatedLog[]): string {
  return lines
    .map(
      (line) =>
        `${new Date(line.timestamp).toISOString()} ${line.level.toUpperCase()} ${line.participant} ${line.source} ${line.message}`,
    )
    .join('\n');
}

export function StructuredLogViewer({
  logs,
  participants = [],
  loading = false,
  emptyText = 'No log entries yet.',
  initialLevel = 'DEBUG',
  level,
  onLevelChange,
  showParticipantFilters = true,
  showDownload = true,
  downloadFilename = 'logs.log',
  containerClassName,
  bodyClassName,
  toolbarTestId = 'structured-log-toolbar',
}: StructuredLogViewerProps) {
  const [selectedParticipant, setSelectedParticipant] = useState('all');
  const [internalLevel, setInternalLevel] = useState(initialLevel);
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (level === undefined) setInternalLevel(initialLevel);
  }, [initialLevel, level]);

  const selectedLevel = level ?? internalLevel;

  const resolvedParticipants = useMemo<VolundrLogParticipant[]>(() => {
    if (participants.length > 0) return participants;
    const seen = new Map<string, VolundrLogParticipant>();
    for (const line of logs) {
      if (seen.has(line.participant)) continue;
      seen.set(line.participant, {
        id: line.participant,
        label: line.participantLabel,
        kind: line.participantKind,
      });
    }
    return Array.from(seen.values());
  }, [logs, participants]);

  useEffect(() => {
    if (selectedParticipant === 'all') return;
    if (resolvedParticipants.some((participant) => participant.id === selectedParticipant)) return;
    setSelectedParticipant('all');
  }, [resolvedParticipants, selectedParticipant]);

  const levelFilteredLogs = useMemo(() => {
    const order = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3 } as const;
    return logs.filter((line) => {
      const normalized =
        line.level === 'error'
          ? 'ERROR'
          : line.level === 'warn'
            ? 'WARNING'
            : line.level === 'debug'
              ? 'DEBUG'
              : 'INFO';
      return order[normalized] >= order[selectedLevel];
    });
  }, [logs, selectedLevel]);

  const searchFilteredLogs = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return levelFilteredLogs;
    return levelFilteredLogs.filter((line) =>
      [
        line.message,
        line.source,
        line.participant,
        line.participantLabel,
        line.stream ?? '',
      ]
        .join(' ')
        .toLowerCase()
        .includes(query),
    );
  }, [levelFilteredLogs, search]);

  const filteredLogs = useMemo(
    () =>
      selectedParticipant === 'all'
        ? searchFilteredLogs
        : searchFilteredLogs.filter((line) => line.participant === selectedParticipant),
    [searchFilteredLogs, selectedParticipant],
  );

  const lineCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const line of searchFilteredLogs) {
      counts.set(line.participant, (counts.get(line.participant) ?? 0) + 1);
    }
    return counts;
  }, [searchFilteredLogs]);

  const showToolbar = true;

  return (
    <div className={cn('niuu-flex niuu-h-full niuu-flex-col', containerClassName)}>
      {showToolbar && (
        <div
          className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-4 niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-3"
          data-testid={toolbarTestId}
        >
          <div className="niuu-flex niuu-flex-1 niuu-flex-wrap niuu-gap-2">
            {showParticipantFilters && (
              <>
                <button
                  type="button"
                  className={cn(
                    'niuu-rounded-full niuu-border niuu-px-3 niuu-py-1 niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.16em]',
                    selectedParticipant === 'all'
                      ? 'niuu-border-brand/60 niuu-bg-brand/10 niuu-text-brand'
                      : 'niuu-border-border-subtle niuu-bg-bg-primary niuu-text-text-secondary',
                  )}
                  onClick={() => setSelectedParticipant('all')}
                >
                  All
                  <span className="niuu-ml-2 niuu-text-text-faint">{searchFilteredLogs.length}</span>
                </button>
                {resolvedParticipants.map((participant) => (
                  <button
                    key={participant.id}
                    type="button"
                    className={cn(
                      'niuu-rounded-full niuu-border niuu-px-3 niuu-py-1 niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.16em]',
                      selectedParticipant === participant.id
                        ? 'niuu-border-brand/60 niuu-bg-brand/10 niuu-text-brand'
                        : 'niuu-border-border-subtle niuu-bg-bg-primary niuu-text-text-secondary',
                    )}
                    onClick={() => setSelectedParticipant(participant.id)}
                    data-testid={`log-participant-${participant.id}`}
                  >
                    {participant.label}
                    <span className="niuu-ml-2 niuu-text-text-faint">
                      {lineCounts.get(participant.id) ?? 0}
                    </span>
                  </button>
                ))}
              </>
            )}
          </div>
          <div className="niuu-flex niuu-items-center niuu-gap-2">
            <input
              className="niuu-min-w-[180px] niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-px-2 niuu-py-1 niuu-text-xs niuu-text-text-secondary"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search logs..."
              aria-label="Search logs"
            />
            <label className="niuu-flex niuu-items-center niuu-gap-2 niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.16em] niuu-text-text-faint">
              <span>Level</span>
              <select
                className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-px-2 niuu-py-1 niuu-text-[11px] niuu-text-text-secondary"
                value={selectedLevel}
                onChange={(event) => {
                  const next = event.target.value as 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR';
                  if (onLevelChange) onLevelChange(next);
                  else setInternalLevel(next);
                }}
              >
                <option value="DEBUG">Debug</option>
                <option value="INFO">Info</option>
                <option value="WARNING">Warn</option>
                <option value="ERROR">Error</option>
              </select>
            </label>
            {showDownload && (
              <button
                type="button"
                className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.16em] niuu-text-text-secondary hover:niuu-text-text-primary"
                onClick={() => downloadText(downloadFilename, serializeAggregatedLogs(filteredLogs))}
              >
                Download
              </button>
            )}
          </div>
        </div>
      )}
      <div
        className="niuu-grid niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-2 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-faint"
        style={{ gridTemplateColumns: '84px 84px minmax(180px, 0.9fr) minmax(0, 3.1fr)' }}
      >
        <div>Time</div>
        <div>Level</div>
        <div>Source</div>
        <div>Message</div>
      </div>
      <div className={cn('niuu-flex-1 niuu-overflow-auto niuu-bg-bg-primary', bodyClassName)}>
        {loading && filteredLogs.length === 0 && (
          <div className="niuu-p-4 niuu-text-center niuu-text-sm niuu-text-text-muted">
            Loading logs…
          </div>
        )}
        {!loading && filteredLogs.length === 0 && (
          <div className="niuu-p-4 niuu-text-center niuu-text-sm niuu-text-text-muted">
            {emptyText}
          </div>
        )}
        {filteredLogs.map((line) => (
          <div
            key={line.id}
            className="niuu-grid niuu-gap-0 niuu-border-b niuu-border-border-subtle niuu-px-4 niuu-py-2 niuu-font-mono niuu-text-[12px]"
            style={{ gridTemplateColumns: '84px 84px minmax(180px, 0.9fr) minmax(0, 3.1fr)' }}
          >
            <span className="niuu-text-text-muted">{formatLogTimestamp(line.timestamp)}</span>
            <span
              className={cn(
                'niuu-uppercase',
                line.level === 'error'
                  ? 'niuu-text-rose-300'
                  : line.level === 'warn'
                    ? 'niuu-text-amber-300'
                    : line.level === 'debug'
                      ? 'niuu-text-text-faint'
                      : 'niuu-text-sky-300',
              )}
            >
              {line.level}
            </span>
            <div className="niuu-flex niuu-min-w-0 niuu-items-center niuu-gap-2">
              <span
                className={cn(
                  'niuu-rounded-full niuu-border niuu-px-2 niuu-py-0.5 niuu-text-[10px] niuu-uppercase niuu-tracking-[0.14em]',
                  line.participantKind === 'broker'
                    ? 'niuu-border-sky-500/40 niuu-bg-sky-500/10 niuu-text-sky-300'
                    : line.participantKind === 'service'
                      ? 'niuu-border-emerald-500/40 niuu-bg-emerald-500/10 niuu-text-emerald-300'
                      : 'niuu-border-violet-500/40 niuu-bg-violet-500/10 niuu-text-violet-300',
                )}
              >
                {line.participantLabel}
              </span>
              <span
                className="niuu-truncate niuu-text-text-faint"
                title={`${line.source} · ${line.stream}`}
              >
                {line.source}
              </span>
            </div>
            <span className="niuu-whitespace-pre-wrap niuu-break-words niuu-text-text-primary">
              {line.message}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
