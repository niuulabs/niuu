import { useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import {
  LoadingState,
  ErrorState,
  EmptyState,
  StateDot,
  relTime,
  cn,
  Dialog,
  DialogContent,
} from '@niuulabs/ui';
import type { DotState } from '@niuulabs/ui';
import {
  Archive,
  Check,
  ChevronDown,
  ChevronRight,
  RotateCcw,
  Square,
  Clock3,
  Download,
  FolderGit2,
  Search,
  SquareTerminal,
  Ticket,
  Trash2,
} from 'lucide-react';
import { LaunchWizard } from './LaunchWizard';
import { ImportExternalSessionsDialog } from './ImportExternalSessionsDialog';
import { useSessionList } from './hooks/useSessionStore';
import { groupByState } from './sessions/groupByState';
import { LiveSessionDetailPage } from './LiveSessionDetailPage';
import type { Session, SessionState } from '../domain/session';
import type { IVolundrService } from '../ports/IVolundrService';
import { useForgePreference } from './useForgePreference';
import {
  FILTER_LABELS,
  SESSION_FILTERS,
  SESSION_STATE_LABELS,
  STOPPABLE_STATES,
  SIDEBAR_WIDTH,
  sidebarWidth,
  matchesSessionFilter,
  type SessionFilter,
} from './sessions/presentation';
import './SessionsPage.css';

// ---------------------------------------------------------------------------
// Pod group definitions — maps display labels to session states
// ---------------------------------------------------------------------------

interface PodGroupDef {
  label: string;
  states: SessionState[];
}

type SidebarMode = 'state' | 'repo' | 'forge';
const SIDEBAR_MODES = ['state', 'repo', 'forge'] as const;
type RowAction = 'stop' | 'archive' | 'restore' | 'delete';

interface SessionSection {
  label: string;
  sessions: Session[];
}

const POD_GROUPS: PodGroupDef[] = [
  { label: 'NEEDS YOU', states: ['awaiting_input'] },
  { label: 'ACTIVE', states: ['running'] },
  { label: 'IDLE', states: ['idle', 'ready'] },
  { label: 'BOOTING', states: ['provisioning', 'requested'] },
  { label: 'ERROR', states: ['failed'] },
  { label: 'STOPPED', states: ['terminated', 'terminating'] },
  { label: 'ARCHIVED', states: ['archived'] },
];

// ---------------------------------------------------------------------------
// Session state → dot state mapping
// ---------------------------------------------------------------------------

const SESSION_DOT: Record<SessionState, DotState> = {
  running: 'running',
  idle: 'idle',
  awaiting_input: 'attention',
  provisioning: 'processing',
  requested: 'queued',
  ready: 'healthy',
  terminating: 'degraded',
  terminated: 'archived',
  archived: 'archived',
  failed: 'failed',
};

export function looksLikeRepoLabel(value: string): boolean {
  return (
    value.includes('#') ||
    value.startsWith('~/') ||
    value.startsWith('/') ||
    value.startsWith('http')
  );
}

export function compactSourceParts(value: string): { label: string; branch?: string } {
  if (value.includes('#')) {
    const [repo, branch] = value.split('#');
    return { label: shortenRepoLabel(repo ?? value), branch: branch || undefined };
  }
  return { label: shortenRepoLabel(value) };
}

export function shortenRepoLabel(value: string): string {
  if (value.startsWith('~/') || value.startsWith('/')) return value;
  const trimmed = value.replace(/\/+$/, '');
  const slug = trimmed.split('/').pop() ?? trimmed;
  return slug.replace(/\.git$/, '') || value;
}

export function toGroupTestId(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export function sessionActivityTs(session: Session): number {
  return new Date(session.lastActivityAt ?? session.startedAt).getTime();
}

const EXTERNAL_ORIGINS = new Set(['claude', 'codex']);

/** External origins ('claude' / 'codex') get a badge; volundr-native rows do not. */
export function sessionOriginBadge(session: Pick<Session, 'origin'>): string | null {
  if (!session.origin) return null;
  if (!EXTERNAL_ORIGINS.has(session.origin)) return null;
  return session.origin;
}

export function compareSessionsByActivity(a: Session, b: Session): number {
  return sessionActivityTs(b) - sessionActivityTs(a);
}

export function repoGroupLabel(session: Session): string {
  if (session.preview && looksLikeRepoLabel(session.preview)) {
    return compactSourceParts(session.preview).label;
  }
  if (session.personaName.startsWith('~/') || session.personaName.startsWith('/')) {
    return session.personaName;
  }
  return 'other';
}

export function groupByRepo(sessions: Session[]): SessionSection[] {
  const grouped = new Map<string, Session[]>();

  for (const session of sessions) {
    const label = repoGroupLabel(session);
    const bucket = grouped.get(label);
    if (bucket) {
      bucket.push(session);
    } else {
      grouped.set(label, [session]);
    }
  }

  return [...grouped.entries()]
    .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
    .map(([label, groupedSessions]) => ({
      label,
      sessions: [...groupedSessions].sort(compareSessionsByActivity),
    }));
}

export function forgeGroupLabel(session: Session): string {
  return session.clusterName ?? session.clusterId ?? 'unknown forge';
}

export function groupByForge(sessions: Session[]): SessionSection[] {
  const grouped = new Map<string, Session[]>();

  for (const session of sessions) {
    const label = forgeGroupLabel(session);
    const bucket = grouped.get(label);
    if (bucket) {
      bucket.push(session);
    } else {
      grouped.set(label, [session]);
    }
  }

  return [...grouped.entries()]
    .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
    .map(([label, groupedSessions]) => ({
      label,
      sessions: [...groupedSessions].sort(compareSessionsByActivity),
    }));
}

// ---------------------------------------------------------------------------
// PodEntry — a single session row in the sidebar
// ---------------------------------------------------------------------------

function PodEntry({
  session,
  selected,
  onSelect,
  collapsed = false,
  selectable = false,
  checked = false,
  onToggleSelection,
  showDetails = false,
  onAction,
  busy = false,
}: {
  session: Session;
  selected: boolean;
  onSelect: () => void;
  collapsed?: boolean;
  selectable?: boolean;
  checked?: boolean;
  onToggleSelection?: () => void;
  showDetails?: boolean;
  onAction?: (session: Session, action: RowAction) => void;
  busy?: boolean;
}) {
  const ageLabel = relTime(new Date(session.lastActivityAt ?? session.startedAt).getTime());
  const primaryLabel = session.name || session.personaName || session.id;
  const trackerLabel = session.sagaId ?? session.runId ?? session.trackerIssue?.identifier;
  const previewLabel = session.preview;
  const sourceParts =
    previewLabel && looksLikeRepoLabel(previewLabel) ? compactSourceParts(previewLabel) : null;
  const showPreviewFallback = previewLabel && !sourceParts;
  const forgeLabel = session.clusterName ?? session.clusterId;
  const originBadge = sessionOriginBadge(session);
  return (
    <div className="forge-session-row">
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? 'true' : undefined}
        title={primaryLabel}
        data-testid={`pod-entry-${session.id}`}
        className={cn(
          'forge-session-row__select niuu:flex niuu:w-full niuu:items-start niuu:gap-2 niuu:border-b niuu:border-l-2 niuu:px-3 niuu:py-1.5 niuu:text-left niuu:transition-colors',
          selected
            ? 'niuu:border-brand niuu:border-b-white/10 forge-session-row__select--selected niuu:shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]'
            : 'niuu:border-transparent niuu:border-b-white/6 niuu:hover:bg-bg-tertiary',
        )}
      >
        {selectable && !collapsed ? (
          <span
            role="checkbox"
            tabIndex={0}
            aria-checked={checked}
            onClick={(event) => {
              event.stopPropagation();
              onToggleSelection?.();
            }}
            onKeyDown={(event) => {
              if (event.key !== 'Enter' && event.key !== ' ') return;
              event.preventDefault();
              event.stopPropagation();
              onToggleSelection?.();
            }}
            aria-label={`${checked ? 'Deselect' : 'Select'} stopped session ${session.id}`}
            data-testid={`stopped-session-checkbox-${session.id}`}
            className={cn(
              'niuu:mt-0.5 niuu:flex niuu:h-4 niuu:w-4 niuu:flex-shrink-0 niuu:items-center niuu:justify-center niuu:rounded-sm niuu:border niuu:transition-colors',
              checked
                ? 'niuu:border-brand niuu:bg-brand niuu:text-bg-primary'
                : 'niuu:border-border-subtle niuu:bg-bg-elevated niuu:text-transparent niuu:hover:border-brand/60',
            )}
          >
            <Check className="niuu:h-3 niuu:w-3" />
          </span>
        ) : null}
        <StateDot
          state={SESSION_DOT[session.state]}
          pulse={session.state === 'running' || session.state === 'awaiting_input'}
        />
        {collapsed ? null : (
          <>
            <div className="niuu:flex-1 niuu:min-w-0 niuu:flex niuu:flex-col niuu:gap-0.5">
              <div className="forge-session-row__title niuu:text-text-primary niuu:truncate">
                {primaryLabel}
              </div>
              <span className="forge-session-state" data-state={session.state}>
                {SESSION_STATE_LABELS[session.state]}
              </span>
              <div className="niuu:flex niuu:min-w-0 niuu:flex-wrap niuu:items-center niuu:gap-x-2 niuu:gap-y-0.5 niuu:font-mono niuu:text-[10px] niuu:text-text-muted">
                {showDetails && trackerLabel ? (
                  <span
                    className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-1.5"
                    title={trackerLabel}
                  >
                    <Ticket className="niuu:h-3 niuu:w-3 niuu:flex-shrink-0 niuu:text-text-faint" />
                    <span className="niuu:truncate niuu:text-brand">{trackerLabel}</span>
                  </span>
                ) : null}
                {showDetails && forgeLabel ? (
                  <span
                    className="niuu:inline-flex niuu:min-w-0 niuu:items-center niuu:gap-1.5 niuu:rounded-full niuu:border niuu:border-brand/20 niuu:bg-brand/10 niuu:px-2 niuu:py-0.5"
                    title={forgeLabel}
                  >
                    <span className="niuu:text-[9px] niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-faint">
                      forge
                    </span>
                    <span className="niuu:truncate niuu:text-brand">{forgeLabel}</span>
                  </span>
                ) : null}
                {originBadge ? (
                  <span
                    className="niuu:inline-flex niuu:flex-shrink-0 niuu:items-center niuu:rounded-full niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5"
                    title={`Imported from ${originBadge}`}
                    data-testid={`session-origin-badge-${session.id}`}
                  >
                    <span className="niuu:text-[9px] niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-secondary">
                      {originBadge}
                    </span>
                  </span>
                ) : null}
                {sourceParts ? (
                  <span
                    className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-1.5"
                    title={previewLabel}
                  >
                    <FolderGit2 className="niuu:h-3 niuu:w-3 niuu:flex-shrink-0 niuu:text-text-faint" />
                    <span className="niuu:truncate">
                      {showDetails
                        ? sourceParts.label
                        : sourceParts.label.split('/').filter(Boolean).slice(-2).join('/')}
                    </span>
                    {sourceParts.branch ? (
                      <span className="niuu:flex-shrink-0 niuu:text-brand">
                        @{sourceParts.branch}
                      </span>
                    ) : null}
                  </span>
                ) : null}
                {showDetails && showPreviewFallback ? (
                  <span
                    className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-1.5"
                    title={previewLabel}
                  >
                    <SquareTerminal className="niuu:h-3 niuu:w-3 niuu:flex-shrink-0 niuu:text-text-faint" />
                    <span className="niuu:truncate">{previewLabel}</span>
                  </span>
                ) : null}
                <span className="niuu:flex niuu:flex-shrink-0 niuu:items-center niuu:gap-1.5">
                  <Clock3 className="niuu:h-3 niuu:w-3 niuu:flex-shrink-0 niuu:text-text-faint" />
                  <span>{ageLabel}</span>
                </span>
              </div>
            </div>
          </>
        )}
      </button>
      {!collapsed && onAction && (
        <div className="forge-session-row__actions" aria-label={`Actions for ${primaryLabel}`}>
          {STOPPABLE_STATES.has(session.state) && (
            <button
              type="button"
              disabled={busy}
              title="Stop session"
              aria-label={`Stop ${primaryLabel}`}
              data-testid={`pod-entry-${session.id}-stop`}
              onClick={() => onAction(session, 'stop')}
            >
              <Square />
            </button>
          )}
          {session.state !== 'archived' && session.state !== 'terminating' && (
            <button
              type="button"
              disabled={busy}
              title="Archive session"
              aria-label={`Archive ${primaryLabel}`}
              data-testid={`pod-entry-${session.id}-archive`}
              onClick={() => onAction(session, 'archive')}
            >
              <Archive />
            </button>
          )}
          {session.state === 'archived' && (
            <button
              type="button"
              disabled={busy}
              title="Restore session"
              aria-label={`Restore ${primaryLabel}`}
              onClick={() => onAction(session, 'restore')}
            >
              <RotateCcw />
            </button>
          )}
          <button
            type="button"
            disabled={busy}
            title="Delete session"
            aria-label={`Delete ${primaryLabel}`}
            data-testid={`pod-entry-${session.id}-delete`}
            onClick={() => onAction(session, 'delete')}
          >
            <Trash2 />
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PodGroup — a state section with label + session entries
// ---------------------------------------------------------------------------

function PodGroup({
  label,
  sessions,
  selectedId,
  onSelect,
  collapsed = false,
  selectableSessionIds,
  selectedSessionIds,
  onToggleSelection,
  showDetails = false,
  onAction,
  busyId,
}: {
  label: string;
  sessions: Session[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  collapsed?: boolean;
  selectableSessionIds?: ReadonlySet<string>;
  selectedSessionIds?: ReadonlySet<string>;
  onToggleSelection?: (id: string) => void;
  showDetails?: boolean;
  onAction?: (session: Session, action: RowAction) => void;
  busyId?: string;
}) {
  const [folded, setFolded] = useForgePreference(`group.${label}`, '0', ['0', '1']);
  if (sessions.length === 0) return null;

  return (
    <div data-testid={`pod-group-${toGroupTestId(label)}`}>
      {!collapsed && (
        <button
          type="button"
          aria-expanded={folded !== '1'}
          onClick={() => setFolded(folded === '1' ? '0' : '1')}
          className="niuu:flex niuu:w-full niuu:items-center niuu:justify-between niuu:border-b niuu:border-white/6 niuu:px-4 niuu:py-2 niuu:text-[10px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.18em] niuu:text-text-muted"
        >
          <span className="niuu:flex niuu:items-center niuu:gap-1">
            {folded === '1' ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
            {label}
          </span>
          <span
            className="niuu:font-mono niuu:text-text-faint"
            data-testid={`pod-group-${toGroupTestId(label)}-count`}
          >
            {sessions.length}
          </span>
        </button>
      )}
      {(collapsed || folded !== '1') &&
        sessions.map((s) => (
          <PodEntry
            key={s.id}
            session={s}
            showDetails={showDetails}
            onAction={onAction}
            busy={!!busyId}
            selected={s.id === selectedId}
            onSelect={() => onSelect(s.id)}
            collapsed={collapsed}
            selectable={selectableSessionIds?.has(s.id) ?? false}
            checked={selectedSessionIds?.has(s.id) ?? false}
            onToggleSelection={
              onToggleSelection && selectableSessionIds?.has(s.id)
                ? () => onToggleSelection(s.id)
                : undefined
            }
          />
        ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SessionsPage — master-detail layout
// ---------------------------------------------------------------------------

export function SessionsPage() {
  const navigate = useNavigate();
  const { sessionId: routeSessionId } = useParams({ strict: false });
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarMode, setSidebarMode] = useForgePreference<SidebarMode>(
    'grouping',
    'state',
    SIDEBAR_MODES,
  );
  const [filter, setFilter] = useForgePreference<SessionFilter>('filter', 'live', SESSION_FILTERS);
  const [details, setDetails] = useForgePreference('details', '0', ['0', '1']);
  const [storedWidth, setStoredWidth] = useForgePreference<string>(
    'sidebarWidth',
    String(SIDEBAR_WIDTH.default),
  );
  const [dragWidth, setDragWidth] = useState<number | null>(null);
  const width = dragWidth ?? sidebarWidth(storedWidth);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const [rowBusyId, setRowBusyId] = useState<string>();
  const [actionError, setActionError] = useState<string>();
  const [deleteTarget, setDeleteTarget] = useState<Session | null>(null);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [stoppedSelectionMode, setStoppedSelectionMode] = useState(false);
  const [selectedStoppedIds, setSelectedStoppedIds] = useState<Set<string>>(new Set());
  const [deleteStoppedOpen, setDeleteStoppedOpen] = useState(false);
  const [launchOpen, setLaunchOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();

  const sessionsQuery = useSessionList();
  const allSessions = useMemo(() => sessionsQuery.data ?? [], [sessionsQuery.data]);
  const stoppedSessionCount = useMemo(
    () => allSessions.filter((session) => session.state === 'terminated').length,
    [allSessions],
  );
  const stoppedSessionIds = useMemo(
    () =>
      new Set(
        allSessions
          .filter((session) => session.state === 'terminated')
          .map((session) => session.id),
      ),
    [allSessions],
  );

  // Filter by search query
  const filteredSessions = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const visible = allSessions.filter((session) => matchesSessionFilter(session, filter));
    if (!q) return visible;
    return visible.filter(
      (s) =>
        s.id.toLowerCase().includes(q) ||
        s.name?.toLowerCase().includes(q) ||
        s.personaName.toLowerCase().includes(q) ||
        s.preview?.toLowerCase().includes(q) ||
        s.clusterName?.toLowerCase().includes(q) ||
        s.clusterId?.toLowerCase().includes(q),
    );
  }, [allSessions, searchQuery, filter]);
  const filteredStoppedSessions = useMemo(
    () => filteredSessions.filter((session) => session.state === 'terminated'),
    [filteredSessions],
  );
  const filteredStoppedIds = useMemo(
    () => new Set(filteredStoppedSessions.map((session) => session.id)),
    [filteredStoppedSessions],
  );

  // Group by state
  const grouped = useMemo(() => groupByState(filteredSessions), [filteredSessions]);

  // Build sidebar groups — flatten matching states per display group
  const sidebarGroups = useMemo<SessionSection[]>(() => {
    if (sidebarMode === 'repo') {
      return groupByRepo(filteredSessions);
    }
    if (sidebarMode === 'forge') {
      return groupByForge(filteredSessions);
    }
    return POD_GROUPS.map((g) => ({
      label: g.label,
      sessions: g.states.flatMap((st) => grouped[st]),
    }));
  }, [filteredSessions, grouped, sidebarMode]);
  const requestedSessionId = typeof routeSessionId === 'string' ? routeSessionId : null;
  const resolvedSelectedSessionId = useMemo(() => {
    if (allSessions.length === 0) return null;
    if (requestedSessionId) {
      const matchingSession = allSessions.find((session) => session.id === requestedSessionId);
      if (matchingSession) return matchingSession.id;
    }
    if (selectedSessionId) {
      const matchingSession = allSessions.find((session) => session.id === selectedSessionId);
      if (matchingSession) return matchingSession.id;
    }
    const running = allSessions.find((session) => session.state === 'running');
    return running?.id ?? allSessions[0]?.id ?? null;
  }, [allSessions, requestedSessionId, selectedSessionId]);
  const resolvedSelectedStoppedIds = useMemo(
    () => new Set([...selectedStoppedIds].filter((id) => stoppedSessionIds.has(id))),
    [selectedStoppedIds, stoppedSessionIds],
  );

  function handleSelectSession(id: string) {
    setSelectedSessionId(id);
    void navigate({
      to: '/volundr/sessions/$sessionId',
      params: { sessionId: id },
    });
  }

  async function handleArchiveAllStopped() {
    if (archiveBusy || stoppedSessionCount === 0) return;
    setArchiveBusy(true);
    setActionError(undefined);
    try {
      await volundr.archiveStoppedSessions();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'history'] }),
      ]);
      await sessionsQuery.refetch();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Could not archive stopped sessions');
    } finally {
      setArchiveBusy(false);
    }
  }

  function handleToggleStoppedSelection(sessionId: string) {
    setSelectedStoppedIds((current) => {
      const next = new Set(current);
      if (next.has(sessionId)) {
        next.delete(sessionId);
      } else {
        next.add(sessionId);
      }
      return next;
    });
  }

  function handleSelectAllVisibleStopped() {
    setSelectedStoppedIds(new Set(filteredStoppedSessions.map((session) => session.id)));
  }

  function handleClearStoppedSelection() {
    setSelectedStoppedIds(new Set());
  }

  async function handleDeleteSelectedStopped() {
    if (deleteBusy || resolvedSelectedStoppedIds.size === 0) return;
    const ids = [...resolvedSelectedStoppedIds];
    setDeleteBusy(true);
    setActionError(undefined);
    try {
      const results = await Promise.allSettled(ids.map((id) => volundr.deleteSession(id)));
      const deletedIds = ids.filter((_, index) => results[index]?.status === 'fulfilled');
      const failures = results.filter((result) => result.status === 'rejected');
      if (failures.length > 0) {
        setSelectedStoppedIds(new Set(ids.filter((id) => !deletedIds.includes(id))));
        await queryClient.invalidateQueries({ queryKey: ['volundr'] });
        if (resolvedSelectedSessionId && deletedIds.includes(resolvedSelectedSessionId)) {
          setSelectedSessionId(null);
          await navigate({ to: '/volundr/sessions', replace: true });
        }
        throw new Error(
          `${failures.length} session(s) could not be deleted. ${failures[0]?.reason instanceof Error ? failures[0].reason.message : 'Try again.'}`,
        );
      }
      if (resolvedSelectedSessionId && ids.includes(resolvedSelectedSessionId)) {
        setSelectedSessionId(null);
        await navigate({ to: '/volundr/sessions', replace: true });
      }
      setDeleteStoppedOpen(false);
      setStoppedSelectionMode(false);
      setSelectedStoppedIds(new Set());
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'history'] }),
      ]);
      await sessionsQuery.refetch();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Could not delete stopped sessions');
    } finally {
      setDeleteBusy(false);
    }
  }

  async function handleRowAction(session: Session, action: RowAction, confirmed = false) {
    if (rowBusyId || archiveBusy || deleteBusy) return;
    if (action === 'delete' && !confirmed) {
      setActionError(undefined);
      setDeleteTarget(session);
      return;
    }
    setActionError(undefined);
    setRowBusyId(session.id);
    try {
      if (action === 'stop') await volundr.stopSession(session.id);
      if (action === 'archive') {
        if (STOPPABLE_STATES.has(session.state)) await volundr.stopSession(session.id);
        await volundr.archiveSession(session.id);
      }
      if (action === 'restore') await volundr.restoreSession(session.id);
      if (action === 'delete') {
        await volundr.deleteSession(session.id);
        setDeleteTarget(null);
        if (session.id === resolvedSelectedSessionId) {
          setSelectedSessionId(null);
          await navigate({ to: '/volundr/sessions', replace: true });
        }
      }
      await queryClient.invalidateQueries({ queryKey: ['volundr'] });
    } catch (error) {
      setActionError(error instanceof Error ? error.message : `Could not ${action} session`);
    } finally {
      setRowBusyId(undefined);
    }
  }

  const deleteSelectionCount = resolvedSelectedStoppedIds.size;
  const deleteSelectionPreview = filteredStoppedSessions.filter((session) =>
    resolvedSelectedStoppedIds.has(session.id),
  );

  return (
    <>
      <div
        className="forge-sessions niuu:relative niuu:flex niuu:h-full"
        data-detail-open={!!requestedSessionId}
        data-testid="sessions-page"
      >
        {/* ── Left sidebar: pod list ─────────────────────────────── */}
        <nav
          className={cn('forge-session-list niuu:relative niuu:shrink-0 niuu:overflow-hidden')}
          style={
            sidebarCollapsed
              ? {
                  width: '48px',
                  minWidth: '48px',
                  maxWidth: '48px',
                  flexBasis: '48px',
                }
              : {
                  width: `${width}px`,
                  minWidth: `${SIDEBAR_WIDTH.min}px`,
                  maxWidth: `${SIDEBAR_WIDTH.max}px`,
                  flexBasis: `${width}px`,
                }
          }
          aria-label="Session list"
          data-testid="pod-list-sidebar"
        >
          {sidebarCollapsed ? (
            <div className="niuu:flex niuu:h-full niuu:flex-col niuu:overflow-hidden">
              <div className="niuu:flex niuu:items-center niuu:justify-center niuu:border-b niuu:border-border-subtle niuu:py-2.5">
                <button
                  type="button"
                  onClick={() => setSidebarCollapsed(false)}
                  className="niuu:font-mono niuu:text-sm niuu:text-text-muted"
                  data-testid="pod-sidebar-toggle"
                  aria-label="Expand pods sidebar"
                >
                  ›
                </button>
              </div>
              <div className="niuu:flex-1 niuu:overflow-y-auto niuu:py-2">
                {sidebarGroups.map((g) => (
                  <PodGroup
                    key={g.label}
                    label={g.label}
                    sessions={g.sessions}
                    selectedId={resolvedSelectedSessionId}
                    onSelect={handleSelectSession}
                    showDetails={details === '1'}
                    onAction={(session, action) => void handleRowAction(session, action)}
                    busyId={rowBusyId}
                    collapsed
                  />
                ))}
              </div>
            </div>
          ) : (
            <div className="niuu:flex niuu:h-full niuu:flex-col niuu:overflow-hidden">
              <div className="niuu:flex niuu:items-center niuu:justify-between niuu:border-b niuu:border-white/8 niuu:px-2.5 niuu:py-2">
                <div className="niuu:flex niuu:items-center niuu:gap-1.5">
                  <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                    Sessions
                  </h2>
                </div>
                <button
                  type="button"
                  onClick={() => setSidebarCollapsed(true)}
                  className="niuu:font-mono niuu:text-lg niuu:text-text-muted"
                  data-testid="pod-sidebar-toggle"
                  aria-label="Collapse pods sidebar"
                >
                  ‹
                </button>
              </div>

              <div className="forge-session-filters" aria-label="Session states">
                {SESSION_FILTERS.map((value) => (
                  <button
                    key={value}
                    type="button"
                    aria-pressed={filter === value}
                    data-testid={`session-filter-${value}`}
                    onClick={() => setFilter(value)}
                  >
                    {FILTER_LABELS[value]}
                    <span>
                      {allSessions.filter((session) => matchesSessionFilter(session, value)).length}
                    </span>
                  </button>
                ))}
              </div>
              <label className="niuu:flex niuu:items-center niuu:gap-2 niuu:px-3 niuu:pb-2 niuu:text-xs niuu:text-text-muted">
                <input
                  type="checkbox"
                  checked={details === '1'}
                  onChange={(event) => setDetails(event.target.checked ? '1' : '0')}
                />
                Show session details
              </label>
              {actionError && (
                <div role="alert" className="niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-critical">
                  {actionError}
                </div>
              )}
              <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:px-2.5 niuu:py-1">
                <span className="niuu:text-xs niuu:text-text-muted">group by</span>
                <div
                  className="niuu:inline-flex niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:p-0.5"
                  data-testid="pod-group-mode"
                >
                  {(['state', 'repo', 'forge'] as const).map((mode) => {
                    const active = sidebarMode === mode;
                    return (
                      <button
                        key={mode}
                        type="button"
                        onClick={() => setSidebarMode(mode)}
                        className={cn(
                          'niuu:rounded-md niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-semibold niuu:transition-colors',
                          active
                            ? 'niuu:bg-brand/15 niuu:text-brand'
                            : 'niuu:text-text-muted niuu:hover:text-text-primary',
                        )}
                        data-testid={`pod-group-mode-${mode}`}
                        aria-pressed={active}
                      >
                        {mode}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="niuu:px-2.5 niuu:pb-1">
                <div className="niuu:flex niuu:items-center niuu:gap-2">
                  <button
                    type="button"
                    onClick={() => setLaunchOpen(true)}
                    className="niuu:flex niuu:h-10 niuu:w-10 niuu:flex-shrink-0 niuu:items-center niuu:justify-center niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:text-sm niuu:font-semibold niuu:text-text-muted niuu:transition-colors niuu:hover:border-brand/40 niuu:hover:text-brand"
                    data-testid="pod-launch-button"
                    aria-label="Launch a new session"
                    title="Launch a new session"
                  >
                    +
                  </button>
                  <button
                    type="button"
                    onClick={() => setImportOpen(true)}
                    className="niuu:flex niuu:h-10 niuu:w-10 niuu:flex-shrink-0 niuu:items-center niuu:justify-center niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:text-text-muted niuu:transition-colors niuu:hover:border-brand/40 niuu:hover:text-brand"
                    data-testid="pod-import-button"
                    aria-label="Import external CLI sessions"
                    title="Import external CLI sessions"
                  >
                    <Download className="niuu:h-3.5 niuu:w-3.5" />
                  </button>
                  <div className="forge-session-search niuu:flex niuu:min-w-0 niuu:flex-1 niuu:items-center niuu:gap-2 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:shadow-[inset_0_1px_0_rgba(255,255,255,0.02)] niuu:focus-within:border-brand/50 niuu:focus-within:ring-1 niuu:focus-within:ring-brand/20">
                    <Search
                      className="niuu:h-4 niuu:w-4 niuu:flex-shrink-0 niuu:text-text-muted"
                      aria-hidden="true"
                    />
                    <input
                      type="search"
                      placeholder="filter by name / repo / branch / forge"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      className="niuu:min-w-0 niuu:flex-1 niuu:bg-transparent niuu:py-0.5 niuu:pr-1 niuu:text-[11px] niuu:text-text-primary niuu:placeholder:text-text-muted niuu:focus:outline-none"
                      data-testid="pod-search"
                      aria-label="Filter sessions"
                    />
                  </div>
                </div>
              </div>

              {stoppedSessionCount > 0 && (
                <details className="forge-session-maintenance">
                  <summary>Manage stopped sessions ({stoppedSessionCount})</summary>
                  <div className="niuu:space-y-2">
                    <button
                      type="button"
                      onClick={() => void handleArchiveAllStopped()}
                      disabled={archiveBusy}
                      className="niuu:flex niuu:w-full niuu:items-center niuu:justify-between niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-3 niuu:py-2 niuu:font-mono niuu:text-[10px] niuu:text-text-muted niuu:hover:bg-bg-elevated niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50"
                      data-testid="archive-stopped-button"
                    >
                      <span>
                        {archiveBusy ? 'archiving stopped sessions…' : 'archive all stopped'}
                      </span>
                      <span className="niuu:text-text-faint">{stoppedSessionCount}</span>
                    </button>

                    <div className="niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:p-2">
                      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
                        <button
                          type="button"
                          onClick={() => {
                            const next = !stoppedSelectionMode;
                            setStoppedSelectionMode(next);
                            if (next) setFilter('stopped');
                            if (!next) {
                              setSelectedStoppedIds(new Set());
                            }
                          }}
                          className={cn(
                            'niuu:inline-flex niuu:items-center niuu:gap-2 niuu:rounded-md niuu:px-2.5 niuu:py-1.5 niuu:text-xs niuu:font-semibold niuu:transition-colors',
                            stoppedSelectionMode
                              ? 'niuu:bg-brand/15 niuu:text-brand'
                              : 'niuu:text-text-muted niuu:hover:bg-bg-elevated niuu:hover:text-text-primary',
                          )}
                          data-testid="toggle-stopped-selection-button"
                        >
                          <Trash2 className="niuu:h-3.5 niuu:w-3.5" />
                          {stoppedSelectionMode
                            ? 'cancel delete select'
                            : 'select stopped to delete'}
                        </button>
                        <span className="niuu:font-mono niuu:text-[10px] niuu:text-text-faint">
                          {deleteSelectionCount} selected
                        </span>
                      </div>

                      {stoppedSelectionMode ? (
                        <div className="niuu:mt-2 niuu:flex niuu:flex-wrap niuu:gap-2">
                          <button
                            type="button"
                            onClick={handleSelectAllVisibleStopped}
                            disabled={filteredStoppedSessions.length === 0}
                            className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:px-2.5 niuu:py-1 niuu:font-mono niuu:text-[10px] niuu:text-text-muted niuu:hover:bg-bg-elevated niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50"
                            data-testid="select-all-stopped-button"
                          >
                            select visible stopped ({filteredStoppedSessions.length})
                          </button>
                          <button
                            type="button"
                            onClick={handleClearStoppedSelection}
                            disabled={deleteSelectionCount === 0}
                            className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:px-2.5 niuu:py-1 niuu:font-mono niuu:text-[10px] niuu:text-text-muted niuu:hover:bg-bg-elevated niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50"
                            data-testid="clear-stopped-selection-button"
                          >
                            clear
                          </button>
                          <button
                            type="button"
                            onClick={() => setDeleteStoppedOpen(true)}
                            disabled={deleteSelectionCount === 0}
                            className="niuu:rounded-md niuu:border niuu:border-red-500/35 niuu:bg-red-500/10 niuu:px-2.5 niuu:py-1 niuu:font-mono niuu:text-[10px] niuu:text-red-200 niuu:hover:bg-red-500/15 niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50"
                            data-testid="delete-selected-stopped-button"
                          >
                            delete selected ({deleteSelectionCount})
                          </button>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </details>
              )}

              <div className="niuu:flex-1 niuu:overflow-y-auto niuu:pb-1.5">
                {filteredSessions.length === 0 && !sessionsQuery.isLoading && (
                  <p className="niuu:p-4 niuu:text-sm niuu:text-text-muted">
                    No sessions match these filters.
                  </p>
                )}
                {sidebarGroups.map((g) => (
                  <PodGroup
                    key={g.label}
                    label={g.label}
                    sessions={g.sessions}
                    selectedId={resolvedSelectedSessionId}
                    onSelect={handleSelectSession}
                    showDetails={details === '1'}
                    onAction={(session, action) => void handleRowAction(session, action)}
                    busyId={rowBusyId}
                    selectableSessionIds={stoppedSelectionMode ? filteredStoppedIds : undefined}
                    selectedSessionIds={resolvedSelectedStoppedIds}
                    onToggleSelection={handleToggleStoppedSelection}
                  />
                ))}
              </div>
            </div>
          )}
        </nav>

        {/* ── Main content: session detail ───────────────────────── */}
        {!sidebarCollapsed && (
          <div
            role="separator"
            aria-label="Resize session list"
            aria-orientation="vertical"
            aria-valuemin={SIDEBAR_WIDTH.min}
            aria-valuemax={SIDEBAR_WIDTH.max}
            aria-valuenow={width}
            tabIndex={0}
            className="forge-session-resizer"
            onPointerDown={(event) => {
              drag.current = { x: event.clientX, width };
              event.currentTarget.setPointerCapture(event.pointerId);
            }}
            onPointerMove={(event) => {
              if (drag.current)
                setDragWidth(sidebarWidth(drag.current.width + event.clientX - drag.current.x));
            }}
            onPointerUp={(event) => {
              if (!drag.current) return;
              setStoredWidth(
                String(sidebarWidth(drag.current.width + event.clientX - drag.current.x)),
              );
              drag.current = null;
              setDragWidth(null);
            }}
            onPointerCancel={() => {
              drag.current = null;
              setDragWidth(null);
            }}
            onKeyDown={(event) => {
              if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
              event.preventDefault();
              setStoredWidth(
                String(
                  sidebarWidth(
                    event.key === 'Home'
                      ? SIDEBAR_WIDTH.min
                      : event.key === 'End'
                        ? SIDEBAR_WIDTH.max
                        : width +
                          (event.key === 'ArrowRight'
                            ? SIDEBAR_WIDTH.keyboardStep
                            : -SIDEBAR_WIDTH.keyboardStep),
                  ),
                ),
              );
            }}
            onDoubleClick={() => setStoredWidth(String(SIDEBAR_WIDTH.default))}
          />
        )}

        <div className="forge-session-detail niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col niuu:overflow-hidden">
          <button
            type="button"
            className="forge-session-back"
            onClick={() => {
              setSelectedSessionId(null);
              void navigate({ to: '/volundr/sessions' });
            }}
          >
            ‹ Sessions
          </button>
          {sessionsQuery.isLoading && <LoadingState label="Loading sessions…" />}
          {sessionsQuery.isError && (
            <ErrorState
              title="Failed to load sessions"
              message={
                sessionsQuery.error instanceof Error ? sessionsQuery.error.message : 'Unknown error'
              }
            />
          )}
          {sessionsQuery.data && !resolvedSelectedSessionId && (
            <EmptyState
              title="No session selected"
              description="Select a session from the sidebar."
            />
          )}
          {sessionsQuery.data && resolvedSelectedSessionId && (
            <LiveSessionDetailPage
              key={resolvedSelectedSessionId}
              sessionId={resolvedSelectedSessionId}
            />
          )}
        </div>
      </div>
      <Dialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && !rowBusyId) setDeleteTarget(null);
        }}
      >
        <DialogContent
          title="Delete session?"
          description={`Permanently delete ${deleteTarget?.name || deleteTarget?.personaName || 'this session'} and its session record? This cannot be undone.`}
        >
          {actionError && <p role="alert">{actionError}</p>}
          <div className="niuu:flex niuu:justify-end niuu:gap-3">
            <button type="button" disabled={!!rowBusyId} onClick={() => setDeleteTarget(null)}>
              Cancel
            </button>
            <button
              type="button"
              disabled={!!rowBusyId}
              className="niuu:text-critical"
              onClick={() => {
                if (deleteTarget) void handleRowAction(deleteTarget, 'delete', true);
              }}
            >
              Delete permanently
            </button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog
        open={deleteStoppedOpen}
        onOpenChange={(open) => {
          if (!deleteBusy) setDeleteStoppedOpen(open);
        }}
      >
        <DialogContent
          title="Delete Selected Stopped Sessions"
          description="This removes the selected stopped sessions from the list. This action cannot be undone."
        >
          <div className="niuu:space-y-4">
            {actionError && <p role="alert">{actionError}</p>}
            <div className="niuu:rounded-lg niuu:border niuu:border-red-500/25 niuu:bg-red-500/8 niuu:p-3 niuu:text-sm niuu:text-text-secondary">
              {deleteSelectionCount === 1
                ? 'Delete 1 stopped session?'
                : `Delete ${deleteSelectionCount} stopped sessions?`}
            </div>
            {deleteSelectionPreview.length > 0 ? (
              <div className="niuu:max-h-48 niuu:space-y-2 niuu:overflow-y-auto niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:p-3">
                {deleteSelectionPreview.map((session) => (
                  <div
                    key={session.id}
                    className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3 niuu:font-mono niuu:text-xs niuu:text-text-secondary"
                  >
                    <span className="niuu:truncate">{session.personaName || session.id}</span>
                    <span className="niuu:text-text-faint">{session.id}</span>
                  </div>
                ))}
              </div>
            ) : null}
            <div className="niuu:flex niuu:justify-end niuu:gap-2">
              <button
                type="button"
                onClick={() => setDeleteStoppedOpen(false)}
                className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary niuu:hover:bg-bg-secondary"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleDeleteSelectedStopped()}
                disabled={deleteBusy || deleteSelectionCount === 0}
                className="niuu:rounded-md niuu:bg-red-500 niuu:px-3 niuu:py-2 niuu:text-sm niuu:font-medium niuu:text-white niuu:hover:opacity-90 niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50"
                data-testid="confirm-delete-selected-stopped-button"
              >
                {deleteBusy ? 'Deleting…' : `Delete ${deleteSelectionCount}`}
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
      <LaunchWizard open={launchOpen} onOpenChange={setLaunchOpen} />
      <ImportExternalSessionsDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        onImported={async () => {
          await sessionsQuery.refetch();
        }}
      />
    </>
  );
}
