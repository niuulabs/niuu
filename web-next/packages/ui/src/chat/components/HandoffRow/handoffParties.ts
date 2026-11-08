import type { MeshDelegationEvent, RoomParticipant } from '../../types';
import { COORDINATOR_LABEL, speakerFor } from '../RoomMessage/speaker';
import type { Worker } from '../../workers';

export interface HandoffParty {
  name: string;
  color: string;
  peerId?: string;
}

export interface HandoffParties {
  from: HandoffParty;
  /** The workers that listen for the event; empty when none does. */
  targets: readonly Worker[];
}

/** Colour of a sender the room does not list: no persona hue applies. */
const UNKNOWN_SENDER_COLOR = 'var(--color-text-muted)';

/**
 * Who a delegation is between: the sender, found in the room by persona (then by the
 * participant the event came on), and the workers whose `consumes` names the event.
 */
export function handoffParties(
  event: MeshDelegationEvent,
  participants: ReadonlyMap<string, RoomParticipant>,
  workers: readonly Worker[],
): HandoffParties {
  const sender =
    [...participants.values()].find((participant) => participant.persona === event.fromPersona) ??
    participants.get(event.participantId);
  const from: HandoffParty = sender
    ? { ...speakerFor(sender), peerId: sender.peerId }
    : { name: event.fromPersona || COORDINATOR_LABEL, color: UNKNOWN_SENDER_COLOR };
  const targets = workers.filter(
    (worker) => worker.peerId !== sender?.peerId && worker.consumes.includes(event.eventType),
  );
  return { from, targets };
}
