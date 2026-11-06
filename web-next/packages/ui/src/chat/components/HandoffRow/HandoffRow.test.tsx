import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { HandoffRow } from './HandoffRow';
import { handoffParties } from './handoffParties';
import type { MeshDelegationEvent, RoomParticipant } from '../../types';
import type { Worker } from '../../workers';

const participant = (
  peerId: string,
  persona: string,
  extra: Partial<RoomParticipant> = {},
): RoomParticipant => ({ peerId, persona, participantType: 'ravn', ...extra });

const lead = participant('lead', 'Lead', { participantType: 'skuld', displayName: 'Skuld' });
const room = new Map(
  [lead, participant('coder', 'coder'), participant('rev', 'reviewer')].map((p) => [p.peerId, p]),
);

const worker = (peerId: string, name: string, consumes: string[], color: string): Worker => ({
  peerId,
  name,
  persona: name,
  color,
  status: 'idle',
  consumes,
  emits: [],
  outcomes: [],
});

const workers = [
  worker('coder', 'coder', ['code.requested', 'review.changes_requested'], '#aa0000'),
  worker('rev', 'reviewer', ['code.changed'], '#00aa00'),
  worker('aud', 'security-auditor', ['code.changed'], '#0000aa'),
];

const event = (over: Partial<MeshDelegationEvent> = {}): MeshDelegationEvent => ({
  id: 'e1',
  type: 'mesh_message',
  timestamp: new Date('2026-11-05T05:19:00Z'),
  participantId: 'coder',
  participant: {},
  fromPersona: 'coder',
  eventType: 'code.changed',
  preview: 'Review and audit the probe code',
  ...over,
});

describe('handoffParties', () => {
  it('finds the sender by persona and the workers that listen for the event', () => {
    const parties = handoffParties(event(), room, workers);
    expect(parties.from).toMatchObject({ name: 'coder', peerId: 'coder' });
    expect(parties.targets.map((target) => target.name)).toEqual(['reviewer', 'security-auditor']);
  });

  it('names the lead the Coordinator', () => {
    const parties = handoffParties(
      event({ fromPersona: 'Lead', participantId: 'lead', eventType: 'code.requested' }),
      room,
      workers,
    );
    expect(parties.from.name).toBe('Coordinator');
    expect(parties.targets.map((target) => target.name)).toEqual(['coder']);
  });

  it('never targets the sender itself', () => {
    const parties = handoffParties(
      event({ fromPersona: 'reviewer', participantId: 'rev', eventType: 'code.changed' }),
      room,
      workers,
    );
    expect(parties.targets.map((target) => target.name)).toEqual(['security-auditor']);
  });

  it('keeps the persona of a sender the room does not list, with no targets when nobody listens', () => {
    const parties = handoffParties(
      event({ fromPersona: 'stranger', participantId: 'gone', eventType: 'unheard.event' }),
      room,
      workers,
    );
    expect(parties.from).toEqual({ name: 'stranger', color: 'var(--color-text-muted)' });
    expect(parties.targets).toEqual([]);
  });

  it('finds the sender by its participant when the persona is not in the room', () => {
    const parties = handoffParties(
      event({ fromPersona: 'renamed', participantId: 'rev' }),
      room,
      workers,
    );
    expect(parties.from.peerId).toBe('rev');
  });
});

describe('HandoffRow', () => {
  it('reads from → to, what was handed over, and the event type', () => {
    render(
      <HandoffRow
        event={event()}
        parties={handoffParties(event(), room, workers)}
        onOpenWorker={vi.fn()}
      />,
    );
    const row = screen.getByTestId('handoff-row');
    expect(row).toHaveTextContent('coderreviewer · security-auditor');
    expect(row).toHaveTextContent('Review and audit the probe code');
    expect(row).toHaveTextContent('code.changed');
    expect(screen.getByText('reviewer')).toHaveStyle({ color: '#00aa00' });
  });

  it('opens the receiving worker when clicked', () => {
    const onOpenWorker = vi.fn();
    render(
      <HandoffRow
        event={event()}
        parties={handoffParties(event(), room, workers)}
        onOpenWorker={onOpenWorker}
      />,
    );
    fireEvent.click(screen.getByTestId('handoff-row'));
    expect(onOpenWorker).toHaveBeenCalledWith('rev');
  });

  it('shows the event only, and nothing to open, when no worker listens for it', () => {
    const onOpenWorker = vi.fn();
    const lonely = event({ eventType: 'unheard.event' });
    render(
      <HandoffRow
        event={lonely}
        parties={handoffParties(lonely, room, workers)}
        onOpenWorker={onOpenWorker}
      />,
    );
    const row = screen.getByTestId('handoff-row');
    expect(row.tagName).toBe('DIV');
    expect(row).toHaveTextContent('unheard.event');
    expect(screen.queryByLabelText('to')).toBeNull();
    fireEvent.click(row);
    expect(onOpenWorker).not.toHaveBeenCalled();
  });
});
