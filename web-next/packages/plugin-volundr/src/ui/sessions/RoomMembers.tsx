import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { authorInitials, cn, verdictLabel } from '@niuulabs/ui';
import type { Worker } from '@niuulabs/ui';
import { modelLabel as readableModel } from '@niuulabs/domain';
import { sessionHarness, type Session, type SessionState } from '../../domain/session';
import type { IVolundrService } from '../../ports/IVolundrService';
import type {
  SessionMemberAgent,
  SessionMemberPerson,
  SessionStatus,
} from '../../models/volundr.model';
import { roleLabel } from '../../domain/projectTree';
import { getDefinitionRune } from '../launchWizardModel';
import { errorText } from '../errorText';
import { MemberAvatar, memberColor } from '../MemberAvatar';
import { sessionPeopleKey, useViewerIdentity } from '../hooks/useSessionPeople';
import { LIVE_REFETCH_MS } from '../hooks/useSessionStore';
import { SESSION_STATE_LABELS } from './presentation';
import './RoomMembers.css';

/** The open session's room: its workers, the one the chat shows alone, and how to pick one. */
export interface OpenRoom {
  sessionId: string;
  workers: readonly Worker[];
  /** Workers that are sessions of their own (a workflow run's members), nested under the room. */
  sessionWorkerIds: ReadonlySet<string>;
  focusedPeerId: string | null;
  onSelect: (sessionId: string, peerId: string) => void;
}

export const WORKER_STATUS_TEXT: Record<Worker['status'], string> = {
  working: 'Working',
  idle: 'Idle',
  error: 'Error',
  left: 'Left',
  stopped: 'Stopped',
};

/**
 * One member of a room under its session in the list: a worker of the open room, an agent
 * of a team, or a person. Selectable when `onSelect` is given; a person is only shown.
 */
export function MemberRow({
  testId,
  title,
  name,
  role,
  roleTestId,
  detail,
  color,
  avatar,
  status,
  statusKind,
  badge,
  selected = false,
  onSelect,
}: {
  testId: string;
  /** What the row's tooltip says it does. */
  title: string;
  name: string;
  /** A run member's role, ahead of its name. */
  role?: string;
  roleTestId?: string;
  /** Said after the name, quieter: the agent's engine, or the person's standing. */
  detail?: string;
  /** The member's colour: its dot, or its avatar's ring. */
  color: string;
  /** With an avatar the row shows it; without one, a dot. */
  avatar?: { kind: 'agent' | 'person'; glyph: string };
  status?: string;
  statusKind?: 'working' | 'idle' | 'error' | 'quiet';
  /** A mark on the member, such as the agent that leads. */
  badge?: string;
  selected?: boolean;
  onSelect?: () => void;
}) {
  const content = (
    <>
      {avatar ? (
        <MemberAvatar kind={avatar.kind} glyph={avatar.glyph} color={color} small />
      ) : (
        <span className="forge-room-worker__dot" aria-hidden="true" />
      )}
      {role ? (
        <span className="forge-session-row__role" data-testid={roleTestId}>
          {role}
        </span>
      ) : null}
      <span className="forge-session-row__title">{name}</span>
      {detail ? <span className="forge-room-member__detail">{detail}</span> : null}
      {badge ? <span className="forge-room-member__badge">{badge}</span> : null}
      {status ? (
        <span className="forge-room-worker__state" data-status={statusKind}>
          {status}
        </span>
      ) : null}
    </>
  );
  const tint = { '--forge-worker-color': color } as React.CSSProperties;
  const classes = cn(
    'forge-session-row__select niuu:flex niuu:w-full niuu:items-center niuu:gap-2 niuu:border-b niuu:border-l-2 niuu:px-3 niuu:py-1.5 niuu:text-left niuu:transition-colors',
  );
  return (
    <div className="forge-session-row forge-room-worker" data-nested="true">
      {onSelect ? (
        <button
          type="button"
          onClick={onSelect}
          aria-current={selected ? 'true' : undefined}
          title={title}
          data-testid={testId}
          className={cn(
            classes,
            selected
              ? 'niuu:border-brand niuu:border-b-white/10 forge-session-row__select--selected'
              : 'niuu:border-transparent niuu:border-b-white/6 niuu:hover:bg-bg-tertiary',
          )}
          style={tint}
        >
          {content}
        </button>
      ) : (
        <div
          title={title}
          data-testid={testId}
          className={cn(classes, 'niuu:border-transparent niuu:border-b-white/6')}
          style={tint}
        >
          {content}
        </div>
      )}
    </div>
  );
}

