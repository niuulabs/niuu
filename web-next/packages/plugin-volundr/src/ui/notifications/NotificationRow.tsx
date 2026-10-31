import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import {
  Chip,
  MarkdownContent,
  NOTIFICATION_KIND_LABELS,
  NOTIFICATION_SEVERITY_LABELS,
  NotificationKindIcon,
  cn,
  notificationTone,
  relTime,
} from '@niuulabs/ui';
import {
  notificationExcerpt,
  safeLinkHref,
  type NotificationDelivery,
  type NotificationDeliveryStatus,
  type SessionNotification,
} from '../../domain/notifications';
import { useNotificationDeliveries } from '../hooks/useNotifications';
import { CliBadge } from '../atoms/CliBadge';
import { ModelChip } from '../atoms/ModelChip';
import { SOURCE_LABELS } from './NotificationFilters';

/** Characters of body shown in the collapsed row. */
export const NOTIFICATION_EXCERPT_CHARS = 180;

const DELIVERY_TONES: Record<NotificationDeliveryStatus, string> = {
  pending: 'niuu:bg-state-warn-bg niuu:text-state-warn',
  claimed: 'niuu:bg-state-warn-bg niuu:text-state-warn',
  delivered: 'niuu:bg-state-ok-bg niuu:text-state-ok',
  failed: 'niuu:bg-critical-bg niuu:text-critical',
  dead: 'niuu:bg-critical-bg niuu:text-critical',
  suppressed: 'niuu:bg-bg-tertiary niuu:text-text-muted',
};

const DELIVERY_LABELS: Record<NotificationDeliveryStatus, string> = {
  pending: 'Pending',
  claimed: 'Sending',
  delivered: 'Delivered',
  failed: 'Retrying',
  dead: 'Failed',
  suppressed: 'Held (quiet hours or rate limit)',
};

const ACTION_CLASS =
  'niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-transparent niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-secondary niuu:hover:text-text-primary niuu:cursor-pointer';

function DeliveryRow({
  delivery,
  sinkLabel,
}: {
  delivery: NotificationDelivery;
  sinkLabel: string;
}) {
  const at = delivery.deliveredAt ?? delivery.updatedAt;
  return (
    <li
      className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2 niuu:text-xs"
      data-testid="notification-delivery"
    >
      <span className="niuu:font-medium niuu:text-text-primary">{sinkLabel}</span>
      <span
        className={cn('niuu:rounded-full niuu:px-2 niuu:py-0.5', DELIVERY_TONES[delivery.status])}
      >
        {DELIVERY_LABELS[delivery.status]}
      </span>
      {delivery.attempts > 1 && (
        <span className="niuu:text-text-muted">{delivery.attempts} attempts</span>
      )}
      {at && (
        <time className="niuu:text-text-muted" dateTime={at} title={new Date(at).toLocaleString()}>
          {relTime(at)}
        </time>
      )}
      {delivery.lastError && (
        <span className="niuu:basis-full niuu:text-critical">{delivery.lastError}</span>
      )}
    </li>
  );
}

function DeliveryStatus({
  notification,
  sinkLabels,
}: {
  notification: SessionNotification;
  sinkLabels: Record<string, string>;
}) {
  const deliveries = useNotificationDeliveries(notification, true);
  let content;
  if (deliveries.isLoading) content = <p className="niuu:m-0">Checking delivery…</p>;
  else if (deliveries.isError)
    content = (
      <p className="niuu:m-0 niuu:text-critical" role="alert">
        Could not load delivery status.
      </p>
    );
  else if ((deliveries.data ?? []).length === 0)
    content = <p className="niuu:m-0">Not sent anywhere else — no delivery rule matched.</p>;
  else
    content = (
      <ul className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-1 niuu:p-0">
        {deliveries.data!.map((delivery) => (
          <DeliveryRow
            key={delivery.id}
            delivery={delivery}
            sinkLabel={sinkLabels[delivery.sink] ?? delivery.sink}
          />
        ))}
      </ul>
    );
  return (
    <section
      aria-label="Delivery"
      className="niuu:flex niuu:flex-col niuu:gap-1 niuu:text-xs niuu:text-text-muted"
    >
      <h4 className="niuu:m-0 niuu:text-xs niuu:font-medium niuu:uppercase niuu:tracking-wide niuu:text-text-muted">
        Delivery
      </h4>
      {content}
    </section>
  );
}

export interface NotificationRowProps {
  notification: SessionNotification;
  read: boolean;
  hostLabel: string | null;
  sinkLabels: Record<string, string>;
  onOpen: (notification: SessionNotification) => void;
  onMarkReadThrough: (notification: SessionNotification) => void;
  markingRead: boolean;
}

