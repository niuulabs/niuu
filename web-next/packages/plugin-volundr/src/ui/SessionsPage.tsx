import { useEffect, useMemo, useState } from 'react';
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
import { Check, Clock3, FolderGit2, Search, SquareTerminal, Ticket, Trash2 } from 'lucide-react';
import { LaunchWizard } from './LaunchWizard';
import { useSessionList } from './hooks/useSessionStore';
import { groupByState } from './sessions/groupByState';
import { LiveSessionDetailPage } from './LiveSessionDetailPage';
import type { Session, SessionState } from '../domain/session';
import type { IVolundrService } from '../ports/IVolundrService';

// ---------------------------------------------------------------------------
// Pod group definitions — maps display labels to session states
// ---------------------------------------------------------------------------

interface PodGroupDef {
  label: string;
  states: SessionState[];
}

type SidebarMode = 'state' | 'repo' | 'forge';

interface SessionSection {
  label: string;
  sessions: Session[];
}

const POD_GROUPS: PodGroupDef[] = [
  { label: 'ACTIVE', states: ['running'] },
  { label: 'IDLE', states: ['idle'] },
  { label: 'BOOTING', states: ['provisioning', 'requested'] },
  { label: 'ERROR', states: ['failed'] },
  { label: 'STOPPED', states: ['terminated'] },
  { label: 'ARCHIVED', states: ['archived'] },
];

// ---------------------------------------------------------------------------
// Session state → dot state mapping
// ---------------------------------------------------------------------------

const SESSION_DOT: Record<SessionState, DotState> = {
  running: 'running',
  idle: 'idle',
  provisioning: 'processing',
  requested: 'queued',
  ready: 'healthy',
  terminating: 'degraded',
  terminated: 'archived',
  archived: 'archived',
  failed: 'failed',
};

function looksLikeRepoLabel(value: string): boolean {
  return (
    value.includes('#') ||
    value.startsWith('~/') ||
    value.startsWith('/') ||
    value.startsWith('http')
  );
}

function compactSourceParts(value: string): { label: string; branch?: string } {
  if (value.includes('#')) {
    const [repo, branch] = value.split('#');
    return { label: shortenRepoLabel(repo ?? value), branch: branch || undefined };
  }
  return { label: shortenRepoLabel(value) };
}

function shortenRepoLabel(value: string): string {
  if (value.startsWith('~/') || value.startsWith('/')) return value;
  const trimmed = value.replace(/\/+$/, '');
  const slug = trimmed.split('/').pop() ?? trimmed;
  return slug.replace(/\.git$/, '') || value;
}

