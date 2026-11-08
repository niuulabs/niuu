import { useMemo, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { NOTIFICATION_KIND_LABELS } from '@niuulabs/ui';
import { Check, Folder, Search } from 'lucide-react';
import { isCoordinator } from '../domain/projectTree';
import {
  DEFAULT_NOTIFICATION_FILTER,
  type NotificationFilter,
  type SessionNotification,
  notificationKey,
} from '../domain/notifications';
import type { ForgeProject } from '../models/volundr.model';
import type { Session } from '../domain/session';
import { errorText } from './errorText';
import { useNotifications } from './hooks/useNotifications';
import { ProjectSettings } from './ProjectSettings';
import { SESSION_STATE_LABELS, sessionModelLabel } from './sessions/presentation';
import { compactDuration, sessionTimeFact, useMinuteClock } from './sessions/timeFact';
import './ProjectHome.css';

/** Where "All updates" goes: the Forge notifications page. */
export const NOTIFICATIONS_PATH = '/volundr/notifications';

/** An ask is a notice that waits for the operator to answer. */
const ASKING_KINDS = new Set<SessionNotification['kind']>(['decision', 'attention']);

export interface ProjectHomeProps {
  projectId: string;
  /** What the folder is called in the list. */
  name: string;
  /** The project's record; absent when sessions name a project the host does not list. */
  project?: ForgeProject;
  /** Every session the host reports; the home keeps this project's. */
  sessions: readonly Session[];
  /** Each coordinator's worker count among every session the host knows. */
  workerCounts: ReadonlyMap<string, number>;
  onOpenSession: (sessionId: string) => void;
  onNewSession: () => void;
  /** The project was archived from its settings; the home has nothing left to show. */
  onArchived: () => void;
}

function sessionLabel(session: Session): string {
  return session.name || session.personaName || session.id;
}

function hostLabel(session: Session): string | undefined {
  return session.clusterName ?? session.clusterId;
}

function joinParts(parts: ReadonlyArray<string | undefined | false>): string {
  return parts.filter(Boolean).join(' · ');
}

/** How long a session has been in its state, as the list counts it. */
function stateAge(session: Session, now: number): string | null {
  const since = Date.parse(session.activityStateSince ?? session.lastActivityAt ?? '');
  return Number.isFinite(since) ? compactDuration(now - since) : null;
}

function isStopped(session: Session): boolean {
  return session.state === 'terminated' || session.state === 'terminating';
}

/**
 * An ask is resolved once its session stopped waiting, or a newer ask took its place. A notice
 * that asks nothing has nothing to resolve.
 */
export function resolvedAsks(
  notifications: readonly SessionNotification[],
  sessions: readonly Session[],
): ReadonlySet<string> {
  const awaiting = new Set(
    sessions.filter((session) => session.state === 'awaiting_input').map((session) => session.id),
  );
  // Newest first: the first ask met for a session is the one still open.
  const open = new Set<string>();
  const resolved = new Set<string>();
  for (const notification of notifications) {
    if (!ASKING_KINDS.has(notification.kind) || !notification.sessionId) continue;
    const key = notificationKey(notification);
    const stillOpen = awaiting.has(notification.sessionId) && !open.has(notification.sessionId);
    if (stillOpen) open.add(notification.sessionId);
    else resolved.add(key);
  }
  return resolved;
}

function SessionCard({
  session,
  detail,
  now,
  onOpen,
}: {
  session: Session;
  detail: string;
  now: number;
  onOpen: () => void;
}) {
  const fact = sessionTimeFact(session, now);
  return (
    <button
      type="button"
      className="forge-project-card"
      onClick={onOpen}
      data-testid={`project-session-${session.id}`}
    >
      <span className="forge-project-card__text">
        <span className="forge-project-card__title">{sessionLabel(session)}</span>
        <span className="forge-project-card__detail">{detail}</span>
      </span>
      <span className="forge-project-card__state">
        <span className="forge-project-pill" data-state={session.state}>
          {SESSION_STATE_LABELS[session.state]}
        </span>
        {fact && <span className="forge-project-card__age">{fact}</span>}
      </span>
    </button>
  );
}

function Heading({ label, count }: { label: string; count?: number }) {
  return (
    <h3 className="forge-project-home__heading">
      {label}
      {count !== undefined && <span>{count}</span>}
    </h3>
  );
}

function UpdatesSection({
  projectId,
  sessions,
  query,
  now,
  onOpenSession,
}: {
  projectId: string;
  sessions: readonly Session[];
  query: string;
  now: number;
  onOpenSession: (sessionId: string) => void;
}) {
  const navigate = useNavigate();
  const filter = useMemo<NotificationFilter>(
    () => ({ ...DEFAULT_NOTIFICATION_FILTER, projectId }),
    [projectId],
  );
  const updates = useNotifications(filter);
  const resolved = useMemo(
    () => resolvedAsks(updates.notifications, sessions),
    [updates.notifications, sessions],
  );
  const projectSessionIds = useMemo(
    () => new Set(sessions.map((session) => session.id)),
    [sessions],
  );
  const shown = useMemo(
    () =>
      updates.notifications.filter(
        (notification) =>
          !query ||
          (notification.sessionName ?? '').toLowerCase().includes(query) ||
          notification.title.toLowerCase().includes(query),
      ),
    [updates.notifications, query],
  );

  if (updates.isLoading) {
    return (
      <section aria-label="Updates" className="forge-project-home__section">
        <Heading label="Updates" />
        <p className="forge-project-home__quiet" role="status">
          Loading updates…
        </p>
      </section>
    );
  }
  if (updates.isError) {
    return (
      <section aria-label="Updates" className="forge-project-home__section">
        <Heading label="Updates" />
        <div className="forge-project-home__error" role="alert" data-testid="project-updates-error">
          <span>Updates could not be loaded. {errorText(updates.error)}</span>
          <button type="button" onClick={updates.refetch}>
            Retry
          </button>
        </div>
      </section>
    );
  }
  if (shown.length === 0) return null;

  return (
    <section aria-label="Updates" className="forge-project-home__section">
      <Heading label="Updates" count={shown.length} />
      <ul className="forge-project-updates">
        {shown.map((notification) => {
          const isResolved = resolved.has(notificationKey(notification));
          const openable =
            notification.sessionId !== null && projectSessionIds.has(notification.sessionId);
          const body = (
            <>
              <span className="forge-project-update__meta">
                <span className="forge-project-update__kind" data-kind={notification.kind}>
                  {NOTIFICATION_KIND_LABELS[notification.kind]}
                </span>
                {notification.sessionName && (
                  <span className="forge-project-update__session">{notification.sessionName}</span>
                )}
                <span className="forge-project-update__age">
                  {compactDuration(now - Date.parse(notification.createdAt))}
                </span>
              </span>
              <span className="forge-project-update__title">{notification.title}</span>
              {isResolved && (
                <span className="forge-project-update__resolved">
                  <Check size={12} aria-hidden="true" />
                  Resolved
                </span>
              )}
            </>
          );
          return (
            <li
              key={notificationKey(notification)}
              data-testid="project-update"
              data-resolved={isResolved || undefined}
            >
              {openable ? (
                <button
                  type="button"
                  className="forge-project-update"
                  onClick={() => onOpenSession(notification.sessionId!)}
                >
                  {body}
                </button>
              ) : (
                <div className="forge-project-update">{body}</div>
              )}
            </li>
          );
        })}
      </ul>
      <button
        type="button"
        className="forge-project-home__link"
        onClick={() => void navigate({ to: NOTIFICATIONS_PATH })}
      >
        All updates ›
      </button>
    </section>
  );
}

/**
 * A project's home, as Lexi's: what needs the person, the coordinators and sessions, and the
 * updates its sessions sent.
 */
export function ProjectHome({
  projectId,
  name,
  project,
  sessions,
  workerCounts,
  onOpenSession,
  onNewSession,
  onArchived,
}: ProjectHomeProps) {
  const [query, setQuery] = useState('');
  const [showStopped, setShowStopped] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const now = useMinuteClock();

  const mine = useMemo(
    () =>
      sessions.filter(
        (session) => session.coordination?.projectId === projectId && session.state !== 'archived',
      ),
    [sessions, projectId],
  );
  const needle = query.trim().toLowerCase();
  const matching = useMemo(
    () => mine.filter((session) => !needle || sessionLabel(session).toLowerCase().includes(needle)),
    [mine, needle],
  );
  const live = matching.filter((session) => !isStopped(session));
  const stopped = matching.filter(isStopped);
  const needsYou = live.filter((session) => session.state === 'awaiting_input');
  const coordinators = live.filter((session) => isCoordinator(session, workerCounts));
  const others = live.filter((session) => !isCoordinator(session, workerCounts));

  function cardDetail(session: Session): string {
    const workers = workerCounts.get(session.id) ?? 0;
    return joinParts([
      sessionModelLabel(session),
      hostLabel(session),
      isCoordinator(session, workerCounts) && `${workers} ${workers === 1 ? 'worker' : 'workers'}`,
    ]);
  }

  return (
    <section
      className="forge-project-home"
      aria-label={`${name} project`}
      data-testid="project-home"
    >
      <header className="forge-project-home__head">
        <Folder size={26} aria-hidden="true" />
        <h1>{name}</h1>
        <div className="forge-project-home__actions">
          <button type="button" onClick={onNewSession} data-testid="project-new-session">
            New session
          </button>
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            disabled={!project}
            title={project ? undefined : 'This host has no record of the project to change'}
            data-testid="project-settings"
          >
            Settings…
          </button>
        </div>
      </header>

      <label className="forge-project-home__search">
        <Search size={16} aria-hidden="true" />
        <input
          type="search"
          placeholder={`Search ${name}`}
          aria-label={`Search ${name}`}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          data-testid="project-search"
        />
      </label>

      {mine.length === 0 && <p className="forge-project-home__quiet">No sessions yet</p>}
      {mine.length > 0 && matching.length === 0 && needle && (
        <p className="forge-project-home__quiet">No session matches “{query.trim()}”</p>
      )}

      {needsYou.length > 0 && (
        <section aria-label="Needs you" className="forge-project-home__section">
          <Heading label="Needs you" count={needsYou.length} />
          {needsYou.map((session) => {
            const age = stateAge(session, now);
            return (
              <button
                key={session.id}
                type="button"
                className="forge-project-card forge-project-card--attention"
                onClick={() => onOpenSession(session.id)}
                data-testid={`project-attention-${session.id}`}
              >
                <span className="forge-project-card__meta">
                  <span className="forge-project-card__flag">Waiting</span>
                  <span className="forge-project-card__sub">
                    {joinParts([sessionLabel(session), hostLabel(session)])}
                  </span>
                  {age && <span className="forge-project-card__age">{age}</span>}
                </span>
                <span className="forge-project-card__title">
                  {session.shortDescription || `${sessionLabel(session)} is waiting for you`}
                </span>
              </button>
            );
          })}
        </section>
      )}

      {coordinators.length > 0 && (
        <section
          aria-label={coordinators.length === 1 ? 'Coordinator' : 'Coordinators'}
          className="forge-project-home__section"
        >
          <Heading
            label={coordinators.length === 1 ? 'Coordinator' : 'Coordinators'}
            count={coordinators.length}
          />
          {coordinators.map((session) => (
            <SessionCard
              key={session.id}
              session={session}
              detail={cardDetail(session)}
              now={now}
              onOpen={() => onOpenSession(session.id)}
            />
          ))}
        </section>
      )}

      {others.length > 0 && (
        <section aria-label="Sessions" className="forge-project-home__section">
          <Heading label="Sessions" count={others.length} />
          {others.map((session) => (
            <SessionCard
              key={session.id}
              session={session}
              detail={cardDetail(session)}
              now={now}
              onOpen={() => onOpenSession(session.id)}
            />
          ))}
        </section>
      )}

      {stopped.length > 0 && (
        <section aria-label="Stopped sessions" className="forge-project-home__section">
          <div className="forge-project-home__stopped">
            <span>{stopped.length} stopped</span>
            <button
              type="button"
              aria-expanded={showStopped}
              onClick={() => setShowStopped(!showStopped)}
              data-testid="project-stopped-toggle"
            >
              {showStopped ? 'Hide' : 'Show'}
            </button>
          </div>
          {showStopped &&
            stopped.map((session) => (
              <SessionCard
                key={session.id}
                session={session}
                detail={cardDetail(session)}
                now={now}
                onOpen={() => onOpenSession(session.id)}
              />
            ))}
        </section>
      )}

      <UpdatesSection
        projectId={projectId}
        sessions={mine}
        query={needle}
        now={now}
        onOpenSession={onOpenSession}
      />

      {project && (
        <ProjectSettings
          project={project}
          open={settingsOpen}
          onOpenChange={setSettingsOpen}
          onArchived={() => {
            setSettingsOpen(false);
            onArchived();
          }}
        />
      )}
    </section>
  );
}
