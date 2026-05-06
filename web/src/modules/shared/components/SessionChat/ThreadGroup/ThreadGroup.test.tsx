import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ThreadGroup } from './ThreadGroup';
import type { SkuldChatMessage } from '@/modules/shared/hooks/useSkuldChat';

// Mock RoomMessage so we focus on ThreadGroup behavior
vi.mock('../RoomMessage', () => ({
  RoomMessage: ({ message }: { message: SkuldChatMessage }) => (
    <div data-testid={`room-message-${message.id}`}>{message.content}</div>
  ),
}));

function makeMessage(overrides: Partial<SkuldChatMessage> = {}): SkuldChatMessage {
  return {
    id: 'msg-1',
    role: 'assistant',
    content: 'internal message',
    createdAt: new Date('2024-01-01T12:03:00'),
    status: 'complete',
    visibility: 'internal',
    threadId: 'thread-abc',
    participant: {
      peerId: 'peer-1',
      persona: 'Ravn-A',
      color: 'p1',
      participantType: 'ravn',
    },
    participantId: 'peer-1',
    ...overrides,
  };
}

const twoMessages = [
  makeMessage({
    id: 'msg-1',
    content: 'message one',
    createdAt: new Date('2024-01-01T12:03:00'),
    participant: { peerId: 'peer-1', persona: 'Ravn-A', color: 'p1', participantType: 'ravn' },
  }),
  makeMessage({
    id: 'msg-2',
    content: 'message two',
    createdAt: new Date('2024-01-01T12:07:00'),
    participantId: 'peer-2',
    participant: { peerId: 'peer-2', persona: 'Ravn-B', color: 'p2', participantType: 'ravn' },
  }),
];

describe('ThreadGroup', () => {
  it('renders collapsed by default when isCollapsed=true', () => {
    const { container } = render(
      <ThreadGroup messages={twoMessages} isCollapsed={true} onToggle={vi.fn()} />
    );
    const body = container.querySelector('[data-expanded]');
    expect(body).toHaveAttribute('data-expanded', 'false');
  });

  it('renders expanded when isCollapsed=false', () => {
    const { container } = render(
      <ThreadGroup messages={twoMessages} isCollapsed={false} onToggle={vi.fn()} />
    );
    const body = container.querySelector('[data-expanded]');
    expect(body).toHaveAttribute('data-expanded', 'true');
  });

  it('shows message count in label', () => {
    render(<ThreadGroup messages={twoMessages} isCollapsed={true} onToggle={vi.fn()} />);
    expect(screen.getByText(/2 messages/)).toBeInTheDocument();
  });

  it('shows participant personas in label', () => {
    render(<ThreadGroup messages={twoMessages} isCollapsed={true} onToggle={vi.fn()} />);
    expect(screen.getByText(/Ravn-A/)).toBeInTheDocument();
    expect(screen.getByText(/Ravn-B/)).toBeInTheDocument();
  });

  it('calls onToggle when header is clicked', () => {
    const onToggle = vi.fn();
    render(<ThreadGroup messages={twoMessages} isCollapsed={true} onToggle={onToggle} />);
    fireEvent.click(screen.getByRole('button'));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('shows messages when isCollapsed=false', () => {
    render(<ThreadGroup messages={twoMessages} isCollapsed={false} onToggle={vi.fn()} />);
    expect(screen.getByTestId('room-message-msg-1')).toBeInTheDocument();
    expect(screen.getByTestId('room-message-msg-2')).toBeInTheDocument();
  });

  it('sets aria-expanded=false when isCollapsed=true', () => {
    render(<ThreadGroup messages={twoMessages} isCollapsed={true} onToggle={vi.fn()} />);
    expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'false');
  });

  it('sets aria-expanded=true when isCollapsed=false', () => {
    render(<ThreadGroup messages={twoMessages} isCollapsed={false} onToggle={vi.fn()} />);
    expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'true');
  });

  it('shows plain count label when no participant has a persona', () => {
    const msgs = [
      makeMessage({ id: 'msg-1', participant: undefined }),
      makeMessage({ id: 'msg-2', participant: undefined }),
    ];
    render(<ThreadGroup messages={msgs} isCollapsed={true} onToggle={vi.fn()} />);
    expect(screen.getByText(/2 messages/)).toBeInTheDocument();
    expect(screen.queryByText(/—/)).not.toBeInTheDocument();
  });

  it('shows singular "message" for single message with no persona', () => {
    const one = [makeMessage({ id: 'msg-1', participant: undefined })];
    render(<ThreadGroup messages={one} isCollapsed={true} onToggle={vi.fn()} />);
    expect(screen.getByText('1 message')).toBeInTheDocument();
    expect(screen.queryByText(/1 messages/)).not.toBeInTheDocument();
    expect(screen.queryByText(/—/)).not.toBeInTheDocument();
  });

  it('shows singular "message" for single message', () => {
    const one = [makeMessage({ id: 'msg-1' })];
    render(<ThreadGroup messages={one} isCollapsed={true} onToggle={vi.fn()} />);
    expect(screen.getByText(/1 message/)).toBeInTheDocument();
    expect(screen.queryByText(/1 messages/)).not.toBeInTheDocument();
  });

  it('shows time range for messages with different timestamps', () => {
    render(<ThreadGroup messages={twoMessages} isCollapsed={true} onToggle={vi.fn()} />);
    const timeRange = document.querySelector('[class*="timeRange"]');
    expect(timeRange).toBeInTheDocument();
    expect(timeRange!.textContent).toContain('–');
  });

  it('shows single time for messages with same timestamp', () => {
    const sameTime = new Date('2024-01-01T12:03:00');
    const msgs = [
      makeMessage({ id: 'msg-1', createdAt: sameTime }),
      makeMessage({ id: 'msg-2', createdAt: sameTime }),
    ];
    render(<ThreadGroup messages={msgs} isCollapsed={true} onToggle={vi.fn()} />);
    const timeRange = document.querySelector('[class*="timeRange"]');
    expect(timeRange).toBeInTheDocument();
    expect(timeRange!.textContent).not.toContain('–');
  });

  it('applies --thread-border-color from first participant color', () => {
    const { container } = render(
      <ThreadGroup messages={twoMessages} isCollapsed={true} onToggle={vi.fn()} />
    );
    const group = container.firstChild as HTMLElement;
    expect(group.style.getPropertyValue('--thread-border-color')).toBe(
      'var(--color-participant-1)'
    );
  });

  it('applies fallback border color when no participant', () => {
    const msgs = [
      makeMessage({ id: 'msg-1', participant: undefined }),
      makeMessage({ id: 'msg-2', participant: undefined }),
    ];
    const { container } = render(
      <ThreadGroup messages={msgs} isCollapsed={true} onToggle={vi.fn()} />
    );
    const group = container.firstChild as HTMLElement;
    expect(group.style.getPropertyValue('--thread-border-color')).toBe('var(--color-border)');
  });
});