export function NotificationRow({
  notification,
  read,
  hostLabel,
  sinkLabels,
  onOpen,
  onMarkReadThrough,
  markingRead,
}: NotificationRowProps) {
  const [expanded, setExpanded] = useState(false);
  const tone = notificationTone(notification.severity);
  const detailsId = `notification-details-${notification.instanceId ?? 'local'}-${notification.id}`;
  // Older reply summaries repeat the title as their body; never show the same text twice.
  const body = notification.body.trim() === notification.title.trim() ? '' : notification.body;
  const excerpt = notificationExcerpt(body, NOTIFICATION_EXCERPT_CHARS);
  const canOpen = Boolean(notification.sessionId);

  return (
    <li
      data-testid="notification-row"
      data-unread={!read || undefined}
      data-kind={notification.kind}
      data-severity={notification.severity}
      className="niuu:flex niuu:overflow-hidden niuu:rounded-md niuu:border niuu:border-solid niuu:border-border-subtle niuu:bg-bg-secondary"
    >
      <span
        aria-hidden="true"
        data-testid="notification-accent"
        className={cn('niuu:w-1 niuu:shrink-0', tone.bar)}
      />
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
        <div className="niuu:flex niuu:items-start niuu:gap-3 niuu:p-3">
          <span className="niuu:mt-1.5 niuu:flex niuu:h-2 niuu:w-2 niuu:shrink-0">
            {!read && (
              <span
                className="niuu:h-2 niuu:w-2 niuu:rounded-full niuu:bg-brand"
                data-testid="notification-unread-dot"
                title="Unread"
              />
            )}
          </span>
          <NotificationKindIcon
            kind={notification.kind}
            className={cn('niuu:mt-0.5 niuu:h-4 niuu:w-4 niuu:shrink-0', tone.text)}
          />
          <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col niuu:gap-1">
            <button
              type="button"
              data-notification-open
              className={cn(
                'niuu:m-0 niuu:cursor-pointer niuu:border-0 niuu:bg-transparent niuu:p-0 niuu:text-left niuu:text-sm niuu:text-text-primary niuu:hover:underline',
                read ? 'niuu:font-normal' : 'niuu:font-semibold',
              )}
              onClick={() => (canOpen ? onOpen(notification) : setExpanded((open) => !open))}
              aria-label={`${read ? '' : 'Unread. '}${NOTIFICATION_KIND_LABELS[notification.kind]}: ${notification.title}${canOpen ? ` — open ${notification.sessionName ?? 'session'}` : ''}`}
            >
              {notification.title}
            </button>
            {!expanded && excerpt && (
              <p className="niuu:m-0 niuu:line-clamp-2 niuu:text-sm niuu:text-text-secondary">
                {excerpt}
              </p>
            )}
            <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2 niuu:text-xs niuu:text-text-muted">
              <span className={tone.text}>{NOTIFICATION_KIND_LABELS[notification.kind]}</span>
              {notification.severity !== 'info' && (
                <span>{NOTIFICATION_SEVERITY_LABELS[notification.severity]}</span>
              )}
              <span>{SOURCE_LABELS[notification.source]}</span>
              {notification.sessionName && (
                <span className="niuu:font-mono niuu:text-text-secondary">
                  {notification.sessionName}
                </span>
              )}
              {hostLabel && <Chip tone="muted">{hostLabel}</Chip>}
              {notification.engine && <CliBadge cli={notification.engine} compact />}
              {notification.model && <ModelChip model={notification.model} />}
              <time
                dateTime={notification.createdAt}
                title={new Date(notification.createdAt).toLocaleString()}
              >
                {relTime(notification.createdAt)}
              </time>
            </div>
          </div>
          <div className="niuu:flex niuu:shrink-0 niuu:items-center niuu:gap-1">
            {!read && (
              <button
                type="button"
                className={ACTION_CLASS}
                disabled={markingRead}
                onClick={() => onMarkReadThrough(notification)}
                title="Mark this and everything older as read"
              >
                Read through here
              </button>
            )}
            <button
              type="button"
              className={ACTION_CLASS}
              aria-expanded={expanded}
              aria-controls={detailsId}
              aria-label={expanded ? 'Hide details' : 'Show details'}
              onClick={() => setExpanded((open) => !open)}
            >
              {expanded ? (
                <ChevronDown className="niuu:h-3 niuu:w-3" aria-hidden="true" />
              ) : (
                <ChevronRight className="niuu:h-3 niuu:w-3" aria-hidden="true" />
              )}
            </button>
          </div>
        </div>
        {expanded && (
          <div
            id={detailsId}
            className="niuu:flex niuu:flex-col niuu:gap-3 niuu:border-0 niuu:border-t niuu:border-solid niuu:border-border-subtle niuu:px-3 niuu:py-3 niuu:pl-12"
          >
            {body ? (
              <div className="niuu:text-sm niuu:text-text-secondary">
                <MarkdownContent content={body} />
              </div>
            ) : (
              <p className="niuu:m-0 niuu:text-sm niuu:text-text-muted">No details.</p>
            )}
            {notification.links.length > 0 && (
              <ul
                aria-label="Links"
                className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-wrap niuu:gap-2 niuu:p-0 niuu:text-xs"
              >
                {notification.links.map((link, index) => {
                  const href = safeLinkHref(link.url);
                  return (
                    <li key={`${link.label}:${index}`}>
                      {href ? (
                        <a
                          href={href}
                          target={href.startsWith('/') ? undefined : '_blank'}
                          rel="noopener noreferrer"
                          className="niuu:text-brand"
                        >
                          {link.label}
                        </a>
                      ) : (
                        <span title="Open it from the session">{link.label}</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
            <DeliveryStatus notification={notification} sinkLabels={sinkLabels} />
          </div>
        )}
      </div>
    </li>
  );
}
