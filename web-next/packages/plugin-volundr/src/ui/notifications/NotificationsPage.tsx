import { useCallback, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { BellOff } from 'lucide-react';
import { useService } from '@niuulabs/plugin-sdk';
import {
  EmptyState,
  ErrorState,
  LiveBadge,
  LoadingState,
  cn,
  notificationAnchorId,
} from '@niuulabs/ui';
import {
  DEFAULT_NOTIFICATION_FILTER,
  isFilterActive,
  isNotificationRead,
  notificationKey,
  readThroughFrom,
  type SessionNotification,
} from '../../domain/notifications';
import type { IVolundrService } from '../../ports/IVolundrService';
import {
  readEverythingThrough,
  useMarkNotificationsRead,
  useNotifications,
  useNotificationSinks,
} from '../hooks/useNotifications';
import { NotificationFilters, type FilterOption } from './NotificationFilters';
import { NotificationRow } from './NotificationRow';
import { DeliveryRulesPanel } from './DeliveryRulesPanel';
import { useNotificationFilter } from './useNotificationFilter';
import { describeError } from './errors';
import { PRIMARY_BUTTON, SECONDARY_BUTTON } from './RuleEditor';

function errorMessage(error: unknown): string {
  return describeError(error, 'The Forge did not answer.');
}

function distinct(options: FilterOption[]): FilterOption[] {
  const seen = new Map<string, FilterOption>();
  for (const option of options) if (!seen.has(option.id)) seen.set(option.id, option);
  return [...seen.values()].sort((a, b) => a.label.localeCompare(b.label));
}

const ROW_STEPS: Record<string, number> = { j: 1, ArrowDown: 1, k: -1, ArrowUp: -1 };

/** j/k and arrow keys move focus between rows; Enter activates the focused row. */
function moveRowFocus(event: KeyboardEvent<HTMLElement>) {
  const step = ROW_STEPS[event.key];
  if (!step) return;
  event.preventDefault();
  // The list only holds rows, and each row holds its own open button.
  const rows = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>('[data-notification-open]'),
  );
  const current = rows.findIndex((row) => row.closest('li')!.contains(event.target as Node));
  rows[Math.min(Math.max(current + step, 0), rows.length - 1)]!.focus();
}

