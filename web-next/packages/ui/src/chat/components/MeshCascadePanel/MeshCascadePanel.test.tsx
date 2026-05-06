import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MeshCascadePanel } from './MeshCascadePanel';
import type { MeshEvent } from '../../types';

const events: MeshEvent[] = [
  {
    id: 'e1',
    type: 'outcome',
    participantId: 'peer-1',
    participant: { color: '#38bdf8' },
    timestamp: new Date(),
    persona: 'Ada',
    eventType: 'review',
    verdict: 'pass',
    summary: 'All good',
  },
  {
    id: 'e2',
    type: 'mesh_message',
    participantId: 'peer-1',
    participant: { color: '#38bdf8' },
    timestamp: new Date(),
    fromPersona: 'Ada',
    eventType: 'delegate',
    preview: 'Take this task',
  },
];

describe('MeshCascadePanel', () => {
  it('returns null when events is empty', () => {
    const { container } = render(<MeshCascadePanel events={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders all events', () => {
    render(<MeshCascadePanel events={events} />);
    expect(screen.getByTestId('mesh-cascade-panel')).toBeInTheDocument();
    expect(screen.getAllByText('Ada').length).toBeGreaterThan(0);
  });

  it('shows correct event count badge', () => {
    render(<MeshCascadePanel events={events} />);
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('calls onEventClick when event clicked', () => {
    const onEventClick = vi.fn();
    render(<MeshCascadePanel events={events} onEventClick={onEventClick} />);
    // Click on the first timeline item
    const items = screen.getAllByText('Ada');
    fireEvent.click(items[0]);
    expect(onEventClick).toHaveBeenCalledWith(events[0]);
  });

  it('routes Show details to the outcome detail handler without triggering card click', () => {
    const onEventClick = vi.fn();
    const onOutcomeShowDetails = vi.fn();
    render(
      <MeshCascadePanel
        events={events}
        onEventClick={onEventClick}
        onOutcomeShowDetails={onOutcomeShowDetails}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Show details' }));

    expect(onOutcomeShowDetails).toHaveBeenCalledWith(events[0]);
    expect(onEventClick).not.toHaveBeenCalled();
  });

  it('shows summary counts', () => {
    render(<MeshCascadePanel events={events} />);
    expect(screen.getByText('1 outcome')).toBeInTheDocument();
    expect(screen.getByText('1 delegation')).toBeInTheDocument();
  });

  it('renders a collapsed rail and expands from the header control', () => {
    const onToggleCollapsed = vi.fn();
    render(<MeshCascadePanel events={events} collapsed onToggleCollapsed={onToggleCollapsed} />);

    expect(
      screen.getByRole('button', { name: /expand mesh cascade sidebar/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText('Mesh Cascade')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /expand mesh cascade sidebar/i }));
    expect(onToggleCollapsed).toHaveBeenCalledTimes(1);
  });
});
