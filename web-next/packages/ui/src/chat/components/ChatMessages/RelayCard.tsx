import { useState } from 'react';
import { cn } from '../../../utils/cn';
import { MarkdownContent } from '../MarkdownContent';
import { sessionOpenHandler, type SessionLink, type SessionLinksValue } from '../../sessionLinks';
import { isLongRelay, type RelayedTurn } from './relayedTurn';
import './RelayCard.css';

export interface RelayCardProps {
  relay: RelayedTurn;
  /** The sender as the session list knows it; the header opens it. */
  link: SessionLink | null;
  links: SessionLinksValue | null;
  /** The turn's time as the transcript shows it; null when timestamps are off. */
  time: string | null;
}

/** Another session's message to this one: who it came from, how, and what it said. */
export function RelayCard({ relay, link, links, time }: RelayCardProps) {
  const long = isLongRelay(relay.body);
  const [expanded, setExpanded] = useState(false);
  const collapsed = long && !expanded;
  const sender = relay.senderName ?? link?.name ?? 'Another session';

  const identity = (
    <>
      <span className="niuu-chat-relay-glyph" aria-hidden="true">
        ↪
      </span>
      <b className="niuu-chat-relay-sender">{sender}</b>
      {relay.host && <span className="niuu-chat-relay-host">· {relay.host}</span>}
      <span className="niuu-chat-relay-tag">{relay.tag}</span>
    </>
  );

  return (
    <div
      className="niuu-chat-relay"
      data-testid="relay-message"
      data-relay-kind={relay.kind}
      data-collapsed={collapsed || undefined}
    >
      <div className="niuu-chat-relay-head">
        {link && links ? (
          <a
            className="niuu-chat-relay-open"
            href={link.href}
            onClick={sessionOpenHandler(links, link)}
            aria-label={`Open session ${link.name}`}
          >
            {identity}
          </a>
        ) : (
          <span className="niuu-chat-relay-open">{identity}</span>
        )}
        {time && <span className="niuu-chat-relay-time">{time}</span>}
      </div>
      {relay.body && (
        <div className={cn('niuu-chat-relay-body', collapsed && 'niuu-chat-relay-body--collapsed')}>
          <MarkdownContent content={relay.body} />
        </div>
      )}
      {long && (
        <button
          type="button"
          className="niuu-chat-relay-more"
          aria-expanded={expanded}
          onClick={() => setExpanded((open) => !open)}
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  );
}