function toGroupTestId(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function sessionActivityTs(session: Session): number {
  return new Date(session.lastActivityAt ?? session.startedAt).getTime();
}

function compareSessionsByActivity(a: Session, b: Session): number {
  return sessionActivityTs(b) - sessionActivityTs(a);
}

function repoGroupLabel(session: Session): string {
  if (session.preview && looksLikeRepoLabel(session.preview)) {
    return compactSourceParts(session.preview).label;
  }
  if (session.personaName.startsWith('~/') || session.personaName.startsWith('/')) {
    return session.personaName;
  }
  return 'other';
}

function groupByRepo(sessions: Session[]): SessionSection[] {
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

function forgeGroupLabel(session: Session): string {
  return session.clusterName ?? session.clusterId ?? 'unknown forge';
}

function groupByForge(sessions: Session[]): SessionSection[] {
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
}: {
  session: Session;
  selected: boolean;
  onSelect: () => void;
  collapsed?: boolean;
  selectable?: boolean;
  checked?: boolean;
  onToggleSelection?: () => void;
}) {
  const ageLabel = relTime(new Date(session.lastActivityAt ?? session.startedAt).getTime());
  const primaryLabel = session.personaName || session.id;
  const trackerLabel = session.sagaId ?? session.runId ?? session.ravnId;
  const previewLabel = session.preview;
  const sourceParts =
    previewLabel && looksLikeRepoLabel(previewLabel) ? compactSourceParts(previewLabel) : null;
  const showPreviewFallback = previewLabel && !sourceParts;
  const forgeLabel = session.clusterName ?? session.clusterId;
  return (
    <button
      type="button"
      onClick={onSelect}
      data-testid={`pod-entry-${session.id}`}
      className={cn(
        'niuu-flex niuu-w-full niuu-items-start niuu-gap-2 niuu-border-b niuu-border-l-2 niuu-px-3 niuu-py-1.5 niuu-text-left niuu-transition-colors',
        selected
          ? 'niuu-border-brand niuu-border-b-white/10 niuu-bg-[#12212b] niuu-shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]'
          : 'niuu-border-transparent niuu-border-b-white/6 hover:niuu-bg-bg-tertiary',
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
            'niuu-mt-0.5 niuu-flex niuu-h-4 niuu-w-4 niuu-flex-shrink-0 niuu-items-center niuu-justify-center niuu-rounded-sm niuu-border niuu-transition-colors',
            checked
              ? 'niuu-border-brand niuu-bg-brand niuu-text-bg-primary'
              : 'niuu-border-border-subtle niuu-bg-bg-elevated niuu-text-transparent hover:niuu-border-brand/60',
          )}
        >
          <Check className="niuu-h-3 niuu-w-3" />
        </span>
      ) : null}
      <StateDot state={SESSION_DOT[session.state]} pulse={session.state === 'running'} />
      {collapsed ? null : (
        <>
          <div className="niuu-flex-1 niuu-min-w-0 niuu-flex niuu-flex-col niuu-gap-0.5">
            <div className="niuu-font-mono niuu-text-[13px] niuu-font-medium niuu-text-text-primary niuu-truncate">
              {primaryLabel}
            </div>
            <div className="niuu-flex niuu-min-w-0 niuu-flex-wrap niuu-items-center niuu-gap-x-2 niuu-gap-y-0.5 niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
              {trackerLabel ? (
                <span
                  className="niuu-flex niuu-min-w-0 niuu-items-center niuu-gap-1.5"
                  title={trackerLabel}
                >
                  <Ticket className="niuu-h-3 niuu-w-3 niuu-flex-shrink-0 niuu-text-text-faint" />
                  <span className="niuu-truncate niuu-text-brand">{trackerLabel}</span>
                </span>
              ) : null}
              {forgeLabel ? (
                <span
                  className="niuu-inline-flex niuu-min-w-0 niuu-items-center niuu-gap-1.5 niuu-rounded-full niuu-border niuu-border-brand/20 niuu-bg-brand/10 niuu-px-2 niuu-py-0.5"
                  title={forgeLabel}
                >
                  <span className="niuu-text-[9px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
                    forge
                  </span>
                  <span className="niuu-truncate niuu-text-brand">{forgeLabel}</span>
                </span>
              ) : null}
              {sourceParts ? (
                <span
                  className="niuu-flex niuu-min-w-0 niuu-items-center niuu-gap-1.5"
                  title={previewLabel}
                >
                  <FolderGit2 className="niuu-h-3 niuu-w-3 niuu-flex-shrink-0 niuu-text-text-faint" />
                  <span className="niuu-truncate">{sourceParts.label}</span>
                  {sourceParts.branch ? (
                    <span className="niuu-flex-shrink-0 niuu-text-brand">
                      @{sourceParts.branch}
                    </span>
                  ) : null}
                </span>
              ) : null}
              {showPreviewFallback ? (
                <span
                  className="niuu-flex niuu-min-w-0 niuu-items-center niuu-gap-1.5"
                  title={previewLabel}
                >
                  <SquareTerminal className="niuu-h-3 niuu-w-3 niuu-flex-shrink-0 niuu-text-text-faint" />
                  <span className="niuu-truncate">{previewLabel}</span>
                </span>
              ) : null}
              <span className="niuu-flex niuu-flex-shrink-0 niuu-items-center niuu-gap-1.5">
                <Clock3 className="niuu-h-3 niuu-w-3 niuu-flex-shrink-0 niuu-text-text-faint" />
                <span>{ageLabel}</span>
              </span>
            </div>
          </div>
        </>
      )}
    </button>
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
}: {
  label: string;
  sessions: Session[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  collapsed?: boolean;
  selectableSessionIds?: ReadonlySet<string>;
  selectedSessionIds?: ReadonlySet<string>;
  onToggleSelection?: (id: string) => void;
}) {
  if (sessions.length === 0) return null;

  return (
    <div data-testid={`pod-group-${toGroupTestId(label)}`}>
      {!collapsed && (
        <div className="niuu-flex niuu-items-center niuu-justify-between niuu-border-b niuu-border-white/6 niuu-px-4 niuu-py-2 niuu-text-[10px] niuu-font-semibold niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-muted">
          <span>{label}</span>
          <span
            className="niuu-font-mono niuu-text-text-faint"
            data-testid={`pod-group-${toGroupTestId(label)}-count`}
          >
            {sessions.length}
          </span>
        </div>
      )}
      {sessions.map((s) => (
        <PodEntry
          key={s.id}
          session={s}
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
  const [sidebarMode, setSidebarMode] = useState<SidebarMode>('state');
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [stoppedSelectionMode, setStoppedSelectionMode] = useState(false);
  const [selectedStoppedIds, setSelectedStoppedIds] = useState<Set<string>>(new Set());
  const [deleteStoppedOpen, setDeleteStoppedOpen] = useState(false);
  const [launchOpen, setLaunchOpen] = useState(false);
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
    if (!q) return allSessions;
    return allSessions.filter(
      (s) =>
        s.id.toLowerCase().includes(q) ||
        s.personaName.toLowerCase().includes(q) ||
        s.preview?.toLowerCase().includes(q) ||
        s.clusterName?.toLowerCase().includes(q) ||
        s.clusterId?.toLowerCase().includes(q),
    );
  }, [allSessions, searchQuery]);
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

  // Auto-select first running session on load
  useEffect(() => {
    if (!sessionsQuery.data) return;

    const requestedSessionId = typeof routeSessionId === 'string' ? routeSessionId : null;
    if (requestedSessionId) {
      const matchingSession = sessionsQuery.data.find(
        (session) => session.id === requestedSessionId,
      );
      if (matchingSession) {
        if (selectedSessionId !== matchingSession.id) {
          setSelectedSessionId(matchingSession.id);
        }
        return;
      }
    }

    if (selectedSessionId) return;

    const running = sessionsQuery.data.filter((s) => s.state === 'running');
    if (running.length > 0) {
      setSelectedSessionId(running[0]!.id);
    } else if (sessionsQuery.data.length > 0) {
      setSelectedSessionId(sessionsQuery.data[0]!.id);
    }
  }, [routeSessionId, selectedSessionId, sessionsQuery.data]);

  useEffect(() => {
    setSelectedStoppedIds((current) => {
      const next = new Set([...current].filter((id) => stoppedSessionIds.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [stoppedSessionIds]);

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
    try {
      await volundr.archiveStoppedSessions();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'history'] }),
      ]);
      await sessionsQuery.refetch();
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
    if (deleteBusy || selectedStoppedIds.size === 0) return;
    const ids = [...selectedStoppedIds];
    setDeleteBusy(true);
    try {
      await Promise.all(ids.map((id) => volundr.deleteSession(id)));
      if (selectedSessionId && ids.includes(selectedSessionId)) {
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
    } finally {
      setDeleteBusy(false);
    }
  }

  const deleteSelectionCount = selectedStoppedIds.size;
  const deleteSelectionPreview = filteredStoppedSessions.filter((session) =>
    selectedStoppedIds.has(session.id),
  );

  return (
    <>
      <div className="niuu-relative niuu-flex niuu-h-full" data-testid="sessions-page">
        {/* ── Left sidebar: pod list ─────────────────────────────── */}
        <nav
          className={cn(
            'niuu-relative niuu-shrink-0 niuu-overflow-hidden niuu-bg-[#0b0c10] niuu-transition-[width] niuu-duration-200',
          )}
          style={
            sidebarCollapsed
              ? {
                  width: '48px',
                  minWidth: '48px',
                  maxWidth: '48px',
                  flexBasis: '48px',
                }
              : {
                  width: '228px',
                  minWidth: '228px',
                  maxWidth: '228px',
                  flexBasis: '228px',
                }
          }
          aria-label="Session list"
          data-testid="pod-list-sidebar"
        >
          {sidebarCollapsed ? (
            <div className="niuu-flex niuu-h-full niuu-flex-col niuu-overflow-hidden">
              <div className="niuu-flex niuu-items-center niuu-justify-center niuu-border-b niuu-border-border-subtle niuu-py-2.5">
                <button
                  type="button"
                  onClick={() => setSidebarCollapsed(false)}
                  className="niuu-font-mono niuu-text-sm niuu-text-text-muted"
                  data-testid="pod-sidebar-toggle"
                  aria-label="Expand pods sidebar"
                >
                  ›
                </button>
              </div>
              <div className="niuu-flex-1 niuu-overflow-y-auto niuu-py-2">
                {sidebarGroups.map((g) => (
                  <PodGroup
                    key={g.label}
                    label={g.label}
                    sessions={g.sessions}
                    selectedId={selectedSessionId}
                    onSelect={handleSelectSession}
                    collapsed
                  />
                ))}
              </div>
            </div>
          ) : (
            <div className="niuu-flex niuu-h-full niuu-flex-col niuu-overflow-hidden">
              <div className="niuu-flex niuu-items-center niuu-justify-between niuu-border-b niuu-border-white/8 niuu-px-2.5 niuu-py-2">
                <div className="niuu-flex niuu-items-center niuu-gap-1.5">
                  <h2 className="niuu-text-sm niuu-font-semibold niuu-text-text-primary">
                    Sessions
                  </h2>
                  <span
                    className="niuu-rounded-full niuu-bg-bg-elevated niuu-px-1.5 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-text-text-muted"
                    data-testid="pod-count"
                  >
                    {allSessions.length}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => setSidebarCollapsed(true)}
                  className="niuu-font-mono niuu-text-lg niuu-text-text-muted"
                  data-testid="pod-sidebar-toggle"
                  aria-label="Collapse pods sidebar"
                >
                  ‹
                </button>
              </div>

              <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-px-2.5 niuu-py-1">
                <span className="niuu-text-[10px] niuu-font-mono niuu-text-text-faint">
                  group by
                </span>
                <div
                  className="niuu-inline-flex niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-p-0.5"
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
                          'niuu-rounded-md niuu-px-2.5 niuu-py-1 niuu-font-mono niuu-text-[10px] niuu-transition-colors',
                          active
                            ? 'niuu-bg-brand/15 niuu-text-brand'
                            : 'niuu-text-text-muted hover:niuu-text-text-primary',
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

              <div className="niuu-px-2.5 niuu-pb-1">
                <div className="niuu-flex niuu-items-center niuu-gap-2">
                  <button
                    type="button"
                    onClick={() => setLaunchOpen(true)}
                    className="niuu-flex niuu-h-7 niuu-w-7 niuu-flex-shrink-0 niuu-items-center niuu-justify-center niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-text-sm niuu-font-semibold niuu-text-text-muted niuu-transition-colors hover:niuu-border-brand/40 hover:niuu-text-brand"
                    data-testid="pod-launch-button"
                    aria-label="Launch a new session"
                    title="Launch a new session"
                  >
                    +
                  </button>
                  <div className="niuu-flex niuu-min-w-0 niuu-flex-1 niuu-items-center niuu-gap-2 niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-px-2 niuu-py-1 niuu-shadow-[inset_0_1px_0_rgba(255,255,255,0.02)] focus-within:niuu-border-brand/50 focus-within:niuu-ring-1 focus-within:niuu-ring-brand/20">
                    <Search
                      className="niuu-h-4 niuu-w-4 niuu-flex-shrink-0 niuu-text-text-muted"
                      aria-hidden="true"
                    />
                    <input
                      type="search"
                      placeholder="filter by name / repo / branch / forge"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      className="niuu-min-w-0 niuu-flex-1 niuu-bg-transparent niuu-py-0.5 niuu-pr-1 niuu-text-[11px] niuu-text-text-primary placeholder:niuu-text-text-muted focus:niuu-outline-none"
                      data-testid="pod-search"
                      aria-label="Filter sessions"
                    />
                  </div>
                </div>
              </div>

              {stoppedSessionCount > 0 && (
                <div className="niuu-px-2.5 niuu-pb-2">
                  <div className="niuu-space-y-2">
                    <button
                      type="button"
                      onClick={() => void handleArchiveAllStopped()}
                      disabled={archiveBusy}
                      className="niuu-flex niuu-w-full niuu-items-center niuu-justify-between niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-px-3 niuu-py-2 niuu-font-mono niuu-text-[10px] niuu-text-text-muted hover:niuu-bg-bg-elevated disabled:niuu-cursor-not-allowed disabled:niuu-opacity-50"
                      data-testid="archive-stopped-button"
                    >
                      <span>
                        {archiveBusy ? 'archiving stopped sessions…' : 'archive all stopped'}
                      </span>
                      <span className="niuu-text-text-faint">{stoppedSessionCount}</span>
                    </button>

                    <div className="niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-p-2">
                      <div className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-2">
                        <button
                          type="button"
                          onClick={() => {
                            const next = !stoppedSelectionMode;
                            setStoppedSelectionMode(next);
                            if (!next) {
                              setSelectedStoppedIds(new Set());
                            }
                          }}
                          className={cn(
                            'niuu-inline-flex niuu-items-center niuu-gap-2 niuu-rounded-md niuu-px-2.5 niuu-py-1.5 niuu-font-mono niuu-text-[10px] niuu-transition-colors',
                            stoppedSelectionMode
                              ? 'niuu-bg-brand/15 niuu-text-brand'
                              : 'niuu-text-text-muted hover:niuu-bg-bg-elevated hover:niuu-text-text-primary',
                          )}
                          data-testid="toggle-stopped-selection-button"
                        >
                          <Trash2 className="niuu-h-3.5 niuu-w-3.5" />
                          {stoppedSelectionMode
                            ? 'cancel delete select'
                            : 'select stopped to delete'}
                        </button>
                        <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-faint">
                          {deleteSelectionCount} selected
                        </span>
                      </div>

                      {stoppedSelectionMode ? (
                        <div className="niuu-mt-2 niuu-flex niuu-flex-wrap niuu-gap-2">
                          <button
                            type="button"
                            onClick={handleSelectAllVisibleStopped}
                            disabled={filteredStoppedSessions.length === 0}
                            className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-px-2.5 niuu-py-1 niuu-font-mono niuu-text-[10px] niuu-text-text-muted hover:niuu-bg-bg-elevated disabled:niuu-cursor-not-allowed disabled:niuu-opacity-50"
                            data-testid="select-all-stopped-button"
                          >
                            select visible stopped ({filteredStoppedSessions.length})
                          </button>
                          <button
                            type="button"
                            onClick={handleClearStoppedSelection}
                            disabled={deleteSelectionCount === 0}
                            className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-px-2.5 niuu-py-1 niuu-font-mono niuu-text-[10px] niuu-text-text-muted hover:niuu-bg-bg-elevated disabled:niuu-cursor-not-allowed disabled:niuu-opacity-50"
                            data-testid="clear-stopped-selection-button"
                          >
                            clear
                          </button>
                          <button
                            type="button"
                            onClick={() => setDeleteStoppedOpen(true)}
                            disabled={deleteSelectionCount === 0}
                            className="niuu-rounded-md niuu-border niuu-border-red-500/35 niuu-bg-red-500/10 niuu-px-2.5 niuu-py-1 niuu-font-mono niuu-text-[10px] niuu-text-red-200 hover:niuu-bg-red-500/15 disabled:niuu-cursor-not-allowed disabled:niuu-opacity-50"
                            data-testid="delete-selected-stopped-button"
                          >
                            delete selected ({deleteSelectionCount})
                          </button>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </div>
              )}

              <div className="niuu-flex-1 niuu-overflow-y-auto niuu-pb-1.5">
                {sidebarGroups.map((g) => (
                  <PodGroup
                    key={g.label}
                    label={g.label}
                    sessions={g.sessions}
                    selectedId={selectedSessionId}
                    onSelect={handleSelectSession}
                    selectableSessionIds={stoppedSelectionMode ? filteredStoppedIds : undefined}
                    selectedSessionIds={selectedStoppedIds}
                    onToggleSelection={handleToggleStoppedSelection}
                  />
                ))}
              </div>
            </div>
          )}
        </nav>

        {/* ── Main content: session detail ───────────────────────── */}
        <div
          aria-hidden="true"
          className="niuu-h-full niuu-flex-shrink-0"
          style={{
            width: '3px',
            background:
              'linear-gradient(to right, rgba(255,255,255,0.12), rgba(255,255,255,0.30), rgba(255,255,255,0.12))',
          }}
        />

        <div className="niuu-flex niuu-min-w-0 niuu-flex-1 niuu-flex-col niuu-overflow-hidden">
          {sessionsQuery.isLoading && <LoadingState label="Loading sessions…" />}
          {sessionsQuery.isError && (
            <ErrorState
              title="Failed to load sessions"
              message={
                sessionsQuery.error instanceof Error ? sessionsQuery.error.message : 'Unknown error'
              }
            />
          )}
          {sessionsQuery.data && !selectedSessionId && (
            <EmptyState
              title="No session selected"
              description="Select a session from the sidebar."
            />
          )}
          {sessionsQuery.data && selectedSessionId && (
            <LiveSessionDetailPage key={selectedSessionId} sessionId={selectedSessionId} />
          )}
        </div>
      </div>
      <Dialog open={deleteStoppedOpen} onOpenChange={setDeleteStoppedOpen}>
        <DialogContent
          title="Delete Selected Stopped Sessions"
          description="This removes the selected stopped sessions from the list. This action cannot be undone."
        >
          <div className="niuu-space-y-4">
            <div className="niuu-rounded-lg niuu-border niuu-border-red-500/25 niuu-bg-red-500/8 niuu-p-3 niuu-text-sm niuu-text-text-secondary">
              {deleteSelectionCount === 1
                ? 'Delete 1 stopped session?'
                : `Delete ${deleteSelectionCount} stopped sessions?`}
            </div>
            {deleteSelectionPreview.length > 0 ? (
              <div className="niuu-max-h-48 niuu-space-y-2 niuu-overflow-y-auto niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-p-3">
                {deleteSelectionPreview.map((session) => (
                  <div
                    key={session.id}
                    className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-3 niuu-font-mono niuu-text-xs niuu-text-text-secondary"
                  >
                    <span className="niuu-truncate">{session.personaName || session.id}</span>
                    <span className="niuu-text-text-faint">{session.id}</span>
                  </div>
                ))}
              </div>
            ) : null}
            <div className="niuu-flex niuu-justify-end niuu-gap-2">
              <button
                type="button"
                onClick={() => setDeleteStoppedOpen(false)}
                className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-primary niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-primary hover:niuu-bg-bg-secondary"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleDeleteSelectedStopped()}
                disabled={deleteBusy || deleteSelectionCount === 0}
                className="niuu-rounded-md niuu-bg-red-500 niuu-px-3 niuu-py-2 niuu-text-sm niuu-font-medium niuu-text-white hover:niuu-opacity-90 disabled:niuu-cursor-not-allowed disabled:niuu-opacity-50"
                data-testid="confirm-delete-selected-stopped-button"
              >
                {deleteBusy ? 'Deleting…' : `Delete ${deleteSelectionCount}`}
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
      <LaunchWizard open={launchOpen} onOpenChange={setLaunchOpen} />
    </>
  );
}
