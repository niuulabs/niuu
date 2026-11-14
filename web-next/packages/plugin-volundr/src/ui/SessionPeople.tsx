import { useState } from 'react';
import { useService } from '@niuulabs/plugin-sdk';
import { Users } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger, authorInitials } from '@niuulabs/ui';
import {
  PARTICIPANT_ROLES,
  isSharedSession,
  type ParticipantRole,
  type SessionParticipant,
  type VolundrSession,
} from '../models/volundr.model';
import type { IVolundrService } from '../ports/IVolundrService';
import { errorText } from './errorText';
import { CONTROL, CandidatePicker, nameOf } from './CandidatePicker';
import {
  sessionPeopleKey,
  useInviteParticipant,
  useRemoveParticipant,
  useSessionParticipants,
  useSessionProjectPeople,
  useViewerIdentity,
} from './hooks/useSessionPeople';

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-text-primary niuu:disabled:opacity-40';
const ERROR = 'niuu:text-xs niuu:text-critical-fg';

const ROLE_LABEL: Record<ParticipantRole, string> = { viewer: 'Viewer', approver: 'Approver' };

const ROLE_HELP =
  'Viewers chat in this session and steer it. Approvers also decide its gates and answer for it. People you invite act through your credentials.';

function Avatar({ name, userId }: { name: string; userId: string }) {
  return (
    <span
      aria-hidden="true"
      className="niuu:flex niuu:h-6 niuu:w-6 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:bg-brand/15 niuu:text-[10px] niuu:font-semibold niuu:text-brand-300"
    >
      {authorInitials({ userId, displayName: name })}
    </span>
  );
}

function Person({
  userId,
  name,
  you,
  note,
  children,
}: {
  userId: string;
  name: string;
  you?: boolean;
  note?: string;
  children?: React.ReactNode;
}) {
  return (
    <li
      className="niuu:flex niuu:items-center niuu:gap-2 niuu:border-t niuu:border-border-subtle niuu:py-1.5"
      data-testid={`session-person-${userId}`}
    >
      <Avatar name={name} userId={userId} />
      <span className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
        <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">
          {name}
          {you ? <span className="niuu:ml-1 niuu:text-text-muted">(you)</span> : null}
        </span>
        {note ? <span className="niuu:text-xs niuu:text-text-muted">{note}</span> : null}
      </span>
      {children}
    </li>
  );
}

/** Find someone in the org and invite them into this chat. */
function InviteSomeone({ session }: { session: VolundrSession }) {
  const volundr = useService<IVolundrService>('volundr');
  const [query, setQuery] = useState('');
  const [chosen, setChosen] = useState<string | null>(null);
  const [role, setRole] = useState<ParticipantRole>('viewer');
  const invite = useInviteParticipant(session.id, session.instanceId);

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-2" data-testid="session-people-invite">
      <span className="niuu:text-xs niuu:font-medium niuu:text-text-secondary">
        Invite someone from your org
      </span>
      <CandidatePicker
        queryKey={[...sessionPeopleKey(session.id), 'candidates']}
        fetcher={(typed, signal) =>
          volundr.getParticipantCandidates(session.id, typed, {
            instanceId: session.instanceId,
            signal,
          })
        }
        query={query}
        onQueryChange={(value) => {
          setQuery(value);
          setChosen(null);
          invite.reset();
        }}
        chosen={chosen}
        onChoose={(person) => {
          setChosen(person.userId);
          setQuery(nameOf(person));
        }}
      >
        <select
          aria-label="Role for the invitee"
          className={CONTROL}
          value={role}
          onChange={(event) => setRole(event.target.value as ParticipantRole)}
        >
          {PARTICIPANT_ROLES.map((value) => (
            <option key={value} value={value}>
              {ROLE_LABEL[value]}
            </option>
          ))}
        </select>
        <button
          type="button"
          className={BUTTON}
          disabled={!chosen || invite.isPending}
          onClick={() =>
            chosen &&
            invite.mutate(
              { userId: chosen, role },
              {
                onSuccess: () => {
                  setQuery('');
                  setChosen(null);
                },
              },
            )
          }
        >
          {invite.isPending ? 'Inviting…' : 'Invite'}
        </button>
      </CandidatePicker>
      {invite.error ? <span className={ERROR}>{errorText(invite.error)}</span> : null}
      <p className="niuu:m-0 niuu:text-xs niuu:text-text-muted">{ROLE_HELP}</p>
    </div>
  );
}

function ParticipantRow({
  session,
  participant,
  manage,
  isViewer,
}: {
  session: VolundrSession;
  participant: SessionParticipant;
  manage: boolean;
  isViewer: boolean;
}) {
  const remove = useRemoveParticipant(session.id, session.instanceId);
  const name = nameOf(participant);
  return (
    <>
      <Person userId={participant.userId} name={name} you={isViewer}>
        <span className="niuu:text-xs niuu:text-text-muted">
          {ROLE_LABEL[participant.role]} ·{' '}
          {participant.status === 'invited' ? 'Invited' : 'In the chat'}
        </span>
        {manage ? (
          <button
            type="button"
            className={BUTTON}
            aria-label={`Remove ${name}`}
            disabled={remove.isPending}
            onClick={() => remove.mutate(participant.userId)}
          >
            {participant.status === 'invited' ? 'Withdraw' : 'Remove'}
          </button>
        ) : null}
      </Person>
      {remove.error ? (
        <li className={ERROR} role="alert">
          {errorText(remove.error)}
        </li>
      ) : null}
    </>
  );
}

