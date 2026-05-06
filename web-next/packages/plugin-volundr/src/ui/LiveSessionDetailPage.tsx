import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { getAccessToken } from '@niuulabs/query';
import {
  Dialog,
  DialogContent,
  ErrorState,
  LoadingState,
  SessionChat,
  cn,
  type PermissionBehavior,
} from '@niuulabs/ui';
import {
  AlertTriangle,
  Check,
  ExternalLink,
  FileCode2,
  FileDiff,
  FilePenLine,
  FolderOpen,
  GitCommitHorizontal,
  MessageSquareText,
  ScrollText,
  SquareTerminal,
} from 'lucide-react';
import type { IVolundrService } from '../ports/IVolundrService';
import type { IFileSystemPort } from '../ports/IFileSystemPort';
import type {
  SessionChronicle,
  SessionFile,
  VolundrAggregatedLog,
  VolundrLogParticipant,
  VolundrSession,
} from '../models/volundr.model';
import { useSessionDetail } from './hooks/useSessionStore';
import { SessionFilesWorkspace } from './SessionFilesWorkspace';
import { useSkuldChat } from './hooks/useSkuldChat';
import { deriveTerminalWsUrl, normalizeSessionUrl, wsUrlToHttpBase } from './liveSessionTransport';
import { SessionTerminalLive } from './SessionTerminalLive';
import './LiveSessionDetailPage.css';

type SessionTab = 'chat' | 'terminal' | 'diffs' | 'files' | 'chronicles' | 'logs';

const ALL_TABS: Array<{ id: SessionTab; label: string; icon: typeof MessageSquareText }> = [
  { id: 'chat', label: 'Chat', icon: MessageSquareText },
  { id: 'terminal', label: 'Terminal', icon: SquareTerminal },
  { id: 'diffs', label: 'Diffs', icon: FileDiff },
  { id: 'files', label: 'Files', icon: FolderOpen },
  { id: 'chronicles', label: 'Chronicle', icon: ScrollText },
  { id: 'logs', label: 'Logs', icon: FileCode2 },
];

function isSessionBooting(status: string | null | undefined): boolean {
  return status === 'starting' || status === 'provisioning';
}

function statusDotClass(status: string | null | undefined): string {
  switch (status) {
    case 'running':
      return 'niuu-bg-brand';
    case 'idle':
      return 'niuu-bg-amber-300';
    case 'starting':
    case 'provisioning':
      return 'niuu-bg-sky-400';
    case 'stopped':
    case 'archived':
      return 'niuu-bg-text-faint';
    case 'error':
      return 'niuu-bg-rose-400';
    default:
      return 'niuu-bg-text-muted';
  }
}

function formatCount(value: number): string {
  if (!Number.isFinite(value)) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}m`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}k`;
  return `${value}`;
}

function formatRelativeAge(epochMs?: number): string {
  if (!epochMs) return '—';
  const delta = Math.max(0, Date.now() - epochMs);
  const minutes = Math.floor(delta / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

function formatEventTime(value: number): string {
  const minutes = Math.floor(value / 60);
  const seconds = value % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function eventTone(type: SessionChronicle['events'][number]['type']) {
  switch (type) {
    case 'message':
      return {
        badge: 'niuu-text-sky-300 niuu-bg-sky-500/12 niuu-border-sky-500/30',
        dot: 'niuu-bg-sky-400',
      };
    case 'file':
      return {
        badge: 'niuu-text-emerald-300 niuu-bg-emerald-500/12 niuu-border-emerald-500/30',
        dot: 'niuu-bg-emerald-400',
      };
    case 'git':
      return {
        badge: 'niuu-text-violet-300 niuu-bg-violet-500/12 niuu-border-violet-500/30',
        dot: 'niuu-bg-violet-400',
      };
    case 'terminal':
      return {
        badge: 'niuu-text-amber-300 niuu-bg-amber-500/12 niuu-border-amber-500/30',
        dot: 'niuu-bg-amber-400',
      };
    case 'error':
      return {
        badge: 'niuu-text-rose-300 niuu-bg-rose-500/12 niuu-border-rose-500/30',
        dot: 'niuu-bg-rose-400',
      };
    default:
      return {
        badge: 'niuu-text-text-secondary niuu-bg-bg-elevated niuu-border-border-subtle',
        dot: 'niuu-bg-text-muted',
      };
  }
}

function eventIcon(type: SessionChronicle['events'][number]['type']) {
  switch (type) {
    case 'message':
      return MessageSquareText;
    case 'file':
      return FilePenLine;
    case 'git':
      return GitCommitHorizontal;
    case 'terminal':
      return SquareTerminal;
    case 'error':
      return AlertTriangle;
    default:
      return ScrollText;
  }
}

function eventLabel(type: SessionChronicle['events'][number]['type']): string {
  switch (type) {
    case 'message':
      return 'Message';
    case 'file':
      return 'File';
    case 'git':
      return 'Commit';
    case 'terminal':
      return 'Terminal';
    case 'error':
      return 'Error';
    default:
      return 'Session';
  }
}

function truncateLeadingPath(value: string, maxLength = 42): string {
  if (value.length <= maxLength) return value;
  const parts = value.split('/').filter(Boolean);
  if (parts.length <= 2) return `…${value.slice(-(maxLength - 1))}`;
  let suffix = parts[parts.length - 1] ?? value;
  let index = parts.length - 2;
  while (index >= 0) {
    const candidate = `${parts[index]}/${suffix}`;
    if (candidate.length + 2 > maxLength) break;
    suffix = candidate;
    index -= 1;
  }
  return `…/${suffix}`;
}

async function copyText(text: string): Promise<boolean> {
  if (typeof navigator === 'undefined' || !navigator.clipboard?.writeText) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
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

function normalizeRepoLink(source: VolundrSession['source'] | null | undefined) {
  if (!source || source.type !== 'git' || !source.repo) return null;
  if (source.repo.startsWith('http://') || source.repo.startsWith('https://')) return source.repo;
  return `https://github.com/${source.repo}`;
}

function renderSourceMeta(session: VolundrSession | null | undefined) {
  if (!session?.source) return null;
  if (session.source.type === 'git') {
    const repoUrl = normalizeRepoLink(session.source);
    const repoLabel = session.source.repo.replace(/^https?:\/\/github\.com\//, '');
    return (
      <span className="niuu-inline-flex niuu-items-center niuu-gap-2 niuu-font-mono niuu-text-[12px]">
        <span className="niuu-text-text-faint" aria-hidden>
          ❯
        </span>
        {repoUrl ? (
          <a
            href={repoUrl}
            target="_blank"
            rel="noreferrer"
            className="niuu-text-text-secondary hover:niuu-text-text-primary hover:niuu-underline"
            title={repoUrl}
          >
            {repoLabel}
          </a>
        ) : (
          <span className="niuu-text-text-secondary">{repoLabel}</span>
        )}
        <span className="niuu-text-brand">@{session.source.branch ?? 'main'}</span>
      </span>
    );
  }

  return (
    <span className="niuu-inline-flex niuu-items-center niuu-gap-2 niuu-font-mono niuu-text-[12px]">
      <span className="niuu-text-text-faint" aria-hidden>
        ⌂
      </span>
      <span className="niuu-text-text-secondary">{session.source.path ?? 'local mount'}</span>
    </span>
  );
}

function HeaderMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="niuu-flex niuu-items-baseline niuu-gap-2 niuu-font-mono">
      <span className="niuu-text-[10px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-faint">
        {label}
      </span>
      <span className="niuu-text-[12px] niuu-text-text-secondary">{value}</span>
    </div>
  );
}

function HeaderDivider() {
  return (
    <span
      className="niuu-mx-1.5 niuu-flex-shrink-0 niuu-self-center"
      style={{
        width: '2px',
        height: '16px',
        background:
          'linear-gradient(to bottom, rgba(255,255,255,0.18), rgba(255,255,255,0.42), rgba(255,255,255,0.18))',
      }}
      aria-hidden="true"
    />
  );
}

function HeaderActionButton({
  label,
  onClick,
  disabled,
  tone = 'neutral',
  title,
  className,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: 'neutral' | 'critical' | 'brand';
  title?: string;
  className?: string;
}) {
  const toneClass =
    tone === 'critical'
      ? 'niuu-border-rose-500/35 niuu-bg-rose-500/10 niuu-text-rose-200 hover:niuu-bg-rose-500/15'
      : tone === 'brand'
        ? 'niuu-border-brand/40 niuu-bg-brand/12 niuu-text-brand hover:niuu-bg-brand/18'
        : 'niuu-border-border-subtle niuu-bg-bg-elevated niuu-text-text-secondary hover:niuu-bg-bg-tertiary';

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        'niuu-inline-flex niuu-appearance-none niuu-items-center niuu-justify-center niuu-rounded-md niuu-border niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-transition-colors disabled:niuu-cursor-not-allowed disabled:niuu-opacity-45',
        toneClass,
        className,
      )}
    >
      {label}
    </button>
  );
}

