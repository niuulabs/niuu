import type { ParticipantMeta } from '../../types';
import { resolveParticipantColor } from '../../utils/participantColor';

/** What the session itself is called in a room of workers. */
export const COORDINATOR_LABEL = 'Coordinator';

/** The coordinator has no persona hue of its own; it wears the brand colour. */
const COORDINATOR_COLOR = 'var(--color-brand)';

export interface Speaker {
  name: string;
  color: string;
  /** The session's own voice rather than a worker's. */
  coordinator: boolean;
}

/**
 * The session whose room this is: its mesh peer (its session id) and what it is called there.
 * Without a peer every Skuld participant is the room's lead, as before.
 */
export interface RoomHost {
  peerId?: string;
  /** The lead's name: Coordinator, or its role in a workflow run's room (Coder). */
  label?: string;
}

/**
 * The session's lead (Skuld) speaks as the Coordinator. In a workflow run's room the other
 * members are Skuld sessions too; only the host is the lead.
 */
export function isCoordinator(participant: ParticipantMeta | undefined, host?: RoomHost): boolean {
  if (participant?.participantType !== 'skuld') return false;
  return !host?.peerId || participant.peerId === host.peerId;
}

/** The coordinator's speaker, shared by messages that carry no participant. */
export function coordinatorSpeaker(label: string = COORDINATOR_LABEL): Speaker {
  return { name: label, color: COORDINATOR_COLOR, coordinator: true };
}

/** Who a participant is in a room of workers: its name and persona colour. */
export function speakerFor(participant: ParticipantMeta, host?: RoomHost): Speaker {
  if (isCoordinator(participant, host)) return coordinatorSpeaker(host?.label);
  return {
    name: participant.displayName || participant.persona,
    color: resolveParticipantColor(participant.peerId, participant.color),
    coordinator: false,
  };
}

/** HH:MM, the way room speakers show when they spoke. */
export function speakerTime(date: Date): string {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
