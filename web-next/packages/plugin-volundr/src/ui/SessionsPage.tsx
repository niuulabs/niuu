import { useEffect, useMemo, useState } from 'react';
import { LoadingState, ErrorState, EmptyState, StateDot, relTime, cn } from '@niuulabs/ui';
import type { DotState } from '@niuulabs/ui';
import { Clock3, FolderGit2, Search, SquareTerminal, Ticket } from 'lucide-react';
import { useSessionList } from './hooks/useSessionStore';
import { groupByState } from './sessions/groupByState';
import { LiveSessionDetailPage } from './LiveSessionDetailPage';
import type { Session, SessionState } from '../domain/session';

// ---------------------------------------------------------------------------
// Pod group definitions — maps display labels to session states
// ---------------------------------------------------------------------------

interface PodGroupDef {
  label: string;
  states: SessionState[];
}

type SidebarMode = 'state' | 'repo';

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

// ---------------------------------------------------------------------------
// PodEntry — a single session row in the sidebar
// ---------------------------------------------------------------------------

function PodEntry({
  session,
  selected,
  onSelect,
  collapsed = false,
}: {
  session: Session;
  selected: boolean;
  onSelect: () => void;
  collapsed?: boolean;
}) {
  const ageLabel = relTime(new Date(session.lastActivityAt ?? session.startedAt).getTime());
  const primaryLabel = session.personaName || session.id;
  const trackerLabel = session.sagaId ?? session.raidId ?? session.ravnId;
  const previewLabel = session.preview;
  const sourceParts =
    previewLabel && looksLikeRepoLabel(previewLabel) ? compactSourceParts(previewLabel) : null;
  const showPreviewFallback = previewLabel && !sourceParts;
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
}: {
  label: string;
  sessions: Session[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  collapsed?: boolean;
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
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SessionsPage — master-detail layout
// ---------------------------------------------------------------------------

export function SessionsPage() {
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarMode, setSidebarMode] = useState<SidebarMode>('state');

  const sessionsQuery = useSessionList();
  const allSessions = useMemo(() => sessionsQuery.data ?? [], [sessionsQuery.data]);

  // Filter by search query
  const filteredSessions = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return allSessions;
    return allSessions.filter(
      (s) =>
        s.id.toLowerCase().includes(q) ||
        s.personaName.toLowerCase().includes(q) ||
        s.preview?.toLowerCase().includes(q),
    );
  }, [allSessions, searchQuery]);

  // Group by state
  const grouped = useMemo(() => groupByState(filteredSessions), [filteredSessions]);

  // Build sidebar groups — flatten matching states per display group
  const sidebarGroups = useMemo<SessionSection[]>(() => {
    if (sidebarMode === 'repo') {
      return groupByRepo(filteredSessions);
    }
    return POD_GROUPS.map((g) => ({
      label: g.label,
      sessions: g.states.flatMap((st) => grouped[st]),
    }));
  }, [filteredSessions, grouped, sidebarMode]);

  // Auto-select first running session on load
  useEffect(() => {
    if (selectedSessionId) return;
    if (!sessionsQuery.data) return;
    const running = sessionsQuery.data.filter((s) => s.state === 'running');
    if (running.length > 0) {
      setSelectedSessionId(running[0]!.id);
    } else if (sessionsQuery.data.length > 0) {
      setSelectedSessionId(sessionsQuery.data[0]!.id);
    }
  }, [sessionsQuery.data, selectedSessionId]);

  return (
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
                  onSelect={setSelectedSessionId}
                  collapsed
                />
              ))}
            </div>
          </div>
        ) : (
          <div className="niuu-flex niuu-h-full niuu-flex-col niuu-overflow-hidden">
            <div className="niuu-flex niuu-items-center niuu-justify-between niuu-border-b niuu-border-white/8 niuu-px-2.5 niuu-py-2">
              <div className="niuu-flex niuu-items-center niuu-gap-1.5">
                <h2 className="niuu-text-sm niuu-font-semibold niuu-text-text-primary">Sessions</h2>
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
              <span className="niuu-text-[10px] niuu-font-mono niuu-text-text-faint">group by</span>
              <div
                className="niuu-inline-flex niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-p-0.5"
                data-testid="pod-group-mode"
              >
                {(['state', 'repo'] as const).map((mode) => {
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
              <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-px-3 niuu-py-1 niuu-shadow-[inset_0_1px_0_rgba(255,255,255,0.02)] focus-within:niuu-border-brand/50 focus-within:niuu-ring-1 focus-within:niuu-ring-brand/20">
                <Search
                  className="niuu-h-4 niuu-w-4 niuu-flex-shrink-0 niuu-text-text-muted"
                  aria-hidden="true"
                />
                <input
                  type="search"
                  placeholder="filter by name / repo / branch"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="niuu-min-w-0 niuu-flex-1 niuu-bg-transparent niuu-py-0.5 niuu-text-[11px] niuu-text-text-primary placeholder:niuu-text-text-muted focus:niuu-outline-none"
                  data-testid="pod-search"
                  aria-label="Filter sessions"
                />
              </div>
            </div>

            <div className="niuu-flex-1 niuu-overflow-y-auto niuu-pb-1.5">
              {sidebarGroups.map((g) => (
                <PodGroup
                  key={g.label}
                  label={g.label}
                  sessions={g.sessions}
                  selectedId={selectedSessionId}
                  onSelect={setSelectedSessionId}
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
  );
}