function IconActionButton({
  label,
  title,
  onClick,
  disabled,
  tone = 'neutral',
}: {
  label: string;
  title: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: 'neutral' | 'critical' | 'brand';
}) {
  return (
    <HeaderActionButton
      label={label}
      onClick={onClick}
      disabled={disabled}
      tone={tone}
      title={title}
    />
  );
}

function SessionIdChip({ sessionId }: { sessionId: string }) {
  const [copied, setCopied] = useState(false);
  const shortId = `${sessionId.slice(0, 8)}…${sessionId.slice(-4)}`;

  return (
    <button
      type="button"
      title={sessionId}
      data-testid="session-id-label"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(sessionId);
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        } catch {
          setCopied(false);
        }
      }}
      className="niuu-truncate niuu-font-mono niuu-text-[12px] niuu-text-text-muted hover:niuu-text-text-primary"
    >
      {copied ? 'copied' : shortId}
    </button>
  );
}

function TicketLink({ issue }: { issue: VolundrSession['trackerIssue'] }) {
  if (!issue) return null;
  return (
    <a
      href={issue.url}
      target="_blank"
      rel="noreferrer"
      className="niuu-inline-flex niuu-items-center niuu-gap-2 niuu-rounded-md niuu-border niuu-border-brand/30 niuu-bg-brand/10 niuu-px-2.5 niuu-py-1 niuu-font-mono niuu-text-[11px] niuu-text-brand hover:niuu-bg-brand/15"
      title={issue.identifier}
    >
      <span>{issue.identifier}</span>
      <ExternalLink className="niuu-h-3.5 niuu-w-3.5" />
    </a>
  );
}

