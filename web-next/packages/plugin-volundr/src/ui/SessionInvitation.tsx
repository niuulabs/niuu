import { MailPlus } from 'lucide-react';
import type { SessionRoom } from '../models/volundr.model';
import { errorText } from './errorText';
import {
  useAcceptInvitation,
  useRemoveParticipant,
  useViewerIdentity,
} from './hooks/useSessionPeople';

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-text-primary niuu:disabled:opacity-40';
const PRIMARY =
  'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-brand-300 niuu:disabled:opacity-40';

/** What an invitation needs to be shown: the chat, who invited the person, and the offered role. */
export interface InvitationSession {
  id: string;
  name?: string;
  personaName?: string;
  ownerId?: string;
  ownerName?: string;
  room?: SessionRoom;
}

/** "Invited by Alice · viewer": who shared it and what they may do in it. */
export function invitationLabel(session: InvitationSession): string {
  const by = session.ownerName ?? session.ownerId;
  return `Invited${by ? ` by ${by}` : ''} · ${session.room?.role ?? 'viewer'}`;
}

/**
 * Join or decline an invitation. Joining turns the invitation into a seat in the chat and
 * opens it; declining removes the invitation (it is the person's own grant they remove).
 */
export function InvitationActions({
  session,
  onJoined,
  onDeclined,
  stopPropagation = false,
}: {
  session: InvitationSession;
  onJoined?: () => void;
  onDeclined?: () => void;
  /** In a list row, a click here must not also select the row. */
  stopPropagation?: boolean;
}) {
  const identity = useViewerIdentity();
  const accept = useAcceptInvitation(session.id, undefined);
  const decline = useRemoveParticipant(session.id, undefined);
  const busy = accept.isPending || decline.isPending;
  const error = accept.error ?? decline.error;
  const guard = (event: React.SyntheticEvent) => {
    if (stopPropagation) event.stopPropagation();
  };

  return (
    <div
      className="niuu:flex niuu:flex-col niuu:gap-1"
      data-testid={`invitation-actions-${session.id}`}
      onClick={guard}
    >
      <div className="niuu:flex niuu:gap-2">
        <button
          type="button"
          className={PRIMARY}
          disabled={busy}
          onClick={(event) => {
            guard(event);
            accept.mutate(undefined, { onSuccess: () => onJoined?.() });
          }}
        >
          {accept.isPending ? 'Joining…' : 'Join'}
        </button>
        <button
          type="button"
          className={BUTTON}
          disabled={busy || !identity.data?.userId}
          onClick={(event) => {
            guard(event);
            if (!identity.data?.userId) return;
            decline.mutate(identity.data.userId, { onSuccess: () => onDeclined?.() });
          }}
        >
          {decline.isPending ? 'Declining…' : 'Decline'}
        </button>
      </div>
      {error ? (
        <span role="alert" className="niuu:text-xs niuu:text-critical-fg">
          {errorText(error)}
        </span>
      ) : null}
    </div>
  );
}

/** The detail pane of an invitation: the chat is not readable until the person joins. */
export function InvitationPane({
  session,
  onJoined,
  onDeclined,
}: {
  session: InvitationSession;
  onJoined: () => void;
  onDeclined: () => void;
}) {
  return (
    <div
      className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:p-6"
      data-testid="invitation-pane"
    >
      <div className="niuu:flex niuu:max-w-md niuu:flex-col niuu:gap-3 niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-5">
        <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-text-primary">
          <MailPlus className="niuu:h-4 niuu:w-4 niuu:text-brand" aria-hidden="true" />
          <strong className="niuu:text-sm">{session.name || session.personaName}</strong>
        </div>
        <p className="niuu:m-0 niuu:text-sm niuu:text-text-secondary">
          {invitationLabel(session)}. Join to read this chat and write in it. What you ask the agent
          to do runs with the owner&apos;s credentials.
        </p>
        <InvitationActions session={session} onJoined={onJoined} onDeclined={onDeclined} />
      </div>
    </div>
  );
}
