import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { LoadingState, ErrorState, EmptyState, cn, Dialog, DialogContent } from '@niuulabs/ui';
import { Search, Download, Trash2 } from 'lucide-react';
import { ImportExternalSessionsDialog } from './ImportExternalSessionsDialog';
import { QuickLaunch } from './QuickLaunch';
import { useSessionList } from './hooks/useSessionStore';
import { groupByState } from './sessions/groupByState';
import { PodGroup } from './sessions/PodGroup';
import { useSessionListWidth } from './sessions/useSessionListWidth';
import {
  POD_GROUPS,
  STOPPABLE_STATES,
  filterSessionsByQuery,
  groupByForge,
  groupByRepo,
  type SessionSection,
} from './sessions/sessionLabels';
import { LiveSessionDetailPage } from './LiveSessionDetailPage';
import { useShowDebugMeta, setShowDebugMeta } from './uxPrefs';
import type { IVolundrService } from '../ports/IVolundrService';
import './session-card.css';

type SidebarMode = 'state' | 'repo' | 'forge';

// ---------------------------------------------------------------------------
// SessionsPage — master-detail layout
// ---------------------------------------------------------------------------

// Compact UX: foldable groups + hide-archived, persisted in localStorage.
const FOLDED_GROUPS_KEY = 'niuu.compactUx.sessions.foldedGroups';
const HIDE_ARCHIVED_KEY = 'niuu.compactUx.sessions.hideArchived';
/** Groups folded away by default on first load. */
const DEFAULT_FOLDED_GROUPS = ['ARCHIVED', 'STOPPED'];

function readFoldedGroups(): Record<string, boolean> {
  const seed = Object.fromEntries(DEFAULT_FOLDED_GROUPS.map((g) => [g, true]));
  if (typeof window === 'undefined') return seed;
  try {
    const raw = window.localStorage.getItem(FOLDED_GROUPS_KEY);
    if (!raw) return seed;
    const parsed = JSON.parse(raw) as Record<string, boolean>;
    return parsed && typeof parsed === 'object' ? parsed : seed;
  } catch {
    return seed;
  }
}

function readHideArchived(): boolean {
  if (typeof window === 'undefined') return true;
  try {
    return window.localStorage.getItem(HIDE_ARCHIVED_KEY) !== '0';
  } catch {
    return true;
  }
}

