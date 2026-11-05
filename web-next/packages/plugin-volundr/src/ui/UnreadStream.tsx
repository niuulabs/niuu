import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { EmptyState, ErrorState, LoadingState, MarkdownContent } from '@niuulabs/ui';
import { Check, ExternalLink } from 'lucide-react';
import type { IVolundrService, VolundrConversationTurn } from '../ports/IVolundrService';
import type { Session } from '../domain/session';
import { SESSION_STATE_LABELS } from './sessions/presentation';
import { compactDuration, useMinuteClock } from './sessions/timeFact';
import { useSessionRead } from './hooks/useSessionRead';
import { errorText } from './errorText';
import './UnreadStream.css';

export interface UnreadStreamProps {
  /** The sessions with a reply the person has not read, newest reply first. */
  sessions: readonly Session[];
  onOpen: (sessionId: string) => void;
}

/** The newest public assistant reply, for a session whose read state names no reply. */
export function latestReply(turns: readonly VolundrConversationTurn[]): VolundrConversationTurn {
  const newest = turns.findLast(
    (turn) => turn.role === 'assistant' && turn.visibility !== 'internal',
  );
  if (!newest) throw new Error('This session has no assistant reply in its history.');
  return newest;
}

/** True once the element has been on screen; it stays true so a loaded reply is not dropped. */
function useSeen(ref: React.RefObject<HTMLElement | null>): boolean {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (!element || seen) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) setSeen(true);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, seen]);
  return seen;
}

function UnreadBlock({
  session,
  onOpen,
  onMarkRead,
  pending,
}: {
  session: Session;
  onOpen: (sessionId: string) => void;
  onMarkRead: (session: Session) => void;
  pending: boolean;
}) {
  const volundr = useService<IVolundrService>('volundr');
  const ref = useRef<HTMLElement>(null);
  const seen = useSeen(ref);
  const now = useMinuteClock();
  const name = session.name || session.personaName || session.id;
  const turnId = session.readState?.latestOutputTurnId;
  const reply = useQuery({
    queryKey: ['volundr', 'unread-reply', session.id, turnId ?? null],
    // The read state names the reply; fetch exactly that turn, which may sit far outside
    // the recent history page. Without a name, the newest reply in the history is it.
    queryFn: async () =>
      turnId
        ? volundr.getConversationTurn(session.id, turnId)
        : latestReply((await volundr.getConversationHistory(session.id)).turns),
    enabled: seen,
  });
  const at = Date.parse(session.readState?.latestOutputAt ?? '');
  return (
    <section
      ref={ref}
      className="unread-stream__block"
      aria-label={`Unread: ${name}`}
      data-testid={`unread-block-${session.id}`}
    >
      <header className="unread-stream__block-head">
        <h3 className="unread-stream__name">{name}</h3>
        <span className="forge-session-state" data-state={session.state}>
          {SESSION_STATE_LABELS[session.state]}
        </span>
        {session.readState?.manuallyUnread && (
          <span className="unread-stream__marked">Marked unread</span>
        )}
        {Number.isFinite(at) && (
          <time className="unread-stream__ago" dateTime={session.readState?.latestOutputAt ?? ''}>
            {compactDuration(now - at)} ago
          </time>
        )}
      </header>
      <div className="unread-stream__reply">
        {!seen && <div className="unread-stream__placeholder" aria-hidden="true" />}
        {seen && reply.isPending && <LoadingState label="Loading latest reply…" />}
        {seen && reply.isError && (
          <ErrorState
            title="Could not load the latest reply"
            message={errorText(reply.error)}
            action={
              <button type="button" onClick={() => void reply.refetch()}>
                Retry
              </button>
            }
          />
        )}
        {reply.data && (
          <div className="unread-stream__excerpt" data-testid={`unread-reply-${session.id}`}>
            <MarkdownContent content={reply.data.content} />
          </div>
        )}
      </div>
      <div className="unread-stream__actions">
        <button type="button" onClick={() => onOpen(session.id)}>
          <ExternalLink size={14} aria-hidden="true" />
          Open session
        </button>
        <button
          type="button"
          className="unread-stream__mark"
          disabled={pending}
          aria-label={`Mark ${name} read`}
          onClick={() => onMarkRead(session)}
        >
          <Check size={14} aria-hidden="true" />
          Mark read
        </button>
      </div>
    </section>
  );
}

/**
 * Every session with a reply the person has not seen, as one stream: who answered, how long
 * ago, and the reply itself. Replies load only for the blocks that reach the screen.
 */
export function UnreadStream({ sessions, onOpen }: UnreadStreamProps) {
  const read = useSessionRead();
  return (
    <div className="unread-stream" data-testid="unread-stream">
      <div className="unread-stream__top">
        <span data-testid="unread-stream-summary">
          {sessions.length === 0
            ? 'No unread replies'
            : `${sessions.length} ${sessions.length === 1 ? 'session has' : 'sessions have'} new replies`}
        </span>
        <button
          type="button"
          className="unread-stream__mark"
          disabled={sessions.length === 0 || read.pendingIds.size > 0}
          onClick={() => void read.markAllRead(sessions)}
        >
          Mark all read
        </button>
      </div>
      {read.error && (
        <p role="alert" className="unread-stream__error" data-testid="unread-stream-error">
          {read.error}
        </p>
      )}
      {sessions.length === 0 ? (
        <EmptyState title="All caught up" description="No session has a reply you haven't read." />
      ) : (
        sessions.map((session) => (
          <UnreadBlock
            key={session.id}
            session={session}
            onOpen={onOpen}
            onMarkRead={(target) => void read.markRead(target)}
            pending={read.pendingIds.has(session.id)}
          />
        ))
      )}
    </div>
  );
}
