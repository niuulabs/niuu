import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useService } from '@niuulabs/plugin-sdk';
import { getAuthHeaders } from '@niuulabs/query';
import {
  Dialog,
  DialogContent,
  ErrorState,
  LoadingState,
  SessionChat,
  cn,
  type MeshNotificationEvent,
  type PermissionBehavior,
} from '@niuulabs/ui';
import {
  Archive,
  AlertTriangle,
  Check,
  Eye,
  EyeOff,
  ExternalLink,
  FileCode2,
  FileDiff,
  FilePenLine,
  FolderOpen,
  GitCommitHorizontal,
  MessageSquareText,
  Play,
  RotateCcw,
  ScrollText,
  Square,
  SquareTerminal,
  Trash2,
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
import {
  meshEventsFromTurns,
  participantsFromTurns,
  transformTurns,
  useSkuldChat,
} from './hooks/useSkuldChat';
import { deriveTerminalWsUrl, normalizeSessionUrl, wsUrlToHttpBase } from './liveSessionTransport';
import { SessionTerminalLive } from './SessionTerminalLive';
import { StructuredLogViewer } from './components/StructuredLogViewer';
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

function formatCount(value: number): string {
  if (!Number.isFinite(value)) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}m`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}k`;
  return `${value}`;
}

function formatElapsedSince(value?: string | null): string {
  if (!value) return '—';
  const startedAt = new Date(value).getTime();
  if (!Number.isFinite(startedAt)) return '—';
  const delta = Math.max(0, Date.now() - startedAt);
  const minutes = Math.floor(delta / 60_000);
  if (minutes < 1) return '<1m';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

function formatCurrencyCents(value?: number): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `$${(value / 100).toFixed(2)}`;
}

