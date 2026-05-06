import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ParticipantFilter } from './ParticipantFilter';
import type { RoomParticipant } from '../../types';

const participants: ReadonlyMap<string, RoomParticipant> = new Map([
  ['peer-1', { peerId: 'peer-1', persona: 'Ada', color: '#38bdf8' }],
  ['peer-2', { peerId: 'peer-2', persona: 'Björk', displayName: 'Björk', color: '#a78bfa' }],
]);

describe('ParticipantFilter', () => {
  it('renders All tab and participant tabs', () => {
    render(
      <ParticipantFilter
        participants={participants}
        activeFilter="all"
        onFilterChange={vi.fn()}
        showInternal={false}
        onToggleInternal={vi.fn()}
      />,
    );
    expect(screen.getByTestId('filter-tab-all')).toBeInTheDocument();
    expect(screen.getByTestId('filter-tab-peer-1')).toBeInTheDocument();
    expect(screen.getByTestId('filter-tab-peer-2')).toBeInTheDocument();
  });

  it('calls onFilterChange when tab clicked', () => {
    const onFilterChange = vi.fn();
    render(
      <ParticipantFilter
        participants={participants}
        activeFilter="all"
        onFilterChange={onFilterChange}
        showInternal={false}
        onToggleInternal={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('filter-tab-peer-1'));
    expect(onFilterChange).toHaveBeenCalledWith('peer-1');
  });

  it('shows displayName when participant has it', () => {
    render(
      <ParticipantFilter
        participants={participants}
        activeFilter="all"
        onFilterChange={vi.fn()}
        showInternal={false}
        onToggleInternal={vi.fn()}
      />,
    );
    expect(screen.getByText('Björk (Björk)')).toBeInTheDocument();
  });

  it('marks active tab with aria-pressed', () => {
    render(
      <ParticipantFilter
        participants={participants}
        activeFilter="peer-1"
        onFilterChange={vi.fn()}
        showInternal={false}
        onToggleInternal={vi.fn()}
      />,
    );
    expect(screen.getByTestId('filter-tab-peer-1')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('filter-tab-all')).toHaveAttribute('aria-pressed', 'false');
  });

  it('calls onToggleInternal when internal button clicked', () => {
    const onToggleInternal = vi.fn();
    render(
      <ParticipantFilter
        participants={participants}
        activeFilter="all"
        onFilterChange={vi.fn()}
        showInternal={false}
        onToggleInternal={onToggleInternal}
      />,
    );
    fireEvent.click(screen.getByTestId('internal-toggle'));
    expect(onToggleInternal).toHaveBeenCalled();
  });

  it('shows correct internal toggle state', () => {
    const { rerender } = render(
      <ParticipantFilter
        participants={participants}
        activeFilter="all"
        onFilterChange={vi.fn()}
        showInternal={false}
        onToggleInternal={vi.fn()}
      />,
    );
    expect(screen.getByTestId('internal-toggle')).toHaveAttribute('aria-pressed', 'false');
    rerender(
      <ParticipantFilter
        participants={participants}
        activeFilter="all"
        onFilterChange={vi.fn()}
        showInternal={true}
        onToggleInternal={vi.fn()}
      />,
    );
    expect(screen.getByTestId('internal-toggle')).toHaveAttribute('aria-pressed', 'true');
  });
});