/** A worker in the open session's room, under its coordinator: picking it shows only its output. */
export function RoomWorkerRow({
  worker,
  selected,
  onSelect,
}: {
  worker: Worker;
  selected: boolean;
  onSelect: () => void;
}) {
  const hasRole =
    worker.persona && worker.persona !== worker.peerId && worker.persona !== worker.name;
  return (
    <MemberRow
      testId={`room-worker-${worker.peerId}`}
      title={`${worker.name}: show only this worker`}
      name={worker.name}
      role={hasRole ? roleLabel(worker.persona) : undefined}
      roleTestId={`room-worker-${worker.peerId}-role`}
      color={worker.color}
      status={
        worker.latest && worker.status !== 'working'
          ? verdictLabel(worker.latest.verdict)
          : WORKER_STATUS_TEXT[worker.status]
      }
      statusKind={worker.status === 'working' || worker.status === 'error' ? worker.status : 'idle'}
      selected={selected}
      onSelect={onSelect}
    />
  );
}

/** What the host last said about a member session that is not in the list now. */
const MEMBER_STATUS_TEXT: Record<SessionStatus, string> = {
  created: 'Queued',
  starting: 'Starting',
  provisioning: 'Starting',
  running: 'Running',
  stopping: 'Stopping',
  stopped: 'Stopped',
  failed: 'Error',
  error: 'Error',
  archived: 'Archived',
};

const HARNESS_LABEL = { claude: 'Claude Code', codex: 'Codex' } as const;

/** The engine an agent runs on, as a person names it. */
export function engineLabel(agent: Pick<SessionMemberAgent, 'definition' | 'model'>): string {
  const harness = sessionHarness({
    model: agent.model,
    sessionDefinition: agent.definition ?? undefined,
  });
  if (harness) return HARNESS_LABEL[harness];
  return agent.definition || readableModel(agent.model);
}

function liveStatusKind(state: SessionState): 'working' | 'idle' | 'error' {
  if (state === 'running') return 'working';
  if (state === 'failed') return 'error';
  return 'idle';
}

function fetchedStatusKind(status: SessionStatus): 'idle' | 'error' {
  return status === 'failed' || status === 'error' ? 'error' : 'idle';
}

/** How a person stands in the room: owner, invited, or the role they were given. */
function personDetail(person: SessionMemberPerson): string {
  if (person.role === 'owner') return 'owner';
  if (person.status === 'invited') return 'invited';
  return person.role;
}

function personStatus(person: SessionMemberPerson): string | undefined {
  if (person.online === null) return undefined;
  return person.online ? 'Online' : 'Offline';
}

/** States in which nobody comes or goes any more, so the members are read once. */
const SETTLED_STATES: ReadonlySet<SessionState> = new Set(['terminated', 'archived', 'failed']);

/**
 * Who is in a session's room: its agents and people, from the host. While the session runs,
 * people come and go and agents change state, so it is re-read at the live views' cadence.
 */
export function useSessionMembers(
  sessionId: string,
  instanceId: string | undefined,
  { live }: { live: boolean },
) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    // Under the session's people, so inviting or removing someone refreshes it.
    queryKey: [...sessionPeopleKey(sessionId), 'members'] as const,
    queryFn: ({ signal }) => volundr.getSessionMembers(sessionId, { instanceId, signal }),
    refetchInterval: live ? LIVE_REFETCH_MS : false,
  });
}

/**
 * The agents and people of a unit, listed under its row. The agents are the team's member
 * sessions (or the session itself) and a person is the owner or someone invited. Members
 * that are also workers of the open room are the room's own rows, so picking one shows only
 * its output in the chat; the others open their session.
 */