function formatEventTime(value: number): string {
  const minutes = Math.floor(value / 60);
  const seconds = value % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function sessionForgeLabel(session: VolundrSession | null | undefined): string {
  return session?.instanceName ?? session?.instanceId ?? session?.hostname ?? 'shared';
}

function fileChangeCount(
  files?: {
    added: number;
    modified: number;
    deleted: number;
  } | null,
): number | undefined {
  if (!files) return undefined;
  return files.added + files.modified + files.deleted;
}

function WorkflowHumanGateCard({
  title,
  summary,
  reason,
  recommendation,
  onApprove,
  onRequestChanges,
}: {
  title: string;
  summary: string;
  reason?: string;
  recommendation?: string;
  onApprove: (notes: string) => void;
  onRequestChanges: (notes: string) => void;
}) {
  const [notes, setNotes] = useState('');

  return (
    <section className="niuu-m-3 niuu-mb-0 niuu-rounded-xl niuu-border niuu-border-amber-500/35 niuu-bg-amber-500/8 niuu-p-4 niuu-shadow-[0_18px_40px_-24px_rgba(245,158,11,0.45)]">
      <div className="niuu-flex niuu-items-start niuu-gap-3">
        <div className="niuu-mt-0.5 niuu-rounded-full niuu-bg-amber-500/16 niuu-p-2 niuu-text-amber-300">
          <AlertTriangle className="niuu-h-4 niuu-w-4" />
        </div>
        <div className="niuu-flex-1 niuu-space-y-3">
          <div className="niuu-space-y-1">
            <div className="niuu-text-[11px] niuu-font-mono niuu-uppercase niuu-tracking-[0.08em] niuu-text-amber-200/80">
              Human Gate Requested
            </div>
            <h3 className="niuu-text-[15px] niuu-font-semibold niuu-text-text-primary">{title}</h3>
            <p className="niuu-text-sm niuu-leading-6 niuu-text-text-secondary">{summary}</p>
          </div>
          {(reason || recommendation) && (
            <dl className="niuu-grid niuu-gap-2 niuu-rounded-lg niuu-border niuu-border-white/8 niuu-bg-black/10 niuu-p-3">
              {reason ? (
                <div className="niuu-grid niuu-gap-1">
                  <dt className="niuu-text-[11px] niuu-font-mono niuu-uppercase niuu-tracking-[0.08em] niuu-text-text-muted">
                    Reason
                  </dt>
                  <dd className="niuu-text-sm niuu-text-text-secondary">{reason}</dd>
                </div>
              ) : null}
              {recommendation ? (
                <div className="niuu-grid niuu-gap-1">
                  <dt className="niuu-text-[11px] niuu-font-mono niuu-uppercase niuu-tracking-[0.08em] niuu-text-text-muted">
                    Recommendation
                  </dt>
                  <dd className="niuu-text-sm niuu-text-text-secondary">{recommendation}</dd>
                </div>
              ) : null}
            </dl>
          )}
          <div className="niuu-space-y-2">
            <label
              htmlFor="workflow-human-gate-notes"
              className="niuu-text-[11px] niuu-font-mono niuu-uppercase niuu-tracking-[0.08em] niuu-text-text-muted"
            >
              Reply Notes
            </label>
            <textarea
              id="workflow-human-gate-notes"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              rows={4}
              placeholder="Optional context for the gate decision."
              className="niuu-w-full niuu-rounded-lg niuu-border niuu-border-border niuu-bg-bg-primary niuu-px-3 niuu-py-2.5 niuu-text-sm niuu-text-text-primary placeholder:niuu-text-text-muted"
            />
          </div>
          <div className="niuu-flex niuu-flex-wrap niuu-items-center niuu-justify-between niuu-gap-3">
            <p className="niuu-text-xs niuu-text-text-muted">
              This reply resolves the pending workflow gate and resumes the workflow.
            </p>
            <div className="niuu-flex niuu-flex-wrap niuu-gap-2">
              <button
                type="button"
                onClick={() => {
                  onRequestChanges(notes.trim());
                  setNotes('');
                }}
                className="niuu-inline-flex niuu-items-center niuu-gap-2 niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-primary niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-primary hover:niuu-bg-bg-secondary"
              >
                <FilePenLine className="niuu-h-4 niuu-w-4" />
                Request changes
              </button>
              <button
                type="button"
                onClick={() => {
                  onApprove(notes.trim());
                  setNotes('');
                }}
                className="niuu-inline-flex niuu-items-center niuu-gap-2 niuu-rounded-md niuu-bg-brand niuu-px-3 niuu-py-2 niuu-text-sm niuu-font-medium niuu-text-bg-primary hover:niuu-opacity-90"
              >
                <Check className="niuu-h-4 niuu-w-4" />
                Approve
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
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

function normalizeRepoLink(source: VolundrSession['source'] | null | undefined) {
  if (!source || source.type !== 'git' || !source.repo) return null;
  if (source.repo.startsWith('http://') || source.repo.startsWith('https://')) return source.repo;
  return `https://github.com/${source.repo}`;
}

function formatRepoLabel(repo: string): string {
  const trimmed = repo.replace(/\.git$/, '');
  const sshMatch = trimmed.match(/^[^@]+@([^:]+):(.+)$/);
  if (sshMatch) return sshMatch[2] ?? trimmed;
  if (/^[^/]+\/[^/]+$/.test(trimmed)) return trimmed;
  const candidate =
    trimmed.startsWith('http://') || trimmed.startsWith('https://')
      ? trimmed
      : `https://${trimmed}`;
  try {
    const url = new URL(candidate);
    const path = url.pathname.replace(/^\/+/, '');
    if (!path) return trimmed;
    if (['github.com', 'gitlab.com', 'bitbucket.org'].includes(url.hostname)) return path;
    return `${url.hostname}/${path}`;
  } catch {
    return trimmed;
  }
}

function truncateMiddle(value: string, maxLength = 30): string {
  if (value.length <= maxLength) return value;
  const start = Math.ceil((maxLength - 3) * 0.6);
  const end = Math.floor((maxLength - 3) * 0.4);
  return `${value.slice(0, start)}...${value.slice(-end)}`;
}

function titleCaseWord(value: string): string {
  if (!value) return value;
  return `${value.slice(0, 1).toUpperCase()}${value.slice(1)}`;
}

function normalizeForgeBadgeLabel(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return trimmed;
  if (/^local-[a-f0-9]{6,}$/i.test(trimmed)) return 'Local';
  return titleCaseWord(trimmed);
}

function SourceMeta({ session }: { session: VolundrSession | null | undefined }) {
  const [branchCopied, setBranchCopied] = useState(false);

  if (!session?.source) return null;

  if (session.source.type === 'git') {
    const repoUrl = normalizeRepoLink(session.source);
    const repoLabel = formatRepoLabel(session.source.repo);
    const branch = session.source.branch ?? 'main';

    return (
      <span className="niuu-live-session__source">
        <span className="niuu-text-text-faint" aria-hidden>
          ›
        </span>
        {repoUrl ? (
          <a
            href={repoUrl}
            target="_blank"
            rel="noreferrer"
            className="niuu-live-session__source-link"
            title={repoUrl}
          >
            {repoLabel}
          </a>
        ) : (
          <span className="niuu-live-session__source-link" title={session.source.repo}>
            {repoLabel}
          </span>
        )}
        <button
          type="button"
          className="niuu-live-session__branch-button"
          title={`${branchCopied ? 'Copied' : branch} · click to copy`}
          onClick={async () => {
            const copied = await copyText(branch);
            setBranchCopied(copied);
            if (copied) {
              setTimeout(() => setBranchCopied(false), 1200);
            }
          }}
        >
          {`@${truncateMiddle(branch, 18)}`}
        </button>
      </span>
    );
  }

  const path = session.source.path ?? 'local mount';
  return (
    <span className="niuu-live-session__source">
      <span className="niuu-text-text-faint" aria-hidden>
        ›
      </span>
      <span className="niuu-live-session__source-link" title={path}>
        {path}
      </span>
    </span>
  );
}

function HeaderMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="niuu-live-session__metric">
      <span className="niuu-live-session__metric-label">{label}</span>
      <span className="niuu-live-session__metric-value">{value}</span>
    </div>
  );
}

function HeaderDivider() {
  return <span className="niuu-live-session__divider" aria-hidden="true" />;
}

function HeaderActionButton({
  label,
  onClick,
  disabled,
  tone = 'neutral',
  className,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: 'neutral' | 'critical' | 'brand';
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

function TabCountBadge({ count }: { count?: number }) {
  if (count == null || count <= 0) return null;
  return <span className="niuu-live-session__tab-count">{formatCount(count)}</span>;
}

function SessionToolbarButton({
  icon: Icon,
  title,
  onClick,
  disabled,
  tone = 'neutral',
  active = false,
  label,
}: {
  icon: typeof Eye;
  title: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: 'neutral' | 'critical' | 'brand';
  active?: boolean;
  label?: string;
}) {
  const toneClass =
    tone === 'critical'
      ? 'niuu-live-session__toolbar-button--critical'
      : tone === 'brand'
        ? 'niuu-live-session__toolbar-button--brand'
        : 'niuu-live-session__toolbar-button--neutral';

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={title}
      title={title}
      aria-pressed={active || undefined}
      className={cn(
        'niuu-live-session__toolbar-button',
        active && 'niuu-live-session__toolbar-button--active',
        toneClass,
      )}
    >
      <Icon className="niuu-live-session__toolbar-icon" />
      {label ? <span className="niuu-live-session__toolbar-label">{label}</span> : null}
    </button>
  );
}

function SessionIdChip({ sessionId }: { sessionId: string }) {
  return (
    <button
      type="button"
      title={`${sessionId} · click to copy`}
      aria-label="Copy session ID"
      data-testid="session-id-label"
      onClick={async () => {
        await copyText(sessionId);
      }}
      className="niuu-live-session__session-id"
    >
      {sessionId}
    </button>
  );
}

function TicketLink({ issue }: { issue: VolundrSession['trackerIssue'] }) {
  if (!issue) return null;
  if (!issue.url) {
    return (
      <span className="niuu-live-session__ticket niuu-live-session__ticket--static" title={issue.identifier}>
        <span>{issue.identifier}</span>
      </span>
    );
  }
  return (
    <a
      href={issue.url}
      target="_blank"
      rel="noreferrer"
      className="niuu-live-session__ticket"
      title={issue.identifier}
    >
      <span>{issue.identifier}</span>
      <ExternalLink className="niuu-h-3.5 niuu-w-3.5" />
    </a>
  );
}

function SessionForgeBadge({
  label,
  tone,
}: {
  label: string;
  tone?: string;
}) {
  return (
    <span className="niuu-live-session__forge-badge">
      <span className="niuu-live-session__forge-badge-label">{label}</span>
      <span className="niuu-live-session__forge-badge-tone">{tone ?? 'PRIMARY'}</span>
    </span>
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

  return (
    <div className="niuu-flex niuu-h-full niuu-flex-col" data-testid="live-logs-tab">
      <StructuredLogViewer
        logs={logs.lines}
        participants={participants}
        loading={loading}
        initialLevel={level as 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'}
        level={level as 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'}
        onLevelChange={setLevel}
        downloadFilename={`${sessionId}-logs.log`}
        toolbarTestId="live-logs-toolbar"
      />
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
        {sessionStatus === 'running' ? 'No chronicle data yet.' : 'No saved chronicle yet.'}
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
  return Object.fromEntries(getAuthHeaders().entries());
}

function useLiveDiffViewer(chatEndpoint: string | null) {
  const apiBase = useMemo(
    () => (chatEndpoint ? wsUrlToHttpBase(chatEndpoint) : null),
    [chatEndpoint],
  );
  const mountedRef = useRef(true);
  const filesAbortRef = useRef<AbortController | null>(null);
  const diffAbortRef = useRef<AbortController | null>(null);
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [diff, setDiff] = useState<DiffData | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<Error | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [diffBase, setDiffBaseState] = useState<DiffBase>('last-commit');

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      filesAbortRef.current?.abort();
      diffAbortRef.current?.abort();
    };
  }, []);

  const fetchFiles = useCallback(async () => {
    filesAbortRef.current?.abort();
    if (!apiBase) {
      if (mountedRef.current) {
        setFiles([]);
        setFilesLoading(false);
      }
      return;
    }
    const controller = new AbortController();
    filesAbortRef.current = controller;
    setFilesLoading(true);
    try {
      const params = new URLSearchParams({ base: diffBase });
      const response = await fetch(`${apiBase}/api/diff/files?${params}`, {
        headers: authHeaders(),
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch diff files: ${response.status}`);
      }
      const data = (await response.json()) as { files?: SessionFile[] };
      if (!mountedRef.current || filesAbortRef.current !== controller) return;
      setFiles(data.files ?? []);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      if (!mountedRef.current || filesAbortRef.current !== controller) return;
      setFiles([]);
    } finally {
      if (mountedRef.current && filesAbortRef.current === controller) {
        filesAbortRef.current = null;
        setFilesLoading(false);
      }
    }
  }, [apiBase, diffBase]);

  const selectFile = useCallback(
    async (filePath: string) => {
      diffAbortRef.current?.abort();
      if (!apiBase) {
        setSelectedFile(filePath);
        setDiff(null);
        setDiffError(new Error('No session endpoint available'));
        return;
      }
      const controller = new AbortController();
      diffAbortRef.current = controller;
      setSelectedFile(filePath);
      setDiffLoading(true);
      setDiffError(null);
      try {
        const params = new URLSearchParams({ file: filePath, base: diffBase });
        const response = await fetch(`${apiBase}/api/diff?${params}`, {
          headers: authHeaders(),
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Failed to fetch diff: ${response.status}`);
        }
        if (!mountedRef.current || diffAbortRef.current !== controller) return;
        setDiff((await response.json()) as DiffData);
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        if (!mountedRef.current || diffAbortRef.current !== controller) return;
        setDiff(null);
        setDiffError(error instanceof Error ? error : new Error('Failed to fetch diff'));
      } finally {
        if (mountedRef.current && diffAbortRef.current === controller) {
          diffAbortRef.current = null;
          setDiffLoading(false);
        }
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
  const [actionBusy, setActionBusy] = useState<
    'start' | 'stop' | 'archive' | 'restore' | 'delete' | null
  >(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [dismissedHumanGateIds, setDismissedHumanGateIds] = useState<Set<string>>(new Set());
  const [showInternalMessages, setShowInternalMessages] = useState(false);
  const [visibleMessageCount, setVisibleMessageCount] = useState<number | null>(null);
  const volundr = useService<IVolundrService>('volundr');
  const filesystem = useService<IFileSystemPort>('filesystem');
  const navigate = useNavigate();
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
  const liveSession = liveSessionQuery.data;
  const sessionStatus = liveSession?.status ?? null;
  const isRunning = sessionStatus === 'running';
  const chatEndpoint = normalizeSessionUrl(liveSession?.chatEndpoint ?? null);
  const isReady = isRunning && Boolean(chatEndpoint);
  const canReplayTranscript =
    readOnly ||
    sessionStatus === 'stopped' ||
    sessionStatus === 'archived' ||
    sessionStatus === 'failed' ||
    sessionStatus === 'error';
  const terminalUrl = deriveTerminalWsUrl(chatEndpoint);
  const chat = useSkuldChat(chatEndpoint);
  const workflowGatesQuery = useQuery({
    queryKey: ['volundr', 'workflow-gates', sessionId],
    queryFn: () => volundr.getWorkflowGates(sessionId),
    enabled: Boolean(sessionId) && isRunning,
    refetchInterval: isRunning ? 5_000 : false,
  });
  const transcriptQuery = useQuery({
    queryKey: ['volundr', 'conversation-history', sessionId],
    queryFn: () => volundr.getConversationHistory(sessionId),
    enabled: Boolean(sessionId) && canReplayTranscript,
    staleTime: 5_000,
  });
  const sessionName = liveSession?.name ?? sessionQuery.data?.personaName ?? sessionId;
  const sessionTrackerIssue = liveSession?.trackerIssue;
  const sessionHandle = useMemo(() => {
    const rawHandle = sessionQuery.data?.ravnId?.trim();
    if (!rawHandle) return null;
    const normalizedHandle = rawHandle.toLowerCase();
    if (
      normalizedHandle === sessionId.toLowerCase() ||
      normalizedHandle === sessionName.trim().toLowerCase() ||
      normalizedHandle === sessionTrackerIssue?.identifier.trim().toLowerCase()
    ) {
      return null;
    }
    return rawHandle;
  }, [sessionId, sessionName, sessionQuery.data?.ravnId, sessionTrackerIssue?.identifier]);
  const forgeBadgeLabel = useMemo(() => {
    const clusterName = sessionQuery.data?.clusterName?.trim();
    if (clusterName) return clusterName;
    const clusterId = sessionQuery.data?.clusterId?.trim();
    if (clusterId && clusterId !== 'local') return normalizeForgeBadgeLabel(clusterId);
    const instanceName = liveSession?.instanceName?.trim();
    if (instanceName) return normalizeForgeBadgeLabel(instanceName);
    const instanceId = liveSession?.instanceId?.trim();
    if (instanceId) return normalizeForgeBadgeLabel(instanceId);
    return null;
  }, [liveSession?.instanceId, liveSession?.instanceName, sessionQuery.data?.clusterId, sessionQuery.data?.clusterName]);
  const sessionFeatures = useMemo(
    () => sessionFeaturesQuery.data ?? [],
    [sessionFeaturesQuery.data],
  );
  const featurePrefs = useMemo(() => featurePrefsQuery.data ?? [], [featurePrefsQuery.data]);
  const transcriptTurns = useMemo(() => transcriptQuery.data?.turns ?? [], [transcriptQuery.data]);
  const replayMessages = useMemo(() => transformTurns(transcriptTurns), [transcriptTurns]);
  const replayParticipants = useMemo(
    () => participantsFromTurns(transcriptTurns),
    [transcriptTurns],
  );
  const replayMeshEvents = useMemo(() => meshEventsFromTurns(transcriptTurns), [transcriptTurns]);
  const workflowGates = useMemo(() => workflowGatesQuery.data ?? [], [workflowGatesQuery.data]);
  const activeHumanGate = useMemo(() => {
    const nativeGate = workflowGates.find(
      (gate) =>
        gate.status === 'pending' &&
        gate.pending_behavior === 'help_needed' &&
        !dismissedHumanGateIds.has(gate.id),
    );
    if (nativeGate) {
      return {
        source: 'native' as const,
        eventId: nativeGate.id,
        gate: nativeGate,
        summary:
          nativeGate.summary ||
          `${nativeGate.label} is waiting for human approval before the workflow can continue.`,
        reason: nativeGate.condition || undefined,
        recommendation: 'Approve to continue or request changes to send the workflow back.',
      };
    }

    const latest = [...chat.meshEvents]
      .reverse()
      .find(
        (event): event is MeshNotificationEvent =>
          event.type === 'notification' && event.notificationType === 'help_needed',
      );
    if (!latest) return null;
    const eventId =
      latest.id ||
      [
        latest.participantId,
        latest.type,
        latest.notificationType,
        latest.timestamp.toISOString(),
      ].join(':');
    if (dismissedHumanGateIds.has(eventId)) return null;
    const participant = chat.participants.get(latest.participantId);
    if (!participant) return null;
    return {
      source: 'legacy' as const,
      eventId,
      event: {
        summary: latest.summary || 'Workflow gate is waiting for human approval.',
        reason: latest.reason,
        recommendation: latest.recommendation,
        persona: latest.persona,
      },
      participant: {
        peerId: participant.peerId,
        displayName: participant.displayName,
        persona: participant.persona,
        participantType: participant.participantType,
      },
    };
  }, [chat.meshEvents, chat.participants, dismissedHumanGateIds, workflowGates]);
  const sessionDetail = sessionQuery.data;
  const uptimeValue = formatElapsedSince(sessionDetail?.startedAt);
  const costValue =
    sessionDetail?.costCents != null ? formatCurrencyCents(sessionDetail.costCents) : null;
  const trailingMetric = useMemo(() => {
    if (costValue) {
      return { label: 'Cost', value: costValue };
    }
    if (forgeBadgeLabel) {
      return null;
    }
    return { label: 'Forge', value: sessionForgeLabel(liveSession) };
  }, [costValue, forgeBadgeLabel, liveSession]);
  const tabCounts = useMemo<Partial<Record<SessionTab, number>>>(
    () => ({
      chat: liveSession?.messageCount || replayMessages.length || undefined,
      diffs: fileChangeCount(sessionDetail?.files),
      chronicles: sessionDetail?.events.length ? sessionDetail.events.length : undefined,
    }),
    [liveSession?.messageCount, replayMessages.length, sessionDetail?.events, sessionDetail?.files],
  );
  const headerMessageCount = useMemo(() => {
    if (visibleMessageCount != null) return visibleMessageCount;
    if (canReplayTranscript) return replayMessages.length;
    return liveSession?.messageCount ?? 0;
  }, [canReplayTranscript, liveSession?.messageCount, replayMessages.length, visibleMessageCount]);
  const isSessionConnected = isReady && chat.connected;

  const tabs = useMemo(() => {
    const prefMap = new Map(featurePrefs.map((pref) => [pref.featureKey, pref]));
    const visible = ALL_TABS.filter((tab) => {
      if (tab.id === 'diffs') return true;
      if (tab.id === 'terminal') {
        const pref = prefMap.get(tab.id);
        if (pref && !pref.visible) return false;
        const feature = sessionFeatures.find((candidate) => candidate.key === tab.id);
        return Boolean(feature?.enabled || terminalUrl);
      }
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
  }, [featurePrefs, sessionFeatures, terminalUrl]);

  useEffect(() => {
    setTabWasManuallySelected(false);
    setActiveTab('chat');
    setDismissedHumanGateIds(new Set());
    setShowInternalMessages(false);
    setVisibleMessageCount(null);
  }, [sessionId]);

  const handleToggleInternalMessages = useCallback(() => {
    const next = !showInternalMessages;
    setShowInternalMessages(next);
    if (isReady) {
      chat.sendSetInternalVisibility(next);
    }
  }, [chat, isReady, showInternalMessages]);

  const handleHumanGateReply = useCallback(
    (decision: 'APPROVE' | 'CHANGES_REQUESTED', notes: string) => {
      if (!activeHumanGate) return;
      setDismissedHumanGateIds((current) => {
        const next = new Set(current);
        next.add(activeHumanGate.eventId);
        return next;
      });
      if (activeHumanGate.source === 'native') {
        void volundr
          .resolveWorkflowGate(sessionId, activeHumanGate.gate.id, {
            decision,
            notes,
            source: 'human',
          })
          .then(() =>
            queryClient.invalidateQueries({
              queryKey: ['volundr', 'workflow-gates', sessionId],
            }),
          );
        return;
      }
      void chat.sendDirectedMessages(
        [activeHumanGate.participant],
        notes ? `${decision}\n\n${notes}` : decision,
        [],
      );
    },
    [activeHumanGate, chat, queryClient, sessionId, volundr],
  );

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
      queryClient.invalidateQueries({ queryKey: ['volundr', 'conversation-history', sessionId] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-session', sessionId] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'history'] }),
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
      await Promise.all([
        queryClient.cancelQueries({ queryKey: ['volundr', 'raw-session', sessionId] }),
        queryClient.cancelQueries({ queryKey: ['volundr', 'raw-session', 'logs', sessionId] }),
        queryClient.cancelQueries({ queryKey: ['volundr', 'conversation-history', sessionId] }),
        queryClient.cancelQueries({ queryKey: ['volundr', 'domain-session', sessionId] }),
        queryClient.cancelQueries({ queryKey: ['volundr', 'filetree', sessionId] }),
        queryClient.cancelQueries({ queryKey: ['volundr', 'workflow-gates', sessionId] }),
      ]);
      await volundr.deleteSession(liveSession.id, cleanup);
      setDeleteDialogOpen(false);
      queryClient.removeQueries({ queryKey: ['volundr', 'raw-session', sessionId] });
      queryClient.removeQueries({ queryKey: ['volundr', 'raw-session', 'logs', sessionId] });
      queryClient.removeQueries({ queryKey: ['volundr', 'conversation-history', sessionId] });
      queryClient.removeQueries({ queryKey: ['volundr', 'domain-session', sessionId] });
      queryClient.removeQueries({ queryKey: ['volundr', 'filetree', sessionId] });
      queryClient.removeQueries({ queryKey: ['volundr', 'workflow-gates', sessionId] });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'history'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'session-list'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'stats'] }),
      ]);
      await navigate({ to: '/volundr/sessions', replace: true });
    } finally {
      setActionBusy(null);
    }
  }

  async function handleArchiveSession() {
    if (!liveSession || actionBusy) return;
    setActionBusy('archive');
    try {
      await volundr.archiveSession(liveSession.id);
      await refreshSessionData();
    } finally {
      setActionBusy(null);
    }
  }

  async function handleRestoreSession() {
    if (!liveSession || actionBusy) return;
    setActionBusy('restore');
    try {
      await volundr.restoreSession(liveSession.id);
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
      <div className="niuu-live-session__chrome">
        <div className="niuu-live-session__header">
          <div className="niuu-live-session__title-group">
            <span
              className={cn(
                'niuu-live-session__status-dot',
                isSessionConnected
                  ? 'niuu-live-session__status-dot--connected'
                  : 'niuu-live-session__status-dot--disconnected',
              )}
            />
            <div className="niuu-live-session__identity">
              <span className="niuu-live-session__title">{sessionName}</span>
              {sessionHandle ? (
                <span className="niuu-live-session__handle" title={sessionHandle}>
                  {sessionHandle}
                </span>
              ) : null}
              <SessionIdChip sessionId={sessionId} />
            </div>
            {sessionTrackerIssue ? (
              <>
                <HeaderDivider />
                <TicketLink issue={sessionTrackerIssue} />
              </>
            ) : null}
            {liveSession?.source ? (
              <>
                <HeaderDivider />
                <SourceMeta session={liveSession} />
              </>
            ) : null}
            {forgeBadgeLabel ? (
              <>
                <HeaderDivider />
                <SessionForgeBadge label={forgeBadgeLabel} />
              </>
            ) : null}
            {readOnly ? (
              <>
                <HeaderDivider />
                <span className="niuu-live-session__archived-badge">Archived</span>
              </>
            ) : null}
          </div>

          <div className="niuu-live-session__metrics-row" data-testid="session-stats">
            <HeaderMetric label="Uptime" value={uptimeValue} />
            <HeaderDivider />
            <HeaderMetric label="Msgs" value={formatCount(headerMessageCount)} />
            <HeaderDivider />
            <HeaderMetric label="Tokens" value={formatCount(liveSession?.tokensUsed ?? 0)} />
            {trailingMetric ? (
              <>
                <HeaderDivider />
                <HeaderMetric label={trailingMetric.label} value={trailingMetric.value} />
              </>
            ) : null}
          </div>
        </div>

        <div className="niuu-live-session__tabs-row">
          <div className="niuu-live-session__tabs" role="tablist" aria-label="Session tabs">
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
                    className={cn(
                      'niuu-live-session__tab',
                      activeTab === tab.id && 'niuu-live-session__tab--active',
                    )}
                  >
                    <TabIcon className="niuu-live-session__tab-icon" />
                    <span>{tab.label}</span>
                    <TabCountBadge count={tabCounts[tab.id]} />
                  </button>
                );
              })(),
            )}
          </div>
          <div className="niuu-live-session__toolbar">
            <SessionToolbarButton
              icon={showInternalMessages ? Eye : EyeOff}
              title={
                showInternalMessages
                  ? 'Hide tool calls and results'
                  : 'Show tool calls and results'
              }
              active={showInternalMessages}
              onClick={handleToggleInternalMessages}
            />
            {liveSession &&
              !readOnly &&
              (liveSession.status === 'running' ? (
                <SessionToolbarButton
                  icon={Square}
                  title="Stop session"
                  onClick={() => void handleStopSession()}
                  disabled={actionBusy !== null}
                  tone="critical"
                />
              ) : liveSession.status === 'stopped' ? (
                <SessionToolbarButton
                  icon={Play}
                  title="Start session"
                  onClick={() => void handleResumeSession()}
                  disabled={actionBusy !== null}
                  tone="brand"
                />
              ) : liveSession.status === 'archived' ? null : (
                <SessionToolbarButton
                  icon={Play}
                  title="Start session"
                  onClick={() => void handleResumeSession()}
                  disabled={actionBusy !== null}
                  tone="brand"
                />
              ))}
            {!readOnly && liveSession && liveSession.status !== 'archived' && (
              <SessionToolbarButton
                icon={Archive}
                title="Archive session"
                onClick={() => void handleArchiveSession()}
                disabled={actionBusy !== null}
              />
            )}
            {liveSession?.status === 'archived' ? (
              <SessionToolbarButton
                icon={RotateCcw}
                title="Restore archived session"
                onClick={() => void handleRestoreSession()}
                disabled={actionBusy !== null}
                tone="brand"
              />
            ) : null}
            {!readOnly && (
              <SessionToolbarButton
                icon={Trash2}
                title="Delete session"
                onClick={() => setDeleteDialogOpen(true)}
                disabled={actionBusy !== null}
                tone="critical"
              />
            )}
          </div>
        </div>
      </div>

      <div className="niuu-min-h-0 niuu-flex-1 niuu-overflow-hidden">
        {activeTab === 'chat' && (
          <div role="tabpanel" className="niuu-flex niuu-h-full niuu-min-h-0 niuu-flex-col">
            {isReady && chatEndpoint ? (
              <>
                {activeHumanGate && (
                  <WorkflowHumanGateCard
                    title={
                      activeHumanGate.source === 'native'
                        ? activeHumanGate.gate.label
                        : activeHumanGate.participant.displayName ||
                          activeHumanGate.participant.persona ||
                          activeHumanGate.event.persona ||
                          'Workflow gate'
                    }
                    summary={
                      activeHumanGate.source === 'native'
                        ? activeHumanGate.summary
                        : activeHumanGate.event.summary || 'Workflow gate'
                    }
                    reason={
                      activeHumanGate.source === 'native'
                        ? activeHumanGate.reason
                        : activeHumanGate.event.reason
                    }
                    recommendation={
                      activeHumanGate.source === 'native'
                        ? activeHumanGate.recommendation
                        : activeHumanGate.event.recommendation
                    }
                    onApprove={(notes) => handleHumanGateReply('APPROVE', notes)}
                    onRequestChanges={(notes) => handleHumanGateReply('CHANGES_REQUESTED', notes)}
                  />
                )}
                <SessionChat
                  className="niuu-h-full"
                  showToolbar={false}
                  showInternalToggle={false}
                  internalVisibility={showInternalMessages}
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
                  onSetInternalVisibility={chat.sendSetInternalVisibility}
                  onSetModel={chat.sendSetModel}
                  onSetThinkingTokens={chat.sendSetThinkingTokens}
                  onRewindFiles={chat.sendRewindFiles}
                  onPermissionRespond={chat.respondToPermission}
                  onMessageCountChange={setVisibleMessageCount}
                  renderPermissions={permissionRenderer}
                />
              </>
            ) : canReplayTranscript && transcriptQuery.isLoading ? (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
                Loading saved transcript…
              </div>
            ) : canReplayTranscript && transcriptQuery.isError ? (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-critical">
                Failed to load saved transcript.
              </div>
            ) : canReplayTranscript && replayMessages.length > 0 ? (
              <SessionChat
                className="niuu-h-full"
                showToolbar={false}
                showInternalToggle={false}
                internalVisibility={showInternalMessages}
                messages={replayMessages}
                connected={false}
                historyLoaded={!transcriptQuery.isLoading}
                participants={replayParticipants}
                meshEvents={replayMeshEvents}
                sessionName={sessionName}
                onMessageCountChange={setVisibleMessageCount}
                onSend={() => {}}
                onStop={() => {}}
              />
            ) : isSessionBooting(sessionStatus) ? (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
                Session is starting…
              </div>
            ) : canReplayTranscript ? (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
                No saved transcript yet.
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