function DeleteSessionDialog({
  open,
  session,
  onClose,
  onConfirm,
  busy,
}: {
  open: boolean;
  session: VolundrSession | null;
  onClose: () => void;
  onConfirm: (cleanup: string[]) => void;
  busy: boolean;
}) {
  const [cleanup, setCleanup] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!open) setCleanup(new Set());
  }, [open]);

  if (!open || !session) return null;

  const isManual = session.origin === 'manual';
  const isLocalStorage = session.source.type === 'local_mount';

  function toggleCleanup(key: string) {
    setCleanup((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent
        title={isManual ? 'Remove session' : 'Delete session'}
        description={
          isManual
            ? `Remove ${session.name} from the session list?`
            : `Delete ${session.name}? This action cannot be undone.`
        }
        className="niuu-live-delete-dialog"
      >
        {!isManual && (
          <div className="niuu-live-delete-dialog__cleanup">
            <div className="niuu-live-delete-dialog__cleanup-label">Also clean up</div>
            <div className="niuu-live-delete-dialog__cleanup-list">
              <label
                className={cn(
                  'niuu-live-delete-dialog__cleanup-option',
                  isLocalStorage && 'niuu-live-delete-dialog__cleanup-option--disabled',
                )}
              >
                <input
                  type="checkbox"
                  checked={cleanup.has('workspace_storage')}
                  onChange={() => toggleCleanup('workspace_storage')}
                  disabled={isLocalStorage}
                  data-testid="cleanup-workspace_storage"
                  className="niuu-sr-only"
                />
                <span
                  aria-hidden="true"
                  className={cn(
                    'niuu-live-delete-dialog__checkbox',
                    cleanup.has('workspace_storage') &&
                      'niuu-live-delete-dialog__checkbox--checked',
                    isLocalStorage && 'niuu-live-delete-dialog__checkbox--disabled',
                  )}
                >
                  {cleanup.has('workspace_storage') ? (
                    <Check className="niuu-h-3 niuu-w-3" />
                  ) : null}
                </span>
                <div>
                  <div className="niuu-text-sm niuu-text-text-primary">
                    Delete workspace storage
                  </div>
                  <div className="niuu-mt-1 niuu-text-xs niuu-text-text-muted">
                    {isLocalStorage
                      ? 'Local mounted workspace — manage storage on your machine.'
                      : 'Permanently delete the workspace storage so future sessions cannot reuse it.'}
                  </div>
                </div>
              </label>
              <label className="niuu-live-delete-dialog__cleanup-option">
                <input
                  type="checkbox"
                  checked={cleanup.has('chronicles')}
                  onChange={() => toggleCleanup('chronicles')}
                  data-testid="cleanup-chronicles"
                  className="niuu-sr-only"
                />
                <span
                  aria-hidden="true"
                  className={cn(
                    'niuu-live-delete-dialog__checkbox',
                    cleanup.has('chronicles') && 'niuu-live-delete-dialog__checkbox--checked',
                  )}
                >
                  {cleanup.has('chronicles') ? <Check className="niuu-h-3 niuu-w-3" /> : null}
                </span>
                <div>
                  <div className="niuu-text-sm niuu-text-text-primary">Delete chronicles</div>
                  <div className="niuu-mt-1 niuu-text-xs niuu-text-text-muted">
                    Remove timeline history and chronicle records for this session.
                  </div>
                </div>
              </label>
            </div>
          </div>
        )}

        <div className="niuu-live-delete-dialog__actions">
          <HeaderActionButton
            label="cancel"
            onClick={onClose}
            disabled={busy}
            className="niuu-live-delete-dialog__action-button"
          />
          <HeaderActionButton
            label={busy ? (isManual ? 'removing…' : 'deleting…') : isManual ? 'remove' : 'delete'}
            onClick={() => onConfirm(Array.from(cleanup))}
            disabled={busy}
            tone="critical"
            className="niuu-live-delete-dialog__action-button niuu-live-delete-dialog__action-button--critical"
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}

function LiveLogsTab({ sessionId, volundr }: { sessionId: string; volundr: IVolundrService }) {
  const [logs, setLogs] = useState<{
    lines: VolundrAggregatedLog[];
    participants: VolundrLogParticipant[];
  }>({ lines: [], participants: [] });
  const [loading, setLoading] = useState(true);
  const [selectedParticipant, setSelectedParticipant] = useState('all');
  const [level, setLevel] = useState('DEBUG');

  useEffect(() => {
    let active = true;
    setLoading(true);

    void volundr
      .getAggregatedLogs(sessionId, { limit: 400, level })
      .then((payload) => {
        if (!active) return;
        setLogs(payload);
        setLoading(false);
      })
      .catch(() => {
        if (!active) return;
        setLogs({ lines: [], participants: [] });
        setLoading(false);
      });

    const unsubscribe = volundr.subscribeAggregatedLogs(
      sessionId,
      { limit: 400, level },
      (payload) => {
        if (!active) return;
        setLogs(payload);
        setLoading(false);
      },
    );

    return () => {
      active = false;
      unsubscribe();
    };
  }, [level, sessionId, volundr]);

  const participants = useMemo<VolundrLogParticipant[]>(() => {
    if (logs.participants.length > 0) return logs.participants;
    const seen = new Map<string, VolundrLogParticipant>();
    for (const line of logs.lines) {
      if (seen.has(line.participant)) continue;
      seen.set(line.participant, {
        id: line.participant,
        label: line.participantLabel,
        kind: line.participantKind,
      });
    }
    return Array.from(seen.values());
  }, [logs.lines, logs.participants]);

  useEffect(() => {
    if (selectedParticipant === 'all') return;
    if (participants.some((participant) => participant.id === selectedParticipant)) return;
    setSelectedParticipant('all');
  }, [participants, selectedParticipant]);

  const filteredLogs = useMemo(
    () =>
      selectedParticipant === 'all'
        ? logs.lines
        : logs.lines.filter((line) => line.participant === selectedParticipant),
    [logs.lines, selectedParticipant],
  );

  const lineCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const line of logs.lines) {
      counts.set(line.participant, (counts.get(line.participant) ?? 0) + 1);
    }
    return counts;
  }, [logs.lines]);

  return (
    <div className="niuu-flex niuu-h-full niuu-flex-col" data-testid="live-logs-tab">
      <div
        className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-4 niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-3"
        data-testid="live-logs-toolbar"
      >
        <div className="niuu-flex niuu-flex-1 niuu-flex-wrap niuu-gap-2">
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
            <span className="niuu-ml-2 niuu-text-text-faint">{logs.lines.length}</span>
          </button>
          {participants.map((participant) => (
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
        </div>
        <div className="niuu-flex niuu-items-center niuu-gap-2">
          <label className="niuu-flex niuu-items-center niuu-gap-2 niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.16em] niuu-text-text-faint">
            <span>Level</span>
            <select
              className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-px-2 niuu-py-1 niuu-text-[11px] niuu-text-text-secondary"
              value={level}
              onChange={(event) => setLevel(event.target.value)}
            >
              <option value="DEBUG">Debug</option>
              <option value="INFO">Info</option>
              <option value="WARNING">Warn</option>
              <option value="ERROR">Error</option>
            </select>
          </label>
          <button
            type="button"
            className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.16em] niuu-text-text-secondary hover:niuu-text-text-primary"
            onClick={() =>
              downloadText(
                `${sessionId}-${selectedParticipant}-logs.log`,
                serializeAggregatedLogs(filteredLogs),
              )
            }
          >
            Download
          </button>
        </div>
      </div>
      <div
        className="niuu-grid niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-2 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-faint"
        style={{ gridTemplateColumns: '84px 84px minmax(180px, 0.9fr) minmax(0, 3.1fr)' }}
      >
        <div>Time</div>
        <div>Level</div>
        <div>Source</div>
        <div>Message</div>
      </div>
      <div className="niuu-flex-1 niuu-overflow-auto niuu-bg-bg-primary">
        {loading && filteredLogs.length === 0 && (
          <div className="niuu-p-4 niuu-text-center niuu-text-sm niuu-text-text-muted">
            Loading logs…
          </div>
        )}
        {!loading && filteredLogs.length === 0 && (
          <div className="niuu-p-4 niuu-text-center niuu-text-sm niuu-text-text-muted">
            No log entries yet.
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

function LiveChroniclesTab({
  sessionId,
  sessionStatus,
  session,
  volundr,
}: {
  sessionId: string;
  sessionStatus: string | null;
  session: VolundrSession | null;
  volundr: IVolundrService;
}) {
  const [chronicle, setChronicle] = useState<SessionChronicle | null>(null);
  const [loading, setLoading] = useState(true);
  const [copiedPath, setCopiedPath] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    void volundr.getChronicle(sessionId).then((payload) => {
      if (!active) return;
      setChronicle(payload);
      setLoading(false);
    });
    const unsubscribe = volundr.subscribeChronicle(sessionId, (payload) => {
      setChronicle(payload);
      setLoading(false);
    });
    return () => {
      active = false;
      unsubscribe();
    };
  }, [sessionId, volundr]);

  const handleCopyPath = useCallback((path: string) => {
    void copyText(path).then((ok) => {
      if (!ok) return;
      setCopiedPath(path);
      window.setTimeout(() => {
        setCopiedPath((current) => (current === path ? null : current));
      }, 1400);
    });
  }, []);

  if (loading) {
    return (
      <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
        Loading chronicle…
      </div>
    );
  }

  if (!chronicle) {
    return (
      <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
        {sessionStatus === 'running'
          ? 'No chronicle data yet.'
          : 'Start the session to view its chronicle.'}
      </div>
    );
  }

  const maxBurn = Math.max(1, ...chronicle.tokenBurn);
  const totalTokens = chronicle.events.reduce((sum, event) => sum + (event.tokens ?? 0), 0);

  return (
    <div className="niuu-live-chronicles-layout">
      <div className="niuu-flex niuu-min-h-0 niuu-flex-col niuu-border-r niuu-border-border-subtle niuu-bg-bg-primary">
        <div className="niuu-border-b niuu-border-border-subtle niuu-px-4 niuu-py-2.5">
          <div className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-4">
            <div className="niuu-min-w-0">
              <div className="niuu-whitespace-nowrap niuu-text-sm niuu-font-medium niuu-text-text-primary">
                Event timeline
              </div>
              <div className="niuu-mt-1 niuu-whitespace-nowrap niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
                {chronicle.events.length} events · {formatCount(totalTokens)} tokens
              </div>
            </div>
            <div className="niuu-flex niuu-h-12 niuu-items-end niuu-gap-1">
              {chronicle.tokenBurn.map((value, index) => (
                <span
                  key={`${value}-${index}`}
                  className={cn(
                    'niuu-w-2 niuu-rounded-sm niuu-bg-brand/30',
                    value >= maxBurn * 0.75 && 'niuu-bg-brand/60',
                  )}
                  style={{ height: `${Math.max(14, (value / maxBurn) * 48)}px` }}
                  title={`${value} tokens`}
                />
              ))}
            </div>
          </div>
        </div>

        <div className="niuu-min-h-0 niuu-flex-1 niuu-overflow-auto niuu-px-4 niuu-py-3">
          <div className="niuu-live-chronicles-timeline">
            <div className="niuu-live-chronicles-rail" />
            <div className="niuu-flex niuu-flex-col">
              {chronicle.events.map((event, index) => {
                const tone = eventTone(event.type);
                const Icon = eventIcon(event.type);
                return (
                  <div
                    key={`${event.type}-${event.t}-${index}`}
                    className="niuu-live-chronicles-row niuu-border-b niuu-border-border-subtle/60 niuu-py-2.5 last:niuu-border-b-0"
                  >
                    <div className="niuu-pt-1 niuu-text-right niuu-font-mono niuu-text-[10px] niuu-text-text-faint">
                      {formatEventTime(event.t)}
                    </div>
                    <div className="niuu-relative niuu-flex niuu-justify-center">
                      <span
                        className={cn(
                          'niuu-relative niuu-z-[1] niuu-mt-1.5 niuu-inline-flex niuu-h-5 niuu-w-5 niuu-items-center niuu-justify-center niuu-rounded-md niuu-border niuu-bg-bg-secondary',
                          tone.badge,
                        )}
                      >
                        <Icon className="niuu-h-3 niuu-w-3" />
                      </span>
                    </div>
                    <div className="niuu-min-w-0">
                      <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-3">
                        <div className="niuu-flex niuu-min-w-0 niuu-items-center niuu-gap-2">
                          <span className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.16em] niuu-text-text-faint">
                            {eventLabel(event.type)}
                          </span>
                          {event.action ? (
                            <span className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-muted">
                              {event.action}
                            </span>
                          ) : null}
                        </div>
                        <div className="niuu-flex niuu-flex-wrap niuu-justify-end niuu-gap-x-2 niuu-gap-y-1 niuu-font-mono niuu-text-[10px]">
                          {typeof event.tokens === 'number' && (
                            <span className="niuu-text-text-muted">
                              {formatCount(event.tokens)} tok
                            </span>
                          )}
                          {(typeof event.ins === 'number' || typeof event.del === 'number') && (
                            <span className="niuu-text-text-muted">
                              {typeof event.ins === 'number' ? `+${event.ins}` : ''}
                              {typeof event.del === 'number' ? ` / -${event.del}` : ''}
                            </span>
                          )}
                          {event.hash && <span className="niuu-text-text-faint">{event.hash}</span>}
                          {typeof event.exit === 'number' && (
                            <span
                              className={
                                event.exit === 0 ? 'niuu-text-emerald-300' : 'niuu-text-rose-300'
                              }
                            >
                              exit {event.exit}
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="niuu-mt-1 niuu-text-[13px] niuu-leading-5 niuu-text-text-primary">
                        {event.label}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      <div className="niuu-flex niuu-min-h-0 niuu-flex-col niuu-overflow-auto niuu-bg-bg-secondary niuu-p-3">
        {session?.trackerIssue && (
          <section className="niuu-mb-3 niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-p-3">
            <div className="niuu-mb-2 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-faint">
              Tracker
            </div>
            <a
              href={session.trackerIssue.url}
              target="_blank"
              rel="noreferrer"
              className="niuu-inline-flex niuu-items-center niuu-gap-2 niuu-text-sm niuu-text-brand hover:niuu-underline"
            >
              <span className="niuu-rounded-md niuu-border niuu-border-brand/35 niuu-bg-brand/10 niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-[11px]">
                {session.trackerIssue.identifier}
              </span>
              <span className="niuu-text-text-primary">{session.trackerIssue.title}</span>
            </a>
          </section>
        )}

        <section className="niuu-mb-3 niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-p-3">
          <div className="niuu-mb-2.5 niuu-flex niuu-items-center niuu-justify-between">
            <div className="niuu-text-sm niuu-font-medium niuu-text-text-primary">
              Files modified
            </div>
            <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
              {chronicle.files.length}
            </div>
          </div>
          <div className="niuu-flex niuu-flex-col">
            {chronicle.files.length > 0 ? (
              chronicle.files.map((file, index) => (
                <button
                  key={file.path}
                  type="button"
                  onClick={() => handleCopyPath(file.path)}
                  className={cn(
                    'niuu-flex niuu-w-full niuu-items-start niuu-gap-2 niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-2.5 niuu-py-2 niuu-text-left hover:niuu-bg-bg-tertiary',
                    index > 0 && 'niuu-border-t',
                  )}
                  title={copiedPath === file.path ? 'Copied' : `${file.path} · click to copy`}
                >
                  <span className="niuu-mt-0.5 niuu-inline-flex niuu-h-5 niuu-w-5 niuu-flex-shrink-0 niuu-items-center niuu-justify-center niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-primary">
                    {copiedPath === file.path ? (
                      <Check className="niuu-h-3 niuu-w-3 niuu-text-brand" />
                    ) : (
                      <FilePenLine className="niuu-h-3 niuu-w-3 niuu-text-text-muted" />
                    )}
                  </span>
                  <div className="niuu-min-w-0 niuu-flex-1">
                    <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-3">
                      <span className="niuu-truncate niuu-font-mono niuu-text-[11px] niuu-text-text-primary">
                        {truncateLeadingPath(file.path)}
                      </span>
                      <span className="niuu-flex-shrink-0 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
                        {file.status}
                      </span>
                    </div>
                    <div className="niuu-mt-0.5 niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                      +{file.ins} / -{file.del}
                    </div>
                  </div>
                </button>
              ))
            ) : (
              <div className="niuu-text-sm niuu-text-text-muted">No file changes yet.</div>
            )}
          </div>
        </section>

        <section className="niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-p-3">
          <div className="niuu-mb-2.5 niuu-flex niuu-items-center niuu-justify-between">
            <div className="niuu-text-sm niuu-font-medium niuu-text-text-primary">Commits</div>
            <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
              {chronicle.commits.length}
            </div>
          </div>
          <div className="niuu-flex niuu-flex-col">
            {chronicle.commits.length > 0 ? (
              chronicle.commits.map((commit, index) => (
                <div
                  key={commit.hash}
                  className={cn(
                    'niuu-flex niuu-items-start niuu-gap-2 niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-2.5 niuu-py-2',
                    index > 0 && 'niuu-border-t',
                  )}
                >
                  <span className="niuu-mt-0.5 niuu-inline-flex niuu-h-5 niuu-w-5 niuu-flex-shrink-0 niuu-items-center niuu-justify-center niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-primary">
                    <GitCommitHorizontal className="niuu-h-3 niuu-w-3 niuu-text-text-muted" />
                  </span>
                  <div className="niuu-min-w-0 niuu-flex-1">
                    <div className="niuu-truncate niuu-text-[12px] niuu-text-text-primary">
                      {commit.msg}
                    </div>
                    <div className="niuu-mt-0.5 niuu-flex niuu-items-center niuu-gap-2 niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                      <span>{commit.hash.slice(0, 8)}</span>
                      <span className="niuu-text-text-faint">•</span>
                      <span>{commit.time}</span>
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <div className="niuu-text-sm niuu-text-text-muted">No commits yet.</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

interface DiffHunkLine {
  type: 'context' | 'add' | 'remove';
  content: string;
  oldLine?: number;
  newLine?: number;
}

interface DiffHunk {
  oldStart: number;
  oldCount: number;
  newStart: number;
  newCount: number;
  lines: DiffHunkLine[];
}

interface DiffData {
  filePath: string;
  hunks: DiffHunk[];
}

type DiffBase = 'last-commit' | 'default-branch';

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function useLiveDiffViewer(chatEndpoint: string | null) {
  const apiBase = useMemo(
    () => (chatEndpoint ? wsUrlToHttpBase(chatEndpoint) : null),
    [chatEndpoint],
  );
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [diff, setDiff] = useState<DiffData | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<Error | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [diffBase, setDiffBaseState] = useState<DiffBase>('last-commit');

  const fetchFiles = useCallback(async () => {
    if (!apiBase) {
      setFiles([]);
      return;
    }
    setFilesLoading(true);
    try {
      const params = new URLSearchParams({ base: diffBase });
      const response = await fetch(`${apiBase}/api/diff/files?${params}`, {
        headers: authHeaders(),
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch diff files: ${response.status}`);
      }
      const data = (await response.json()) as { files?: SessionFile[] };
      setFiles(data.files ?? []);
    } catch {
      setFiles([]);
    } finally {
      setFilesLoading(false);
    }
  }, [apiBase, diffBase]);

  const selectFile = useCallback(
    async (filePath: string) => {
      if (!apiBase) {
        setSelectedFile(filePath);
        setDiff(null);
        setDiffError(new Error('No session endpoint available'));
        return;
      }
      setSelectedFile(filePath);
      setDiffLoading(true);
      setDiffError(null);
      try {
        const params = new URLSearchParams({ file: filePath, base: diffBase });
        const response = await fetch(`${apiBase}/api/diff?${params}`, {
          headers: authHeaders(),
        });
        if (!response.ok) {
          throw new Error(`Failed to fetch diff: ${response.status}`);
        }
        setDiff((await response.json()) as DiffData);
      } catch (error) {
        setDiff(null);
        setDiffError(error instanceof Error ? error : new Error('Failed to fetch diff'));
      } finally {
        setDiffLoading(false);
      }
    },
    [apiBase, diffBase],
  );

  const setDiffBase = useCallback((base: DiffBase) => {
    setDiffBaseState(base);
    setSelectedFile(null);
    setDiff(null);
    setDiffError(null);
  }, []);

  useEffect(() => {
    void fetchFiles();
  }, [fetchFiles]);

  return {
    files,
    filesLoading,
    diff,
    diffLoading,
    diffError,
    selectedFile,
    diffBase,
    selectFile,
    setDiffBase,
  };
}

function diffStatusLetter(status: SessionFile['status']): string {
  return status === 'new' ? 'A' : status === 'mod' ? 'M' : 'D';
}

function diffStatusColor(status: SessionFile['status']): string {
  return status === 'new'
    ? 'niuu-text-state-ok'
    : status === 'mod'
      ? 'niuu-text-state-warn'
      : 'niuu-text-critical';
}

function DiffFileList({
  files,
  selectedPath,
  onSelect,
}: {
  files: SessionFile[];
  selectedPath: string | null;
  onSelect: (path: string) => void;
}) {
  return (
    <div
      className="niuu-flex niuu-h-full niuu-flex-col niuu-overflow-auto"
      data-testid="diff-file-list"
    >
      <div className="niuu-border-b niuu-border-border-subtle niuu-bg-bg-primary niuu-px-4 niuu-py-3">
        <div className="niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-muted">
          changed files
        </div>
      </div>
      {files.map((f) => (
        <button
          key={f.path}
          type="button"
          onClick={() => onSelect(f.path)}
          className={cn(
            'niuu-flex niuu-items-center niuu-gap-2 niuu-border-b niuu-border-border-subtle niuu-px-4 niuu-py-2.5 niuu-text-left niuu-text-xs hover:niuu-bg-bg-elevated',
            selectedPath === f.path && 'niuu-bg-bg-elevated',
          )}
          data-testid={`diff-file-${f.status}`}
        >
          <span
            className={cn(
              'niuu-w-4 niuu-flex-shrink-0 niuu-font-mono niuu-font-medium',
              diffStatusColor(f.status),
            )}
          >
            {diffStatusLetter(f.status)}
          </span>
          <span className="niuu-min-w-0 niuu-flex-1 niuu-truncate niuu-font-mono niuu-text-text-secondary">
            {f.path}
          </span>
          <span className="niuu-flex-shrink-0 niuu-font-mono niuu-text-[10px]">
            {f.ins > 0 && <span className="niuu-text-state-ok">+{f.ins}</span>}
            {f.del > 0 && <span className="niuu-ml-1 niuu-text-critical">-{f.del}</span>}
          </span>
        </button>
      ))}
    </div>
  );
}

function DiffViewer({
  file,
  diff,
  loading,
  error,
}: {
  file: SessionFile | null;
  diff: DiffData | null;
  loading: boolean;
  error: Error | null;
}) {
  return (
    <div
      className="niuu-flex niuu-h-full niuu-flex-col niuu-overflow-auto"
      data-testid="diff-viewer"
    >
      <div className="niuu-flex niuu-flex-shrink-0 niuu-items-center niuu-gap-2 niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-3">
        {file ? (
          <>
            <span
              className={cn(
                'niuu-font-mono niuu-font-medium niuu-text-xs',
                diffStatusColor(file.status),
              )}
            >
              {diffStatusLetter(file.status)}
            </span>
            <span className="niuu-font-mono niuu-text-sm niuu-text-text-primary">{file.path}</span>
            <span className="niuu-font-mono niuu-text-xs niuu-text-text-muted">
              {file.ins > 0 && <span className="niuu-text-state-ok">+{file.ins}</span>}
              {file.del > 0 && <span className="niuu-ml-1 niuu-text-critical">-{file.del}</span>}
            </span>
          </>
        ) : (
          <span className="niuu-font-mono niuu-text-sm niuu-text-text-muted">Diff viewer</span>
        )}
      </div>
      <div className="niuu-flex-1 niuu-overflow-auto niuu-bg-bg-primary">
        {loading ? (
          <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-font-mono niuu-text-sm niuu-text-text-muted">
            Loading diff...
          </div>
        ) : error ? (
          <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-font-mono niuu-text-sm niuu-text-critical">
            Failed to load diff: {error.message}
          </div>
        ) : !file || !diff ? (
          <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-font-mono niuu-text-sm niuu-text-text-muted">
            Select a file to view changes
          </div>
        ) : diff.hunks.length === 0 ? (
          <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-font-mono niuu-text-sm niuu-text-text-muted">
            No changes in this file
          </div>
        ) : (
          diff.hunks.map((hunk, i) => (
            <div key={i} className="niuu-font-mono niuu-text-xs">
              <div className="niuu-bg-bg-tertiary niuu-px-4 niuu-py-0.5 niuu-text-text-muted">
                @@ -{hunk.oldStart},{hunk.oldCount} +{hunk.newStart},{hunk.newCount} @@
              </div>
              {hunk.lines.map((line, j) => (
                <div
                  key={j}
                  className={cn(
                    'niuu-flex niuu-gap-2 niuu-px-4 niuu-py-px',
                    line.type === 'add' &&
                      'niuu-bg-[color-mix(in_srgb,var(--color-brand)_8%,transparent)]',
                    line.type === 'remove' &&
                      'niuu-bg-[color-mix(in_srgb,var(--color-critical)_8%,transparent)]',
                  )}
                >
                  <span className="niuu-w-8 niuu-flex-shrink-0 niuu-select-none niuu-text-right niuu-text-text-faint">
                    {line.oldLine ?? ''}
                  </span>
                  <span className="niuu-w-8 niuu-flex-shrink-0 niuu-select-none niuu-text-right niuu-text-text-faint">
                    {line.newLine ?? ''}
                  </span>
                  <span
                    className={cn(
                      'niuu-w-3 niuu-flex-shrink-0 niuu-select-none',
                      line.type === 'add' && 'niuu-text-state-ok',
                      line.type === 'remove' && 'niuu-text-critical',
                      line.type === 'context' && 'niuu-text-text-faint',
                    )}
                  >
                    {line.type === 'add' ? '+' : line.type === 'remove' ? '-' : ' '}
                  </span>
                  <span className="niuu-text-text-primary">{line.content || '\u00A0'}</span>
                </div>
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function LiveDiffsTab({ chatEndpoint }: { chatEndpoint: string | null }) {
  const {
    files,
    filesLoading,
    diff,
    diffLoading,
    diffError,
    selectedFile,
    diffBase,
    selectFile,
    setDiffBase,
  } = useLiveDiffViewer(chatEndpoint);
  const selected = files.find((file) => file.path === selectedFile) ?? null;

  return (
    <div className="niuu-live-diffs-layout niuu-h-full" data-testid="diffs-tab">
      <div className="niuu-overflow-hidden niuu-border-r niuu-border-border-subtle niuu-bg-bg-secondary">
        <div className="niuu-flex niuu-flex-shrink-0 niuu-items-center niuu-justify-between niuu-border-b niuu-border-border-subtle niuu-bg-bg-primary niuu-px-4 niuu-py-3">
          <div className="niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-muted">
            changed files
          </div>
          <div className="niuu-flex niuu-items-center niuu-gap-1">
            {(['last-commit', 'default-branch'] as const).map((base) => (
              <button
                key={base}
                type="button"
                onClick={() => setDiffBase(base)}
                className={cn(
                  'niuu-rounded-sm niuu-border niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[10px]',
                  diffBase === base
                    ? 'niuu-border-brand niuu-bg-brand/10 niuu-text-brand'
                    : 'niuu-border-border-subtle niuu-text-text-muted hover:niuu-text-text-secondary',
                )}
              >
                {base === 'last-commit' ? 'last commit' : 'default branch'}
              </button>
            ))}
          </div>
        </div>
        {filesLoading ? (
          <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-font-mono niuu-text-sm niuu-text-text-muted">
            Loading files...
          </div>
        ) : (
          <DiffFileList
            files={files}
            selectedPath={selectedFile}
            onSelect={(path) => void selectFile(path)}
          />
        )}
      </div>
      <div className="niuu-overflow-hidden">
        <DiffViewer file={selected} diff={diff} loading={diffLoading} error={diffError} />
      </div>
    </div>
  );
}

export function LiveSessionDetailPage({
  sessionId,
  readOnly = false,
}: {
  sessionId: string;
  readOnly?: boolean;
  initialTab?: SessionTab;
}) {
  const [activeTab, setActiveTab] = useState<SessionTab>('chat');
  const [tabWasManuallySelected, setTabWasManuallySelected] = useState(false);
  const [actionBusy, setActionBusy] = useState<'start' | 'stop' | 'delete' | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const volundr = useService<IVolundrService>('volundr');
  const filesystem = useService<IFileSystemPort>('filesystem');
  const queryClient = useQueryClient();
  const sessionQuery = useSessionDetail(sessionId);
  const liveSessionQuery = useQuery({
    queryKey: ['volundr', 'raw-session', sessionId],
    queryFn: () => volundr.getSession(sessionId),
    refetchInterval: 5_000,
  });
  const sessionFeaturesQuery = useQuery({
    queryKey: ['volundr', 'feature-modules', 'session'],
    queryFn: () => volundr.getFeatureModules('session'),
    staleTime: 30_000,
  });
  const featurePrefsQuery = useQuery({
    queryKey: ['volundr', 'feature-prefs', 'session'],
    queryFn: () => volundr.getUserFeaturePreferences(),
    staleTime: 30_000,
  });
  const modelsQuery = useQuery({
    queryKey: ['volundr', 'models'],
    queryFn: () => volundr.getModels(),
    staleTime: 30_000,
  });

  const liveSession = liveSessionQuery.data;
  const sessionStatus = liveSession?.status ?? null;
  const isRunning = sessionStatus === 'running';
  const chatEndpoint = normalizeSessionUrl(liveSession?.chatEndpoint ?? null);
  const isReady = isRunning && Boolean(chatEndpoint);
  const terminalUrl = deriveTerminalWsUrl(chatEndpoint);
  const chat = useSkuldChat(chatEndpoint);
  const sessionName = sessionQuery.data?.personaName ?? liveSession?.name ?? sessionId;
  const sessionFeatures = useMemo(
    () => sessionFeaturesQuery.data ?? [],
    [sessionFeaturesQuery.data],
  );
  const featurePrefs = useMemo(() => featurePrefsQuery.data ?? [], [featurePrefsQuery.data]);
  const modelCatalog = modelsQuery.data ?? {};
  const modelInfo = liveSession?.model ? modelCatalog[liveSession.model] : undefined;
  const modelLabel = modelInfo?.name ?? liveSession?.model ?? 'unknown';

  const tabs = useMemo(() => {
    const prefMap = new Map(featurePrefs.map((pref) => [pref.featureKey, pref]));
    const visible = ALL_TABS.filter((tab) => {
      if (tab.id === 'diffs') return true;
      const feature = sessionFeatures.find((candidate) => candidate.key === tab.id);
      if (!feature?.enabled) return false;
      const pref = prefMap.get(tab.id);
      if (pref && !pref.visible) return false;
      return true;
    });

    visible.sort((left, right) => {
      const leftFeature = sessionFeatures.find((feature) => feature.key === left.id);
      const rightFeature = sessionFeatures.find((feature) => feature.key === right.id);
      const leftPref = prefMap.get(left.id);
      const rightPref = prefMap.get(right.id);
      const leftOrder =
        left.id === 'diffs' ? 25 : leftPref ? leftPref.sortOrder : (leftFeature?.order ?? 0);
      const rightOrder =
        right.id === 'diffs' ? 25 : rightPref ? rightPref.sortOrder : (rightFeature?.order ?? 0);
      return leftOrder - rightOrder;
    });

    return visible;
  }, [featurePrefs, sessionFeatures]);

  useEffect(() => {
    setTabWasManuallySelected(false);
    setActiveTab('chat');
  }, [sessionId]);

  useEffect(() => {
    if (!tabs.some((tab) => tab.id === activeTab)) {
      setActiveTab(tabs.find((tab) => tab.id === 'chat')?.id ?? tabs[0]?.id ?? 'chat');
      return;
    }
    if (!tabWasManuallySelected && tabs.some((tab) => tab.id === 'chat') && activeTab !== 'chat') {
      setActiveTab('chat');
    }
  }, [activeTab, tabWasManuallySelected, tabs]);

  const permissionRenderer = useMemo(
    () =>
      chat.pendingPermissions.length > 0
        ? (
            permissions: typeof chat.pendingPermissions,
            onRespond: (requestId: string, behavior: PermissionBehavior) => void,
          ) => (
            <div className="niuu-flex niuu-flex-col niuu-gap-2 niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-3">
              {permissions.map((permission) => (
                <div
                  key={permission.requestId}
                  className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-3 niuu-text-xs"
                >
                  <div>
                    <div className="niuu-font-mono niuu-text-text-primary">
                      {permission.toolName}
                    </div>
                    <div className="niuu-text-text-muted">{permission.description}</div>
                  </div>
                  <div className="niuu-flex niuu-gap-2">
                    <button
                      type="button"
                      className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-px-2 niuu-py-1 niuu-text-text-secondary"
                      onClick={() => onRespond(permission.requestId, 'deny')}
                    >
                      deny
                    </button>
                    <button
                      type="button"
                      className="niuu-rounded-md niuu-border niuu-border-brand niuu-bg-brand niuu-px-2 niuu-py-1 niuu-text-bg-primary"
                      onClick={() => onRespond(permission.requestId, 'allow_once')}
                    >
                      allow
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )
        : undefined,
    [chat],
  );

  async function refreshSessionData() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['volundr', 'raw-session', sessionId] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'raw-session', 'logs', sessionId] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-session', sessionId] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'filetree', sessionId] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'session-list'] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'stats'] }),
    ]);
  }

  async function handleStopSession() {
    if (!liveSession || actionBusy) return;
    setActionBusy('stop');
    try {
      await volundr.stopSession(liveSession.id);
      await refreshSessionData();
    } finally {
      setActionBusy(null);
    }
  }

  async function handleResumeSession() {
    if (!liveSession || actionBusy) return;
    setActionBusy('start');
    try {
      await volundr.resumeSession(liveSession.id);
      await refreshSessionData();
    } finally {
      setActionBusy(null);
    }
  }

  async function handleDeleteSession(cleanup: string[]) {
    if (!liveSession || actionBusy) return;
    setActionBusy('delete');
    try {
      await volundr.deleteSession(liveSession.id, cleanup);
      setDeleteDialogOpen(false);
      await refreshSessionData();
    } finally {
      setActionBusy(null);
    }
  }

  if (
    sessionQuery.isLoading ||
    liveSessionQuery.isLoading ||
    sessionFeaturesQuery.isLoading ||
    featurePrefsQuery.isLoading
  ) {
    return <LoadingState label="Loading session…" />;
  }

  if (
    sessionQuery.isError ||
    liveSessionQuery.isError ||
    sessionFeaturesQuery.isError ||
    featurePrefsQuery.isError
  ) {
    const error =
      sessionQuery.error ??
      liveSessionQuery.error ??
      sessionFeaturesQuery.error ??
      featurePrefsQuery.error;
    return (
      <ErrorState
        title="Failed to load session"
        message={error instanceof Error ? error.message : 'Unknown error'}
      />
    );
  }

  return (
    <div className="niuu-flex niuu-h-full niuu-flex-col" data-testid="live-session-detail-page">
      <div className="niuu-bg-bg-secondary">
        <div className="niuu-flex niuu-flex-nowrap niuu-items-center niuu-gap-2 niuu-overflow-x-auto niuu-px-4 niuu-py-4">
          <div className="niuu-flex niuu-min-w-0 niuu-flex-shrink-0 niuu-items-center niuu-gap-2">
            <span
              className={cn(
                'niuu-h-2.5 niuu-w-2.5 niuu-flex-shrink-0 niuu-rounded-full',
                statusDotClass(liveSession?.status),
              )}
            />
            <span className="niuu-max-w-[240px] niuu-truncate niuu-font-mono niuu-text-[28px] niuu-text-text-primary">
              {sessionName}
            </span>
            {liveSession?.trackerIssue ? (
              <>
                <TicketLink issue={liveSession?.trackerIssue} />
                <HeaderDivider />
              </>
            ) : null}
            {renderSourceMeta(liveSession)}
            <HeaderDivider />
            <span className="niuu-inline-flex niuu-flex-shrink-0 niuu-items-center niuu-gap-2 niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-px-2.5 niuu-py-1 niuu-font-mono niuu-text-[11px] niuu-text-text-secondary">
              {modelLabel}
            </span>
            <HeaderDivider />
            <SessionIdChip sessionId={sessionId} />
            {readOnly && (
              <>
                <HeaderDivider />
                <span className="niuu-rounded-full niuu-bg-bg-elevated niuu-px-3 niuu-py-1 niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
                  Archived
                </span>
              </>
            )}
          </div>

          <div className="niuu-ml-auto niuu-flex niuu-flex-shrink-0 niuu-items-center niuu-gap-2">
            <HeaderMetric label="Active" value={formatRelativeAge(liveSession?.lastActive)} />
            <HeaderDivider />
            <HeaderMetric label="Msgs" value={formatCount(liveSession?.messageCount ?? 0)} />
            <HeaderDivider />
            <HeaderMetric label="Tokens" value={formatCount(liveSession?.tokensUsed ?? 0)} />
            <HeaderDivider />
            <HeaderMetric label="Mode" value={liveSession?.taskType ?? 'forge'} />
          </div>
        </div>
        <div
          aria-hidden="true"
          style={{
            height: '2px',
            background:
              'linear-gradient(to right, rgba(255,255,255,0.08), rgba(255,255,255,0.24), rgba(255,255,255,0.08))',
          }}
        />
      </div>

      <div
        className="niuu-flex niuu-flex-nowrap niuu-items-center niuu-gap-0 niuu-overflow-x-auto niuu-bg-bg-secondary niuu-px-3"
        role="tablist"
        aria-label="Session tabs"
      >
        <div className="niuu-flex niuu-items-center niuu-gap-1">
          {tabs.map((tab) =>
            (() => {
              const TabIcon = tab.icon;
              return (
                <button
                  key={tab.id}
                  id={`tab-${tab.id}`}
                  role="tab"
                  aria-selected={activeTab === tab.id}
                  onClick={() => {
                    setTabWasManuallySelected(true);
                    setActiveTab(tab.id);
                  }}
                  className={
                    activeTab === tab.id
                      ? 'niuu-flex niuu-items-center niuu-gap-2 niuu-border-b-2 niuu-border-brand niuu-px-3 niuu-py-2.5 niuu-font-mono niuu-text-[13px] niuu-font-medium niuu-text-brand'
                      : 'niuu-flex niuu-items-center niuu-gap-2 niuu-border-b-2 niuu-border-transparent niuu-px-3 niuu-py-2.5 niuu-font-mono niuu-text-[13px] niuu-text-text-muted hover:niuu-text-text-secondary'
                  }
                >
                  <TabIcon className="niuu-h-4 niuu-w-4" />
                  {tab.label}
                </button>
              );
            })(),
          )}
        </div>
        {!readOnly && liveSession && (
          <div className="niuu-ml-auto niuu-flex niuu-flex-shrink-0 niuu-items-center niuu-gap-2 niuu-pr-1">
            {liveSession.status === 'running' ? (
              <IconActionButton
                label={actionBusy === 'stop' ? '■ stopping' : '■ stop'}
                title="Stop session"
                onClick={() => void handleStopSession()}
                disabled={actionBusy !== null}
                tone="critical"
              />
            ) : (
              <IconActionButton
                label={actionBusy === 'start' ? '▶ starting' : '▶ start'}
                title="Start session"
                onClick={() => void handleResumeSession()}
                disabled={actionBusy !== null}
                tone="brand"
              />
            )}
            <IconActionButton
              label="⌫ delete"
              title="Delete session"
              onClick={() => setDeleteDialogOpen(true)}
              disabled={actionBusy !== null}
              tone="neutral"
            />
          </div>
        )}
      </div>

      <div
        aria-hidden="true"
        style={{
          height: '2px',
          background:
            'linear-gradient(to right, rgba(255,255,255,0.08), rgba(255,255,255,0.24), rgba(255,255,255,0.08))',
        }}
      />

      <div className="niuu-min-h-0 niuu-flex-1 niuu-overflow-hidden">
        {activeTab === 'chat' && (
          <div role="tabpanel" className="niuu-flex niuu-h-full niuu-min-h-0 niuu-flex-col">
            {isReady && chatEndpoint ? (
              <SessionChat
                className="niuu-h-full"
                messages={chat.messages}
                streamingContent={chat.streamingContent}
                streamingParts={chat.streamingParts}
                streamingModel={chat.streamingModel}
                connected={chat.connected}
                historyLoaded={chat.historyLoaded}
                participants={chat.participants}
                meshEvents={chat.meshEvents}
                agentEvents={chat.agentEvents}
                pendingPermissions={chat.pendingPermissions}
                capabilities={chat.capabilities}
                chatEndpoint={chatEndpoint}
                sessionName={sessionName}
                onSend={chat.sendMessage}
                onSendDirected={chat.sendDirectedMessages}
                onStop={chat.sendInterrupt}
                onClear={chat.clearMessages}
                onSetModel={chat.sendSetModel}
                onSetThinkingTokens={chat.sendSetThinkingTokens}
                onRewindFiles={chat.sendRewindFiles}
                onPermissionRespond={chat.respondToPermission}
                renderPermissions={permissionRenderer}
              />
            ) : isSessionBooting(sessionStatus) ? (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
                Session is starting…
              </div>
            ) : (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
                Start the session to chat.
              </div>
            )}
          </div>
        )}

        {activeTab === 'terminal' && (
          <div role="tabpanel" className="niuu-h-full niuu-min-h-0">
            {isReady ? (
              <SessionTerminalLive url={terminalUrl} readOnly={readOnly} />
            ) : isSessionBooting(sessionStatus) ? (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
                Session is starting…
              </div>
            ) : (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
                Start the session to access terminal.
              </div>
            )}
          </div>
        )}

        {activeTab === 'diffs' && (
          <div role="tabpanel" className="niuu-h-full niuu-min-h-0">
            <LiveDiffsTab chatEndpoint={chatEndpoint} />
          </div>
        )}

        {activeTab === 'files' && (
          <div role="tabpanel" className="niuu-h-full niuu-min-h-0">
            <SessionFilesWorkspace sessionId={sessionId} filesystem={filesystem} />
          </div>
        )}

        {activeTab === 'chronicles' && (
          <div role="tabpanel" className="niuu-h-full niuu-min-h-0">
            <LiveChroniclesTab
              sessionId={sessionId}
              sessionStatus={sessionStatus}
              session={liveSession ?? null}
              volundr={volundr}
            />
          </div>
        )}

        {activeTab === 'logs' && (
          <div role="tabpanel" className="niuu-h-full niuu-min-h-0">
            <LiveLogsTab sessionId={sessionId} volundr={volundr} />
          </div>
        )}
      </div>

      <DeleteSessionDialog
        open={deleteDialogOpen}
        session={liveSession ?? null}
        onClose={() => setDeleteDialogOpen(false)}
        onConfirm={(cleanup) => void handleDeleteSession(cleanup)}
        busy={actionBusy === 'delete'}
      />
    </div>
  );
}
