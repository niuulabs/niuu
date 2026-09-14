import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from '@tanstack/react-router';
import { useService } from '@niuulabs/plugin-sdk';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Popover,
  PopoverContent,
  PopoverTrigger,
  cn,
} from '@niuulabs/ui';
import { Archive, Download, MoreHorizontal, Plus, Search } from 'lucide-react';
import { ImportExternalSessionsDialog } from './ImportExternalSessionsDialog';
import { LiveSessionDetailPage } from './LiveSessionDetailPage';
import { useSessionList } from './hooks/useSessionStore';
import { PodGroup } from './sessions/PodGroup';
import { useSessionListWidth } from './sessions/useSessionListWidth';
import { filterSessionsByQuery, groupForSimpleMode } from './sessions/sessionLabels';
import type { IVolundrService } from '../ports/IVolundrService';
import './session-card.css';

const MENU_ITEM =
  'niuu:flex niuu:w-full niuu:items-center niuu:gap-2 niuu:rounded-md niuu:px-2 niuu:py-1.5 niuu:text-left niuu:text-xs niuu:text-text-secondary niuu:hover:bg-bg-elevated niuu:hover:text-text-primary niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50';

/**
 * Simple-mode session list: what needs you, what is running, what finished —
 * and the live session beside it. Advanced grouping, bulk delete and the debug
 * metadata toggle stay on the Advanced page.
 */
