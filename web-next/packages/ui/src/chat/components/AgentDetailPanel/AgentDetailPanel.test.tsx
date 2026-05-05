import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AgentDetailPanel } from './AgentDetailPanel';
import type { RoomParticipant, AgentInternalEvent } from '../../types';

const participant: RoomParticipant = {
  peerId: 'peer-1',
  persona: 'Ada',
  displayName: 'Ada Lovelace',
  status: 'thinking',
  color: '#38bdf8',
};

const events: AgentInternalEvent[] = [
  { id: 'ev-1', frameType: 'thought', data: 'Analyzing the problem...' },
  {
    id: 'ev-2',
    frameType: 'tool_start',
    data: 'bash',
    metadata: { tool_name: 'bash', input: 'ls -la' },
  },
  { id: 'ev-3', frameType: 'tool_result', data: 'total 24\n...', metadata: { tool_name: 'bash' } },
];

describe('AgentDetailPanel', () => {
  it('renders participant name', () => {
    render(<AgentDetailPanel participant={participant} events={events} onClose={vi.fn()} />);
    expect(screen.getByTestId('agent-persona-name')).toHaveTextContent('Ada Lovelace (Ada)');
  });

  it('renders status badge', () => {
    render(<AgentDetailPanel participant={participant} events={events} onClose={vi.fn()} />);
    expect(screen.getByTestId('agent-activity-status')).toHaveTextContent('thinking');
  });

  it('renders events', () => {
    render(<AgentDetailPanel participant={participant} events={events} onClose={vi.fn()} />);
    expect(screen.getByText('Analyzing the problem...')).toBeInTheDocument();
  });

  it('shows empty state when no events', () => {
    render(<AgentDetailPanel participant={participant} events={[]} onClose={vi.fn()} />);
    expect(screen.getByTestId('agent-detail-empty')).toBeInTheDocument();
  });

  it('calls onClose when close button clicked', () => {
    const onClose = vi.fn();
    render(<AgentDetailPanel participant={participant} events={events} onClose={onClose} />);
    fireEvent.click(screen.getByTestId('agent-detail-close'));
    expect(onClose).toHaveBeenCalled();
  });

  it('uses persona only when no displayName', () => {
    const p: RoomParticipant = { peerId: 'peer-2', persona: 'Björk' };
    render(<AgentDetailPanel participant={p} events={[]} onClose={vi.fn()} />);
    expect(screen.getByTestId('agent-persona-name')).toHaveTextContent('Björk');
  });

  it('shows tool_executing status label', () => {
    const p: RoomParticipant = { ...participant, status: 'tool_executing' };
    render(<AgentDetailPanel participant={p} events={[]} onClose={vi.fn()} />);
    expect(screen.getByTestId('agent-activity-status')).toHaveTextContent('running tool');
  });

  it('shows idle status label', () => {
    const p: RoomParticipant = { ...participant, status: 'idle' };
    render(<AgentDetailPanel participant={p} events={[]} onClose={vi.fn()} />);
    expect(screen.getByTestId('agent-activity-status')).toHaveTextContent('idle');
  });

  it('shows unknown for unrecognized status', () => {
    const p: RoomParticipant = { ...participant, status: undefined };
    render(<AgentDetailPanel participant={p} events={[]} onClose={vi.fn()} />);
    expect(screen.getByTestId('agent-activity-status')).toHaveTextContent('unknown');
  });

  it('renders thought event correctly', () => {
    render(<AgentDetailPanel participant={participant} events={[events[0]]} onClose={vi.fn()} />);
    // The event label "thinking" appears as a span label
    const labels = screen.getAllByText('thinking');
    expect(labels.length).toBeGreaterThan(0);
  });

  it('renders tool_start event correctly', () => {
    render(<AgentDetailPanel participant={participant} events={[events[1]]} onClose={vi.fn()} />);
    expect(screen.getByText(/tool: bash/i)).toBeInTheDocument();
  });

  it('renders tool_result event correctly', () => {
    render(<AgentDetailPanel participant={participant} events={[events[2]]} onClose={vi.fn()} />);
    expect(screen.getByText(/result: bash/i)).toBeInTheDocument();
  });

  it('calls onClose when Escape pressed (not in input)', () => {
    const onClose = vi.fn();
    render(<AgentDetailPanel participant={participant} events={events} onClose={onClose} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('does not call onClose when Escape pressed inside an input', () => {
    const onClose = vi.fn();
    render(
      <div>
        <AgentDetailPanel participant={participant} events={events} onClose={onClose} />
        <input data-testid="test-input" />
      </div>,
    );
    const input = screen.getByTestId('test-input');
    input.focus();
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });
});
