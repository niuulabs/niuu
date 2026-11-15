import type { RoomParticipant } from '@niuulabs/ui';
import type { Session, SessionState } from './session';

/** A member session's state as the run room's worker status. */
const STOPPED_STATES: ReadonlySet<SessionState> = new Set([
  'terminating',
  'terminated',
  'archived',
]);

function memberStatus(state: SessionState, live: string | undefined): string {
  if (STOPPED_STATES.has(state)) return 'stopped';
  if (state === 'failed') return 'error';
  // While the member runs, the live room says what it is doing; else Forge's record does.
  if (live) return live;
  return state === 'running' || state === 'awaiting_input' ? 'running' : 'idle';
}

/**
 * The run room's participants, with every member of the run in it. A workflow run's first
 * session hosts the run's room; its other members are the sessions nested under it that
 * work for the same run. Forge's record names them, so a finished run (no live room) still
 * shows each member as a worker, and the live room, while it runs, adds what they are doing.
 * Each member's status follows its session: running, idle, stopped.
 */
export function withRunMembers(
  participants: ReadonlyMap<string, RoomParticipant>,
  host: Pick<Session, 'id' | 'runId'> | null,
  members: readonly Session[],
): ReadonlyMap<string, RoomParticipant> {
  if (!host?.runId) return participants;
  const runMembers = members.filter((member) => member.runId === host.runId);
  if (runMembers.length === 0) return participants;
  const next = new Map(participants);
  for (const member of runMembers) {
    const known = participants.get(member.id);
    next.set(member.id, {
      peerId: member.id,
      persona: known?.persona ?? member.coordination?.role ?? '',
      participantType: 'skuld',
      ...known,
      displayName: known?.displayName || member.name || member.id,
      status: memberStatus(member.state, known?.status),
    });
  }
  return next;
}