export function SimpleSessionsPage() {
  const navigate = useNavigate();
  const { sessionId: routeSessionId } = useParams({ strict: false });
  const volundr = useService<IVolundrService>('volundr');
  const sessionsQuery = useSessionList();

  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [showOlder, setShowOlder] = useState(false);
  const { width, resizing, separatorProps } = useSessionListWidth();

  const allSessions = useMemo(() => sessionsQuery.data ?? [], [sessionsQuery.data]);
  const filtered = useMemo(
    () => filterSessionsByQuery(allSessions, searchQuery),
    [allSessions, searchQuery],
  );
  const groups = useMemo(() => groupForSimpleMode(filtered), [filtered]);
  const stoppedCount = allSessions.filter((session) => session.state === 'terminated').length;

  const resolvedSelectedSessionId = useMemo(() => {
    if (allSessions.length === 0) return null;
    const requested = typeof routeSessionId === 'string' ? routeSessionId : null;
    if (requested && allSessions.some((session) => session.id === requested)) return requested;
    if (selectedSessionId && allSessions.some((session) => session.id === selectedSessionId)) {
      return selectedSessionId;
    }
    const running = allSessions.find((session) => session.state === 'running');
    return running?.id ?? allSessions[0]?.id ?? null;
  }, [allSessions, routeSessionId, selectedSessionId]);

  function handleSelectSession(id: string) {
    setSelectedSessionId(id);
    void navigate({ to: '/volundr/sessions/$sessionId', params: { sessionId: id } });
  }

  async function handleArchiveAllStopped() {
    if (archiveBusy || stoppedCount === 0) return;
    setMenuOpen(false);
    setArchiveBusy(true);
    setActionError(null);
    try {
      await volundr.archiveStoppedSessions();
      await sessionsQuery.refetch();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Archiving stopped sessions failed');
    } finally {
      setArchiveBusy(false);
    }
  }

  const hasAnySession = allSessions.length > 0;

  return (
    <>
      {actionError ? (
        <p role="alert" className="niuu:px-3 niuu:py-1 niuu:text-xs niuu:text-critical">
          {actionError}
        </p>
      ) : null}
      <div className="niuu:relative niuu:flex niuu:h-full" data-testid="simple-sessions-page">
        <nav
          className={cn(
            'niuu:relative niuu:flex niuu:shrink-0 niuu:flex-col niuu:overflow-hidden niuu:bg-bg-primary',
            !resizing && 'niuu:transition-[width] niuu:duration-200',
          )}
          style={{
            width: `${width}px`,
            minWidth: `${width}px`,
            maxWidth: `${width}px`,
            flexBasis: `${width}px`,
          }}
          aria-label="Session list"
          data-testid="simple-session-list"
        >
          <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2 niuu:border-b niuu:border-white/8 niuu:px-3 niuu:py-2.5">
            <h2 className="niuu:m-0 niuu:flex niuu:items-baseline niuu:gap-1.5 niuu:text-sm niuu:font-semibold niuu:text-text-primary">
              Sessions
              <span
                className="niuu:font-mono niuu:text-[11px] niuu:font-normal niuu:text-text-muted"
                data-testid="simple-session-count"
              >
                · {allSessions.length}
              </span>
            </h2>
            <Link
              to="/volundr/sessions/new"
              className="niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-full niuu:border niuu:border-border-subtle niuu:px-2.5 niuu:py-1 niuu:text-[11px] niuu:text-text-secondary niuu:transition-colors niuu:hover:border-brand/40 niuu:hover:text-brand"
              data-testid="simple-session-new"
            >
              <Plus className="niuu:h-3 niuu:w-3" aria-hidden="true" />
              New
            </Link>
          </div>

          <div className="niuu:px-3 niuu:py-2">
            <div className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-2 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:focus-within:border-brand/50">
              <Search
                className="niuu:h-4 niuu:w-4 niuu:flex-shrink-0 niuu:text-text-muted"
                aria-hidden="true"
              />
              <input
                type="search"
                placeholder="search sessions"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                className="niuu:min-w-0 niuu:flex-1 niuu:bg-transparent niuu:py-0.5 niuu:text-[11px] niuu:text-text-primary niuu:placeholder:text-text-muted niuu:focus:outline-none"
                data-testid="simple-session-search"
                aria-label="Search sessions"
              />
            </div>
          </div>

          <div className="niuu:flex-1 niuu:min-h-0 niuu:overflow-y-auto niuu-scroll-themed">
            {sessionsQuery.isLoading ? <LoadingState label="Loading sessions…" /> : null}
            {!hasAnySession && sessionsQuery.data ? (
              <EmptyState
                title="No sessions yet"
                description="Start one and an agent gets to work in its own sandbox."
                action={
                  <Link
                    to="/volundr/sessions/new"
                    className="niuu:text-xs niuu:text-brand niuu:underline niuu:underline-offset-4"
                  >
                    Start a session
                  </Link>
                }
              />
            ) : null}
            <PodGroup
              label="Needs you"
              sessions={groups.needsYou}
              selectedId={resolvedSelectedSessionId}
              onSelect={handleSelectSession}
            />
            <PodGroup
              label="Running"
              sessions={groups.running}
              selectedId={resolvedSelectedSessionId}
              onSelect={handleSelectSession}
            />
            <PodGroup
              label="Done today"
              sessions={groups.doneToday}
              selectedId={resolvedSelectedSessionId}
              onSelect={handleSelectSession}
            />
            <PodGroup
              label="Older"
              sessions={groups.older}
              selectedId={resolvedSelectedSessionId}
              onSelect={handleSelectSession}
              folded={!showOlder}
              onToggleFold={() => setShowOlder((open) => !open)}
            />
            <PodGroup
              label="Archived"
              hideHeader
              sessions={showArchived ? groups.archived : []}
              selectedId={resolvedSelectedSessionId}
              onSelect={handleSelectSession}
            />
          </div>

          <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2 niuu:border-t niuu:border-white/8 niuu:px-3 niuu:py-2">
            <button
              type="button"
              onClick={() => setShowArchived((open) => !open)}
              aria-expanded={showArchived}
              className="niuu:font-mono niuu:text-[10px] niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted niuu:hover:text-text-primary"
              data-testid="simple-session-archived-toggle"
            >
              Archived · {groups.archived.length}
            </button>
            <Popover open={menuOpen} onOpenChange={setMenuOpen}>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  aria-label="Session list actions"
                  className="niuu:flex niuu:h-6 niuu:w-6 niuu:items-center niuu:justify-center niuu:rounded-md niuu:text-text-muted niuu:hover:bg-bg-elevated niuu:hover:text-text-primary"
                  data-testid="simple-session-menu"
                >
                  <MoreHorizontal className="niuu:h-4 niuu:w-4" aria-hidden="true" />
                </button>
              </PopoverTrigger>
              <PopoverContent align="end" side="top" className="niuu:w-56">
                <div className="niuu:flex niuu:flex-col niuu:gap-1">
                  <button
                    type="button"
                    className={MENU_ITEM}
                    onClick={() => {
                      setMenuOpen(false);
                      setImportOpen(true);
                    }}
                    data-testid="simple-session-import"
                  >
                    <Download className="niuu:h-3.5 niuu:w-3.5" aria-hidden="true" />
                    Import external sessions
                  </button>
                  <button
                    type="button"
                    className={MENU_ITEM}
                    disabled={archiveBusy || stoppedCount === 0}
                    onClick={() => void handleArchiveAllStopped()}
                    data-testid="simple-session-archive-stopped"
                  >
                    <Archive className="niuu:h-3.5 niuu:w-3.5" aria-hidden="true" />
                    {archiveBusy ? 'Archiving stopped…' : `Archive all stopped (${stoppedCount})`}
                  </button>
                </div>
              </PopoverContent>
            </Popover>
          </div>
        </nav>

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
              title="No session open"
              description="Pick one on the left, or start a new one."
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
