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

/** The session's lead (Skuld) speaks as the Coordinator. */
export function isCoordinator(participant: ParticipantMeta | undefined): boolean {
  return participant?.participantType === 'skuld';
}

/** The coordinator's speaker, shared by messages that carry no participant. */
export function coordinatorSpeaker(): Speaker {
  return { name: COORDINATOR_LABEL, color: COORDINATOR_COLOR, coordinator: true };
}

/** Who a participant is in a room of workers: its name and persona colour. */
export function speakerFor(participant: ParticipantMeta): Speaker {
  if (isCoordinator(participant)) return coordinatorSpeaker();
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
