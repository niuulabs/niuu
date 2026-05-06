import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ParticipantFilter } from './ParticipantFilter';
import type { RoomParticipant } from '@/modules/shared/hooks/useSkuldChat';

function makeParticipant(overrides: Partial<RoomParticipant> = {}): RoomParticipant {
  return {
    peerId: 'peer-1',
    persona: 'Ravn-Alpha',
    color: 'p1',
    participantType: 'ravn',
    status: 'idle',
    joinedAt: new Date(),
    ...overrides,
  };
}

function makeParticipantsMap(
  participants: RoomParticipant[]
): ReadonlyMap<string, RoomParticipant> {
  return new Map(participants.map(p => [p.peerId, p]));
}

describe('ParticipantFilter', () => {
  const twoParticipants = makeParticipantsMap([
    makeParticipant({ peerId: 'peer-1', persona: 'Ravn-Alpha', color: 'p1' }),
    makeParticipant({ peerId: 'peer-2', persona: 'Ravn-Beta', color: 'p2' }),
  ]);

  it('renders All pill', () => {
    render(
      <ParticipantFilter
        participants={twoParticipants}
        activeFilter="all"
        onFilterChange={() => {}}
        showInternal={false}
        onToggleInternal={() => {}}
      />
    );
    expect(screen.getByText('All')).toBeInTheDocument();
  });

  it('renders a pill for each participant', () => {
    render(
      <ParticipantFilter
        participants={twoParticipants}
        activeFilter="all"
        onFilterChange={() => {}}
        showInternal={false}
        onToggleInternal={() => {}}
      />
    );
    expect(screen.getByText('Ravn-Alpha')).toBeInTheDocument();
    expect(screen.getByText('Ravn-Beta')).toBeInTheDocument();
  });

  it('calls onFilterChange with "all" when All clicked', () => {
    const handleChange = vi.fn();
    render(
      <ParticipantFilter
        participants={twoParticipants}
        activeFilter="peer-1"
        onFilterChange={handleChange}
        showInternal={false}
        onToggleInternal={() => {}}
      />
    );
    fireEvent.click(screen.getByText('All'));
    expect(handleChange).toHaveBeenCalledWith('all');
  });

  it('calls onFilterChange with participant peerId when pill clicked', () => {
    const handleChange = vi.fn();
    render(
      <ParticipantFilter
        participants={twoParticipants}
        activeFilter="all"
        onFilterChange={handleChange}
        showInternal={false}
        onToggleInternal={() => {}}
      />
    );
    fireEvent.click(screen.getByText('Ravn-Alpha'));
    expect(handleChange).toHaveBeenCalledWith('peer-1');
  });

  it('calls onToggleInternal when toggle button clicked', () => {
    const handleToggle = vi.fn();
    render(
      <ParticipantFilter
        participants={twoParticipants}
        activeFilter="all"
        onFilterChange={() => {}}
        showInternal={false}
        onToggleInternal={handleToggle}
      />
    );
    fireEvent.click(screen.getByTitle('Show internal messages'));
    expect(handleToggle).toHaveBeenCalled();
  });

  it('shows Eye icon when showInternal is true', () => {
    render(
      <ParticipantFilter
        participants={twoParticipants}
        activeFilter="all"
        onFilterChange={() => {}}
        showInternal={true}
        onToggleInternal={() => {}}
      />
    );
    // Eye icon present — button title changes
    expect(screen.getByTitle('Hide internal messages')).toBeInTheDocument();
  });

  it('shows EyeOff icon when showInternal is false', () => {
    render(
      <ParticipantFilter
        participants={twoParticipants}
        activeFilter="all"
        onFilterChange={() => {}}
        showInternal={false}
        onToggleInternal={() => {}}
      />
    );
    expect(screen.getByTitle('Show internal messages')).toBeInTheDocument();
  });

  it('renders with empty participants map', () => {
    render(
      <ParticipantFilter
        participants={new Map()}
        activeFilter="all"
        onFilterChange={() => {}}
        showInternal={false}
        onToggleInternal={() => {}}
      />
    );
    expect(screen.getByText('All')).toBeInTheDocument();
  });

  it('sets aria-pressed=true when showInternal is true', () => {
    render(
      <ParticipantFilter
        participants={twoParticipants}
        activeFilter="all"
        onFilterChange={() => {}}
        showInternal={true}
        onToggleInternal={() => {}}
      />
    );
    const btn = screen.getByTitle('Hide internal messages');
    expect(btn).toHaveAttribute('aria-pressed', 'true');
  });
});