export function SessionMembersList({
  session,
  showAgents,
  memberSessions,
  room,
  selectedId,
  onSelectSession,
}: {
  session: Session;
  /** List the agents. Off when the session's own worker rows already do. */
  showAgents: boolean;
  /** The member sessions in the list now: their state is newer than what the host said. */
  memberSessions: readonly Session[];
  /** The open room, when this session is the open one. */
  room: OpenRoom | null;
  selectedId: string | null;
  onSelectSession: (id: string) => void;
}) {
  const members = useSessionMembers(session.id, session.clusterId, {
    live: !SETTLED_STATES.has(session.state),
  });
  const identity = useViewerIdentity();

  if (members.isPending || identity.isPending) {
    return (
      <p className="forge-room-member__note" data-testid={`session-members-loading-${session.id}`}>
        Loading members…
      </p>
    );
  }
  if (members.isError || identity.isError) {
    return (
      <p role="alert" className="forge-room-member__note forge-room-member__note--error">
        {errorText(members.error ?? identity.error, 'Could not load who is in this session.')}
      </p>
    );
  }

  const { agents, people } = members.data;
  const viewerId = identity.data.userId;
  const live = new Map(memberSessions.map((member) => [member.id, member]));
  const roomWorkers = new Map((room?.workers ?? []).map((worker) => [worker.peerId, worker]));
  const agentIds = new Set(agents.map((agent) => agent.sessionId));

  return (
    <div data-testid={`session-members-${session.id}`}>
      {showAgents &&
        agents.map((agent, index) => {
          const worker = roomWorkers.get(agent.sessionId);
          const member = live.get(agent.sessionId);
          const name = agent.personaName || agent.name;
          if (worker && room) {
            return (
              <MemberRow
                key={agent.sessionId}
                testId={`room-worker-${worker.peerId}`}
                title={`${name}: show only this worker`}
                name={name}
                detail={engineLabel(agent)}
                color={memberColor(index)}
                avatar={{ kind: 'agent', glyph: getDefinitionRune(agent.definition ?? '') }}
                status={
                  worker.latest && worker.status !== 'working'
                    ? verdictLabel(worker.latest.verdict)
                    : WORKER_STATUS_TEXT[worker.status]
                }
                statusKind={
                  worker.status === 'working' || worker.status === 'error' ? worker.status : 'idle'
                }
                badge={agent.lead ? 'Leads' : undefined}
                selected={room.focusedPeerId === worker.peerId}
                onSelect={() => room.onSelect(session.id, worker.peerId)}
              />
            );
          }
          return (
            <MemberRow
              key={agent.sessionId}
              testId={`session-agent-${agent.sessionId}`}
              title={`${name}: open this agent's session`}
              name={name}
              detail={engineLabel(agent)}
              color={memberColor(index)}
              avatar={{ kind: 'agent', glyph: getDefinitionRune(agent.definition ?? '') }}
              status={
                member ? SESSION_STATE_LABELS[member.state] : MEMBER_STATUS_TEXT[agent.status]
              }
              statusKind={member ? liveStatusKind(member.state) : fetchedStatusKind(agent.status)}
              badge={agent.lead ? 'Leads' : undefined}
              selected={selectedId === agent.sessionId && !room?.focusedPeerId}
              onSelect={() => onSelectSession(agent.sessionId)}
            />
          );
        })}
      {/* A worker of the room that is not one of the unit's agents keeps its own row. */}
      {showAgents &&
        room?.workers
          .filter((worker) => !agentIds.has(worker.peerId))
          .map((worker) => (
            <RoomWorkerRow
              key={worker.peerId}
              worker={worker}
              selected={room.focusedPeerId === worker.peerId}
              onSelect={() => room.onSelect(session.id, worker.peerId)}
            />
          ))}
      {people.map((person, index) => {
        const own = person.userId === viewerId;
        const name = own ? 'You' : person.displayName || person.userId;
        return (
          <MemberRow
            key={person.userId}
            testId={`session-person-${person.userId}`}
            title={name}
            name={name}
            detail={personDetail(person)}
            // The owner is the unit's own colour; the others follow the agents.
            color={index === 0 ? 'var(--color-brand)' : memberColor(agents.length + index - 1)}
            avatar={{
              kind: 'person',
              glyph: authorInitials({ userId: person.userId, displayName: person.displayName }),
            }}
            status={personStatus(person)}
            statusKind={person.online ? 'working' : 'quiet'}
          />
        );
      })}
    </div>
  );
}