/**
 * Who is in this chat, and for its owner and admins, the way to invite someone from the
 * org into it. The chat is the one session: the project's own members are listed as they
 * are (their room role comes from the project), and a person invited here is in this chat
 * and no other.
 */
export function SessionPeoplePanel({
  session,
  onLeft,
}: {
  session: VolundrSession;
  onLeft?: () => void;
}) {
  const identity = useViewerIdentity();
  const viewerId = identity.data?.userId;
  const isAdmin = identity.data?.roles.includes('volundr:admin') ?? false;
  const shared = isSharedSession(session);
  const manage = !shared && (isAdmin || (viewerId !== undefined && session.ownerId === viewerId));
  const participants = useSessionParticipants(session.id, session.instanceId, manage || shared);
  const projectPeople = useSessionProjectPeople(
    session.id,
    session.instanceId,
    session.coordination !== undefined,
  );
  const leave = useRemoveParticipant(session.id, session.instanceId);

  // Who may do what depends on who is looking.
  if (identity.isPending)
    return <p className="niuu:m-0 niuu:text-xs niuu:text-text-muted">Loading…</p>;
  if (identity.error)
    return (
      <span className={ERROR} role="alert">
        {errorText(identity.error)}
      </span>
    );

  const listed = participants.data ?? [];
  const ownerName = shared
    ? (session.ownerName ?? session.ownerId ?? 'The owner')
    : session.ownerId === viewerId
      ? (identity.data?.displayName ?? 'You')
      : (session.ownerName ?? session.ownerId ?? 'The owner');
  const ownerId = session.ownerId ?? 'owner';
  const inChat = listed.some(
    (participant) => participant.userId === viewerId && participant.status === 'active',
  );

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-3" data-testid="session-people">
      <ul className="niuu:m-0 niuu:list-none niuu:p-0">
        <Person userId={ownerId} name={ownerName} you={viewerId === ownerId}>
          <span className="niuu:text-xs niuu:text-text-muted">Owner</span>
        </Person>
        {(projectPeople.data ?? [])
          .filter((person) => person.userId !== session.ownerId)
          .map((person) => (
            <Person
              key={`project:${person.userId}`}
              userId={person.userId}
              name={nameOf(person)}
              you={person.userId === viewerId}
            >
              <span className="niuu:text-xs niuu:text-text-muted">
                {ROLE_LABEL[person.roomRole]} · via project
              </span>
            </Person>
          ))}
        {listed.map((participant) => (
          <ParticipantRow
            key={participant.userId}
            session={session}
            participant={participant}
            manage={manage}
            isViewer={participant.userId === viewerId}
          />
        ))}
      </ul>
      {participants.error ? (
        <span className={ERROR} role="alert">
          {errorText(participants.error)}
        </span>
      ) : null}
      {projectPeople.error ? (
        <span className={ERROR} role="alert">
          {errorText(projectPeople.error)}
        </span>
      ) : null}
      {manage ? <InviteSomeone session={session} /> : null}
      {shared && inChat && viewerId ? (
        <div className="niuu:flex niuu:flex-col niuu:gap-1">
          <button
            type="button"
            className={BUTTON}
            disabled={leave.isPending}
            onClick={() => leave.mutate(viewerId, { onSuccess: () => onLeft?.() })}
          >
            {leave.isPending ? 'Leaving…' : 'Leave chat'}
          </button>
          {leave.error ? <span className={ERROR}>{errorText(leave.error)}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The header's People button. The owner and admins manage the chat's people; a participant
 * sees who is in it and may leave. Anyone else with access to the full session (a tenant
 * viewer) has no standing to list or change the chat's people, so gets no button.
 */
export function SessionPeopleButton({
  session,
  onLeft,
}: {
  session: VolundrSession;
  onLeft?: () => void;
}) {
  const identity = useViewerIdentity();
  const [open, setOpen] = useState(false);
  const shared = isSharedSession(session);
  const viewerId = identity.data?.userId;
  const isAdmin = identity.data?.roles.includes('volundr:admin') ?? false;
  const manage = !shared && (isAdmin || (viewerId !== undefined && session.ownerId === viewerId));
  if (!shared && !manage) return null;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="People"
          title="People in this chat"
          className="niuu-live-session__toolbar-button niuu-live-session__toolbar-button--neutral"
          data-testid="session-people-button"
        >
          <Users className="niuu-live-session__toolbar-icon" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="niuu:w-[380px] niuu:max-w-[calc(100vw-24px)] niuu:p-4">
        <div className="niuu:flex niuu:flex-col niuu:gap-2">
          <strong className="niuu:text-sm niuu:text-text-primary">People in this chat</strong>
          <SessionPeoplePanel
            session={session}
            onLeft={() => {
              setOpen(false);
              onLeft?.();
            }}
          />
        </div>
      </PopoverContent>
    </Popover>
  );
}