export function NotificationsPage() {
  const [filter, setFilter] = useNotificationFilter();
  const feed = useNotifications(filter);
  const markRead = useMarkNotificationsRead();
  const sinks = useNotificationSinks(null);
  const volundr = useService<IVolundrService>('volundr');
  const navigate = useNavigate();
  const [rulesOpen, setRulesOpen] = useState(false);
  const rulesToggle = useRef<HTMLButtonElement>(null);

  const targets = useQuery({
    queryKey: ['volundr', 'targets'],
    queryFn: () => volundr.getTargets(),
  });
  const projects = useQuery({
    queryKey: ['volundr', 'projects', 'notifications'],
    queryFn: () => volundr.getProjects(),
  });

  const hostNames = useMemo(() => {
    const names = new Map<string, string>();
    for (const target of targets.data ?? []) names.set(target.id, target.name || target.slug);
    return names;
  }, [targets.data]);

  // Names the facade reported on rows, for hosts the registry does not list.
  const reportedNames = useMemo(() => {
    const names = new Map<string, string>();
    for (const notification of feed.loaded)
      if (notification.instanceId && notification.instanceName)
        names.set(notification.instanceId, notification.instanceName);
    return names;
  }, [feed.loaded]);

  const hostLabel = useCallback(
    (id: string, reported?: string | null) =>
      hostNames.get(id) ?? reported ?? reportedNames.get(id) ?? id,
    [hostNames, reportedNames],
  );

  const hosts = useMemo(
    () =>
      distinct([
        ...(targets.data ?? []).map((target) => ({
          id: target.id,
          label: target.name || target.slug,
        })),
        ...feed.loaded
          .filter((notification) => notification.instanceId)
          .map((notification) => ({
            id: notification.instanceId!,
            label: hostLabel(notification.instanceId!, notification.instanceName),
          })),
      ]),
    [targets.data, feed.loaded, hostLabel],
  );

  const projectOptions = useMemo(() => {
    const names = new Map((projects.data ?? []).map((project) => [project.id, project.name]));
    return distinct(
      feed.loaded
        .filter((notification) => notification.projectId)
        .map((notification) => ({
          id: notification.projectId!,
          label: names.get(notification.projectId!) ?? notification.projectId!,
        }))
        .concat(
          filter.projectId
            ? [{ id: filter.projectId, label: names.get(filter.projectId) ?? filter.projectId }]
            : [],
        ),
    );
  }, [projects.data, feed.loaded, filter.projectId]);

  const sessionOptions = useMemo(
    () =>
      distinct(
        feed.loaded
          .filter((notification) => notification.sessionId)
          .map((notification) => ({
            id: notification.sessionId!,
            label: notification.sessionName ?? notification.sessionId!,
          }))
          .concat(filter.sessionId ? [{ id: filter.sessionId, label: filter.sessionId }] : []),
      ),
    [feed.loaded, filter.sessionId],
  );

  const sinkLabels = useMemo(
    () => Object.fromEntries((sinks.data ?? []).map((sink) => [sink.name, sink.label])),
    [sinks.data],
  );

  const unread = feed.unreadCount ?? 0;
  const multiHost = hosts.length > 1;

  function openSession(notification: SessionNotification) {
    if (!notification.sessionId) return;
    void navigate({
      to: '/volundr/session/$sessionId',
      params: { sessionId: notification.sessionId },
      hash: notificationAnchorId(notification.id),
    } as never);
  }

  function markAllRead() {
    markRead.mutate(readEverythingThrough(feed.readState, feed.loaded));
  }

  function markReadThrough(notification: SessionNotification) {
    const index = feed.notifications.findIndex(
      (row) => notificationKey(row) === notificationKey(notification),
    );
    markRead.mutate(readThroughFrom(feed.notifications, index));
  }

  let body;
  if (feed.isLoading) body = <LoadingState label="Loading notifications…" />;
  else if (feed.isError)
    body = (
      <ErrorState
        title="Could not load notifications"
        message={errorMessage(feed.error)}
        action={
          <button type="button" className={SECONDARY_BUTTON} onClick={feed.refetch}>
            Retry
          </button>
        }
      />
    );
  else if (feed.notifications.length === 0)
    body = isFilterActive(filter) ? (
      <EmptyState
        icon={<BellOff aria-hidden="true" />}
        title="No notifications match these filters"
        description={
          feed.hasMore
            ? 'Older notifications may still match — load more or clear filters.'
            : undefined
        }
        action={
          <div className="niuu:flex niuu:gap-2">
            {feed.hasMore && (
              <button type="button" className={SECONDARY_BUTTON} onClick={feed.loadMore}>
                Load older
              </button>
            )}
            <button
              type="button"
              className={SECONDARY_BUTTON}
              onClick={() => setFilter(DEFAULT_NOTIFICATION_FILTER)}
            >
              Clear filters
            </button>
          </div>
        }
      />
    ) : (
      <EmptyState
        icon={<BellOff aria-hidden="true" />}
        title="No notifications yet"
        description="When a session reports a milestone, a decision or an error, needs your input, or finishes a reply, it shows up here."
      />
    );
  else
    body = (
      <>
        <ul
          aria-label="Notifications"
          className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-2 niuu:p-0"
          onKeyDown={moveRowFocus}
        >
          {feed.notifications.map((notification) => (
            <NotificationRow
              key={notificationKey(notification)}
              notification={notification}
              read={isNotificationRead(notification, feed.readState)}
              hostLabel={
                multiHost && notification.instanceId
                  ? hostLabel(notification.instanceId, notification.instanceName)
                  : null
              }
              sinkLabels={sinkLabels}
              onOpen={openSession}
              onMarkReadThrough={markReadThrough}
              markingRead={markRead.isPending}
            />
          ))}
        </ul>
        {feed.hasMore && (
          <button
            type="button"
            className={cn(SECONDARY_BUTTON, 'niuu:self-center')}
            onClick={feed.loadMore}
            disabled={feed.isLoadingMore}
          >
            {feed.isLoadingMore ? 'Loading…' : 'Load older'}
          </button>
        )}
      </>
    );

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-4 niuu:p-6" data-testid="notifications-page">
      <header className="niuu:flex niuu:flex-wrap niuu:items-start niuu:justify-between niuu:gap-3">
        <div className="niuu:flex niuu:flex-col niuu:gap-1">
          <h2 className="niuu:m-0 niuu:text-xl niuu:text-text-primary">Notifications</h2>
          <p className="niuu:m-0 niuu:max-w-2xl niuu:text-sm niuu:text-text-secondary">
            Milestones, decisions, errors and finished replies from every session on every host.
          </p>
        </div>
        <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2">
          {feed.live && <LiveBadge label="Live" />}
          <span
            className="niuu:text-sm niuu:text-text-secondary"
            aria-live="polite"
            data-testid="notifications-unread"
          >
            {unread === 0 ? 'All read' : `${unread} unread`}
          </span>
          <button
            type="button"
            className={PRIMARY_BUTTON}
            onClick={markAllRead}
            disabled={unread === 0 || markRead.isPending}
          >
            Mark all read
          </button>
          <button
            ref={rulesToggle}
            type="button"
            className={SECONDARY_BUTTON}
            aria-expanded={rulesOpen}
            aria-controls="notification-rules"
            onClick={() => setRulesOpen((open) => !open)}
          >
            Delivery rules
          </button>
        </div>
      </header>
      <NotificationFilters
        filter={filter}
        onChange={setFilter}
        hosts={hosts}
        projects={projectOptions}
        sessions={sessionOptions}
      />
      {feed.unavailableInstances.length > 0 && (
        <p
          role="status"
          data-testid="notifications-unavailable"
          className="niuu:m-0 niuu:text-xs niuu:text-text-muted"
        >
          Some hosts are unavailable (
          {feed.unavailableInstances.map((id) => hostLabel(id)).join(', ')}) — their notifications
          may be missing.
        </p>
      )}
      {markRead.isError && (
        <p role="alert" className="niuu:m-0 niuu:text-sm niuu:text-critical">
          Could not mark notifications read: {errorMessage(markRead.error)}
        </p>
      )}
      <div className="niuu:flex niuu:flex-col niuu:gap-4 niuu:lg:flex-row">
        <section
          aria-label="Notification feed"
          className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col niuu:gap-3"
        >
          {body}
        </section>
        {rulesOpen && (
          <DeliveryRulesPanel
            hosts={hosts}
            onClose={() => {
              setRulesOpen(false);
              rulesToggle.current?.focus();
            }}
          />
        )}
      </div>
    </div>
  );
}
