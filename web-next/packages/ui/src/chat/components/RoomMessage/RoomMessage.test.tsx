import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RoomMessage } from './RoomMessage';
import { OutcomeCard } from '../OutcomeCard';
import type { ChatMessage } from '../../types';

const now = new Date('2026-11-05T05:12:00');

const userMsg: ChatMessage = {
  id: 'r1',
  role: 'user',
  content: 'Hello from user',
  createdAt: now,
  participant: { peerId: 'p1', persona: 'Odin' },
};

const assistantMsg: ChatMessage = {
  id: 'r2',
  role: 'assistant',
  content: 'Response from agent',
  createdAt: now,
  participant: { peerId: 'p1', persona: 'Odin', color: '#123456' },
};

const leadMsg: ChatMessage = {
  id: 'r6',
  role: 'assistant',
  content: 'Framing the spec first.',
  createdAt: now,
  participant: { peerId: 'lead', persona: 'Skuld', displayName: 'Skuld', participantType: 'skuld' },
};

Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });

describe('RoomMessage', () => {
  it('renders participant label', () => {
    render(<RoomMessage message={userMsg} />);
    expect(screen.getByText('Odin')).toBeInTheDocument();
  });

  it('renders user message', () => {
    render(<RoomMessage message={userMsg} />);
    expect(screen.getByText('Hello from user')).toBeInTheDocument();
  });

  it('renders assistant message', () => {
    render(<RoomMessage message={assistantMsg} />);
    expect(screen.getByText('Response from agent')).toBeInTheDocument();
  });

  it('renders system message for system messageType', () => {
    const sysMsg: ChatMessage = {
      id: 'r3',
      role: 'system' as const,
      content: 'System event',
      createdAt: now,
      metadata: { messageType: 'system' },
    };
    render(<RoomMessage message={sysMsg} />);
    expect(screen.getByTestId('system-message')).toBeInTheDocument();
  });

  it('renders streaming message for running assistant', () => {
    const streamMsg: ChatMessage = {
      id: 'r4',
      role: 'assistant',
      content: 'partial...',
      createdAt: now,
      status: 'running',
      participant: { peerId: 'p1', persona: 'Odin' },
    };
    render(<RoomMessage message={streamMsg} />);
    expect(screen.getByTestId('streaming-message')).toBeInTheDocument();
  });

  it('renders without participant gracefully', () => {
    const noPartMsg: ChatMessage = {
      id: 'r5',
      role: 'user',
      content: 'No participant',
      createdAt: now,
    };
    render(<RoomMessage message={noPartMsg} />);
    expect(screen.getByText('No participant')).toBeInTheDocument();
    expect(screen.getByTestId('room-message')).not.toHaveAttribute('data-speaker');
  });

  describe('speaker header', () => {
    it('heads the run with the initial and name in the worker colour; the time sits under the message', () => {
      render(<RoomMessage message={assistantMsg} workerRoom />);
      const message = screen.getByTestId('room-message');
      expect(message).toHaveAttribute('data-speaker');
      expect(message.style.getPropertyValue('--niuu-persona-color')).toBe('#123456');
      expect(screen.getByText('Odin')).toBeInTheDocument();
      expect(document.querySelector('.niuu-chat-room-avatar')).toHaveAttribute('data-initial', 'O');
      expect(document.querySelector('.niuu-chat-assistant-footer')).toHaveTextContent(/5:12/);
    });

    it('names the lead the Coordinator in a room of workers', () => {
      render(<RoomMessage message={leadMsg} workerRoom />);
      expect(screen.getByText('Coordinator')).toBeInTheDocument();
      expect(screen.queryByText('Skuld')).toBeNull();
    });

    it('names an assistant message with no participant the Coordinator', () => {
      const own: ChatMessage = { id: 'r7', role: 'assistant', content: 'On it.', createdAt: now };
      render(<RoomMessage message={own} workerRoom />);
      expect(screen.getByText('Coordinator')).toBeInTheDocument();
    });

    it('keeps the persona label outside a room of workers', () => {
      render(<RoomMessage message={leadMsg} />);
      expect(screen.getByText('Skuld')).toBeInTheDocument();
      expect(screen.queryByText('Coordinator')).toBeNull();
    });

    it('leaves the header out when a run of messages shares one', () => {
      render(<RoomMessage message={assistantMsg} workerRoom showHeader={false} />);
      expect(screen.queryByText('Odin')).toBeNull();
      expect(screen.getByText('Response from agent')).toBeInTheDocument();
      expect(screen.getByTestId('room-message')).toHaveAttribute('data-speaker');
    });

    it('opens the worker when its name is clicked', () => {
      const onShowDetail = vi.fn();
      render(<RoomMessage message={assistantMsg} workerRoom onShowDetail={onShowDetail} />);
      fireEvent.click(screen.getByRole('button', { name: 'Open Odin' }));
      expect(onShowDetail).toHaveBeenCalledWith('p1');
    });

    it('shows the name as plain text without a handler', () => {
      render(<RoomMessage message={assistantMsg} workerRoom />);
      expect(screen.queryByRole('button', { name: 'Open Odin' })).toBeNull();
    });
  });

  it('gives an outcome block in the message the speaker as its worker', () => {
    const outcome: ChatMessage = {
      ...assistantMsg,
      id: 'r8',
      content: '```outcome\nverdict: pass\nsummary: Done\n```',
    };
    render(<RoomMessage message={outcome} workerRoom />);
    const card = screen.getByTestId('outcome-card');
    expect(card).toHaveTextContent('Odin');
    expect(card).toHaveTextContent('Passed');
  });

  it('does not attach a speaker to a card rendered without one', () => {
    render(<OutcomeCard raw="verdict: pass" />);
    expect(screen.getByTestId('outcome-card')).not.toHaveTextContent('Odin');
  });
});
