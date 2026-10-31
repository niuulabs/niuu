import { useEffect, useRef, useState } from 'react';
import { ExternalLink, FileText } from 'lucide-react';
import type { ForgeNotificationLink, ForgeNotificationPayload } from '@niuulabs/domain';
import { cn } from '../../../utils/cn';
import { MarkdownContent } from '../MarkdownContent';
import { safeExternalUrl, useConversationResources } from '../ConversationResources';
import {
  NOTIFICATION_KIND_LABELS,
  NOTIFICATION_SEVERITY_LABELS,
  NotificationKindIcon,
  notificationTone,
} from './notificationPresentation';

/** DOM id of an inline card, so `#notification-<id>` deep links land on the turn. */
export function notificationAnchorId(notificationId: string): string {
  return `notification-${notificationId}`;
}

const LINK_CLASS =
  'niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-secondary niuu:no-underline niuu:hover:text-text-primary';

function isAppPath(url: string): boolean {
  return url.startsWith('/') && !url.startsWith('//');
}

function NotificationLinkItem({ link }: { link: ForgeNotificationLink }) {
  const port = useConversationResources();
  if (link.fileId) {
    return (
      <button
        type="button"
        className={cn(LINK_CLASS, 'niuu:cursor-pointer niuu:disabled:cursor-not-allowed')}
        disabled={!port}
        title={port ? `Open ${link.label}` : 'File access is unavailable in this view.'}
        onClick={() => port?.open({ kind: 'presented', path: link.fileId!, name: link.label })}
      >
        <FileText className="niuu:h-3 niuu:w-3" aria-hidden="true" />
        {link.label}
      </button>
    );
  }
  const url = link.url ?? '';
  if (isAppPath(url)) {
    return (
      <a className={LINK_CLASS} href={url}>
        {link.label}
      </a>
    );
  }
  const external = safeExternalUrl(url);
  if (!external) {
    return (
      <span className={LINK_CLASS} title="Unsupported link">
        {link.label}
      </span>
    );
  }
  return (
    <a className={LINK_CLASS} href={external} target="_blank" rel="noopener noreferrer">
      {link.label}
      <ExternalLink className="niuu:h-3 niuu:w-3" aria-hidden="true" />
    </a>
  );
}

export interface NotificationCardProps {
  notification: ForgeNotificationPayload;
  className?: string;
}

/**
 * Inline card for a `forge_notification` turn: the agent (or Forge) telling the
 * user something happened. Compact, tone-coded by severity, body in markdown.
 */
export function NotificationCard({ notification, className }: NotificationCardProps) {
  const { notificationId, kind, severity, title, body, links } = notification;
  const tone = notificationTone(severity);
  const anchorId = notificationId ? notificationAnchorId(notificationId) : undefined;
  const ref = useRef<HTMLElement>(null);
  // Read once at mount: a deep link targets the card the page opened on.
  const [targeted] = useState(
    () =>
      Boolean(anchorId) && typeof window !== 'undefined' && window.location.hash === `#${anchorId}`,
  );

  useEffect(() => {
    if (targeted) ref.current?.scrollIntoView?.({ block: 'center' });
  }, [targeted]);

  return (
    <article
      ref={ref}
      id={anchorId}
      data-testid="notification-card"
      data-kind={kind}
      data-severity={severity}
      data-targeted={targeted || undefined}
      aria-label={`${NOTIFICATION_KIND_LABELS[kind]}: ${title}`}
      className={cn(
        'niuu:my-3 niuu:flex niuu:items-stretch niuu:gap-3 niuu:overflow-hidden niuu:rounded-lg niuu:border niuu:border-solid niuu:border-border-subtle niuu:bg-bg-secondary niuu:py-3 niuu:pr-3',
        targeted && 'niuu:ring-2 niuu:ring-brand',
        className,
      )}
    >
      <span
        aria-hidden="true"
        data-testid="notification-accent"
        className={cn('niuu:-my-3 niuu:w-1 niuu:shrink-0', tone.bar)}
      />
      <NotificationKindIcon
        kind={kind}
        className={cn('niuu:mt-0.5 niuu:h-4 niuu:w-4 niuu:shrink-0', tone.text)}
      />
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col niuu:gap-1">
        <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2 niuu:text-xs">
          <span className={cn('niuu:font-medium niuu:uppercase niuu:tracking-wide', tone.text)}>
            {NOTIFICATION_KIND_LABELS[kind]}
          </span>
          {severity !== 'info' && (
            <span className="niuu:text-text-muted">{NOTIFICATION_SEVERITY_LABELS[severity]}</span>
          )}
        </div>
        <p className="niuu:m-0 niuu:text-sm niuu:font-semibold niuu:text-text-primary niuu:break-words">
          {title}
        </p>
        {body && (
          <div className="niuu:text-sm niuu:text-text-secondary">
            <MarkdownContent content={body} />
          </div>
        )}
        {links.length > 0 && (
          <ul
            className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-wrap niuu:gap-2 niuu:p-0"
            aria-label="Notification links"
          >
            {links.map((link, index) => (
              <li key={`${link.label}:${index}`}>
                <NotificationLinkItem link={link} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </article>
  );
}
