import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { MeshEventCard } from './MeshEventCard';
import type { MeshEvent } from '../../types';

const outcomeEvent: MeshEvent = {
  id: 'e1',
  type: 'outcome',
  participantId: 'peer-1',
  participant: { color: '#38bdf8' },
  timestamp: new Date('2024-01-01T12:00:00'),
  persona: 'Ada',
  eventType: 'review',
  verdict: 'pass',
  summary: 'All checks passed',
};

const delegationEvent: MeshEvent = {
  id: 'e2',
  type: 'mesh_message',
  participantId: 'peer-1',
  participant: { color: '#38bdf8' },
  timestamp: new Date('2024-01-01T12:01:00'),
  fromPersona: 'Ada',
  eventType: 'delegate',
  preview: 'Delegating task to Björk',
};

const notificationEvent: MeshEvent = {
  id: 'e3',
  type: 'notification',
  participantId: 'peer-2',
  participant: { color: '#a78bfa' },
  timestamp: new Date('2024-01-01T12:02:00'),
  persona: 'Björk',
  notificationType: 'clarification',
  summary: 'Need more context',
  urgency: 0.8,
  reason: 'Ambiguous requirements',
  recommendation: 'Provide more details',
};

describe('MeshEventCard', () => {
  it('renders outcome card with persona and verdict', () => {
    render(<MeshEventCard event={outcomeEvent} />);
    expect(screen.getByText('Ada')).toBeInTheDocument();
    expect(screen.getByText('Passed')).toBeInTheDocument();
    expect(screen.getByText('All checks passed')).toBeInTheDocument();
    expect(screen.getByText('Show details')).toBeInTheDocument();
  });

  it('calls onShowDetails only from the detail control', () => {
    const onShowDetails = vi.fn();
    render(<MeshEventCard event={outcomeEvent} onShowDetails={onShowDetails} />);
    fireEvent.click(screen.getByRole('button', { name: 'Show details' }));
    expect(onShowDetails).toHaveBeenCalledWith(outcomeEvent);
  });

  it('renders delegation card with fromPersona', () => {
    render(<MeshEventCard event={delegationEvent} />);
    expect(screen.getByText('Ada')).toBeInTheDocument();
    expect(screen.getByText('Delegating task to Björk')).toBeInTheDocument();
  });

  it('renders notification card with urgency', () => {
    render(<MeshEventCard event={notificationEvent} />);
    expect(screen.getByText('Björk')).toBeInTheDocument();
    expect(screen.getByText('Need more context')).toBeInTheDocument();
    expect(screen.getByText('Reason: Ambiguous requirements')).toBeInTheDocument();
  });

  it('renders fail verdict', () => {
    const event: MeshEvent = { ...outcomeEvent, id: 'e4', verdict: 'fail' };
    render(<MeshEventCard event={event} />);
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });

  it('renders needs_changes verdict', () => {
    const event: MeshEvent = { ...outcomeEvent, id: 'e5', verdict: 'needs_changes' };
    render(<MeshEventCard event={event} />);
    expect(screen.getByText('Changes Requested')).toBeInTheDocument();
  });
});