export function SessionsPage() {
  const showDebugMeta = useShowDebugMeta();
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
  const [deleteSessionId, setDeleteSessionId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [launchOpen, setLaunchOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  // Mini (local, no-k8s) deployments get the simple Quick Launch; cluster mode
  // keeps the full LaunchWizard.
  const { width: sidebarWidth, resizing, separatorProps } = useSessionListWidth(sidebarCollapsed);
  const [foldedGroups, setFoldedGroups] = useState<Record<string, boolean>>(readFoldedGroups);
  const [hideArchived, setHideArchived] = useState<boolean>(readHideArchived);
  useEffect(() => {
    try {
      window.localStorage.setItem(FOLDED_GROUPS_KEY, JSON.stringify(foldedGroups));
    } catch {
      /* localStorage unavailable — non-fatal */
    }
  }, [foldedGroups]);
  useEffect(() => {
    try {
      window.localStorage.setItem(HIDE_ARCHIVED_KEY, hideArchived ? '1' : '0');
    } catch {
      /* localStorage unavailable — non-fatal */
    }
  }, [hideArchived]);
  const toggleGroupFold = (label: string) => {
    setFoldedGroups((prev) => ({ ...prev, [label]: !prev[label] }));
  };
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();

  const sessionsQuery = useSessionList();
  const allSessions = useMemo(() => sessionsQuery.data ?? [], [sessionsQuery.data]);
  const archivedCount = allSessions.filter((session) => session.state === 'archived').length;
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
  const filteredSessions = useMemo(
    () => filterSessionsByQuery(allSessions, searchQuery),
    [allSessions, searchQuery],
  );
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

  // Build sidebar groups — flatten matching states per display group.
  // When hideArchived is on (and we're grouping by state) the ARCHIVED group
  // is dropped entirely.
  const sidebarGroups = useMemo<SessionSection[]>(() => {
    if (sidebarMode === 'repo') {
      return groupByRepo(filteredSessions);
    }
    if (sidebarMode === 'forge') {
      return groupByForge(filteredSessions);
    }
    return POD_GROUPS.filter((g) => !(hideArchived && g.label === 'ARCHIVED')).map((g) => ({
      label: g.label,
      sessions: g.states.flatMap((st) => grouped[st]),
    }));
  }, [filteredSessions, grouped, sidebarMode, hideArchived]);
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
    try {
      await volundr.archiveStoppedSessions();
      await refreshSessions();
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
    try {
      await Promise.all(ids.map((id) => volundr.deleteSession(id)));
      if (resolvedSelectedSessionId && ids.includes(resolvedSelectedSessionId)) {
        setSelectedSessionId(null);
        await navigate({ to: '/volundr/sessions', replace: true });
      }
      setDeleteStoppedOpen(false);
      setStoppedSelectionMode(false);
      setSelectedStoppedIds(new Set());
      await refreshSessions();
    } finally {
      setDeleteBusy(false);
    }
  }

  const deleteSelectionCount = resolvedSelectedStoppedIds.size;
  const deleteSelectionPreview = filteredStoppedSessions.filter((session) =>
    resolvedSelectedStoppedIds.has(session.id),
  );

  async function refreshSessions() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'history'] }),
    ]);
    await sessionsQuery.refetch();
  }

  async function handleDeleteSession() {
    if (!deleteSessionId || rowBusy) return;
    const id = deleteSessionId;
    setRowBusy(id);
    setRowError(null);
    try {
      await volundr.deleteSession(id);
      setDeleteSessionId(null);
      if (resolvedSelectedSessionId === id) {
        setSelectedSessionId(null);
        await navigate({ to: '/volundr/sessions', replace: true });
      }
      await refreshSessions();
    } catch (error) {
      setRowError(error instanceof Error ? error.message : 'Session deletion failed');
    } finally {
      setRowBusy(null);
    }
  }

  // Hover action: stop a session in place.
  async function handleStopSession(id: string) {
    if (rowBusy) return;
    setRowBusy(id);
    setRowError(null);
    try {
      await volundr.stopSession(id);
      await refreshSessions();
    } catch (error) {
      setRowError(error instanceof Error ? error.message : 'Session action failed');
    } finally {
      setRowBusy(null);
    }
  }

  // Hover action: "archive" == stop (if still active) then archive.
  async function handleArchiveSession(id: string) {
    if (rowBusy) return;
    setRowBusy(id);
    setRowError(null);
    try {
      const target = allSessions.find((s) => s.id === id);
      if (target && STOPPABLE_STATES.includes(target.state)) {
        await volundr.stopSession(id);
      }
      await volundr.archiveSession(id);
      await refreshSessions();
    } catch (error) {
      setRowError(error instanceof Error ? error.message : 'Session action failed');
    } finally {
      setRowBusy(null);
    }
  }

  return (
    <>
      <div className="niuu:relative niuu:flex niuu:h-full" data-testid="sessions-page">
        {/* ── Left sidebar: pod list ─────────────────────────────── */}
        <nav
          className={cn(
            'niuu:relative niuu:shrink-0 niuu:overflow-hidden niuu:bg-bg-primary',
            !resizing && 'niuu:transition-[width] niuu:duration-200',
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
                  width: `${sidebarWidth}px`,
                  minWidth: `${sidebarWidth}px`,
                  maxWidth: `${sidebarWidth}px`,
                  flexBasis: `${sidebarWidth}px`,
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
              <div className="niuu:flex-1 niuu:min-h-0 niuu:overflow-y-auto niuu:py-2 niuu-scroll-themed">
                {sidebarGroups.map((g) => (
                  <PodGroup
                    key={g.label}
                    label={g.label}
                    sessions={g.sessions}
                    selectedId={resolvedSelectedSessionId}
                    onSelect={handleSelectSession}
                    collapsed
                  />
                ))}
              </div>
            </div>
          ) : (
            <div className="niuu:flex niuu:h-full niuu:flex-col niuu:overflow-hidden">
              <div className="niuu:flex niuu:items-center niuu:justify-between niuu:border-b niuu:border-white/8 niuu:px-3 niuu:py-2">
                <div className="niuu:flex niuu:items-center niuu:gap-1.5">
                  <button
                    type="button"
                    aria-pressed={showDebugMeta}
                    onClick={() => setShowDebugMeta(!showDebugMeta)}
                    title="Show debug metadata"
                    className="niuu:rounded niuu:border niuu:border-border-subtle niuu:px-1 niuu:text-[10px] niuu:text-text-muted"
                  >
                    Details
                  </button>
                  <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                    Sessions
                  </h2>
                  <span
                    className="niuu:rounded-full niuu:bg-bg-elevated niuu:px-1.5 niuu:py-0.5 niuu:font-mono niuu:text-[10px] niuu:text-text-muted"
                    data-testid="pod-count"
                  >
                    {allSessions.length}
                  </span>
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

              <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:px-3 niuu:py-1">
                <span className="niuu:text-[10px] niuu:font-mono niuu:text-text-secondary">
                  group by
                </span>
                <div
                  className="niuu:inline-flex niuu:gap-1 niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:p-1"
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
                          'niuu:rounded-md niuu:px-3 niuu:py-1 niuu:font-mono niuu:text-[10px] niuu:transition-colors',
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
                {archivedCount > 0 ? (
                  <button
                    type="button"
                    onClick={() => setHideArchived((v) => !v)}
                    className={cn(
                      'niuu:ml-auto niuu:rounded-md niuu:border niuu:border-border-subtle niuu:px-2 niuu:py-1 niuu:font-mono niuu:text-[10px] niuu:transition-colors',
                      hideArchived
                        ? 'niuu:text-text-muted niuu:hover:text-text-primary'
                        : 'niuu:bg-brand/15 niuu:text-brand',
                    )}
                    data-testid="pod-toggle-archived"
                    aria-pressed={!hideArchived}
                    title={hideArchived ? 'Show archived sessions' : 'Hide archived sessions'}
                  >
                    {hideArchived ? `show archived (${archivedCount})` : 'hide archived'}
                  </button>
                ) : null}
              </div>

              <div className="niuu:px-3 niuu:pb-1">
                <div className="niuu:flex niuu:items-center niuu:gap-2">
                  <button
                    type="button"
                    onClick={() => setLaunchOpen(true)}
                    className="niuu:flex niuu:h-7 niuu:w-7 niuu:flex-shrink-0 niuu:items-center niuu:justify-center niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:text-sm niuu:font-semibold niuu:text-text-muted niuu:transition-colors niuu:hover:border-brand/40 niuu:hover:text-brand"
                    data-testid="pod-launch-button"
                    aria-label="Launch a new session"
                    title="Launch a new session"
                  >
                    +
                  </button>
                  <button
                    type="button"
                    onClick={() => setImportOpen(true)}
                    className="niuu:flex niuu:h-7 niuu:w-7 niuu:flex-shrink-0 niuu:items-center niuu:justify-center niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:text-text-muted niuu:transition-colors niuu:hover:border-brand/40 niuu:hover:text-brand"
                    data-testid="pod-import-button"
                    aria-label="Import external CLI sessions"
                    title="Import external CLI sessions"
                  >
                    <Download className="niuu:h-3.5 niuu:w-3.5" />
                  </button>
                  <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:items-center niuu:gap-2 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:shadow-[inset_0_1px_0_rgba(255,255,255,0.02)] niuu:focus-within:border-brand/50 niuu:focus-within:ring-1 niuu:focus-within:ring-brand/20">
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
                <div className="niuu:px-2.5 niuu:pb-2">
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
                            if (!next) {
                              setSelectedStoppedIds(new Set());
                            }
                          }}
                          className={cn(
                            'niuu:inline-flex niuu:items-center niuu:gap-2 niuu:rounded-md niuu:px-2.5 niuu:py-1.5 niuu:font-mono niuu:text-[10px] niuu:transition-colors',
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
                </div>
              )}

              {rowBusy && (
                <p
                  role="status"
                  className="niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-secondary"
                >
                  Updating session…
                </p>
              )}
              {rowError && !deleteSessionId && (
                <p role="alert" className="niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-critical">
                  {rowError}
                </p>
              )}
              <div className="niuu:flex-1 niuu:min-h-0 niuu:overflow-y-auto niuu:pb-1.5 niuu-scroll-themed">
                {sidebarGroups.map((g) => (
                  <PodGroup
                    key={g.label}
                    label={g.label}
                    sessions={g.sessions}
                    selectedId={resolvedSelectedSessionId}
                    onSelect={handleSelectSession}
                    onStop={handleStopSession}
                    onArchive={handleArchiveSession}
                    onDelete={setDeleteSessionId}
                    busyId={rowBusy}
                    folded={
                      stoppedSelectionMode && g.label === 'STOPPED'
                        ? false
                        : Boolean(foldedGroups[g.label])
                    }
                    onToggleFold={() => toggleGroupFold(g.label)}
                    selectableSessionIds={stoppedSelectionMode ? filteredStoppedIds : undefined}
                    selectedSessionIds={resolvedSelectedStoppedIds}
                    onToggleSelection={handleToggleStoppedSelection}
                  />
                ))}
              </div>
            </div>
          )}
        </nav>

        {/* ── Resizable divider: drag to set the left column width ── */}
        <div {...separatorProps} />

        <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col niuu:overflow-hidden">
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
        open={deleteSessionId !== null}
        onOpenChange={(open) => {
          if (!open && !rowBusy) setDeleteSessionId(null);
        }}
      >
        <DialogContent
          title="Delete Session"
          description="This permanently deletes the session and its workspace storage. This action cannot be undone."
        >
          <p className="niuu:mb-4 niuu:text-sm niuu:text-text-secondary">
            {allSessions.find((session) => session.id === deleteSessionId)?.name || deleteSessionId}
          </p>
          {rowError && <p role="alert">{rowError}</p>}
          <div className="niuu:flex niuu:justify-end niuu:gap-2">
            <button
              type="button"
              className="niuu:px-3 niuu:py-2"
              disabled={!!rowBusy}
              onClick={() => setDeleteSessionId(null)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="niuu:rounded-md niuu:bg-critical niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary"
              disabled={!!rowBusy}
              onClick={() => void handleDeleteSession()}
              data-testid="confirm-delete-session-button"
            >
              {rowBusy ? 'Deleting…' : 'Delete'}
            </button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog open={deleteStoppedOpen} onOpenChange={setDeleteStoppedOpen}>
        <DialogContent
          title="Delete Selected Stopped Sessions"
          description="This removes the selected stopped sessions from the list. This action cannot be undone."
        >
          <div className="niuu:space-y-4">
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
      <QuickLaunch open={launchOpen} onOpenChange={setLaunchOpen} />
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
