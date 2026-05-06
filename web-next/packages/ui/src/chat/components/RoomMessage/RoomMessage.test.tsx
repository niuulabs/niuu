import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RoomMessage } from './RoomMessage';
import type { ChatMessage } from '../../types';

const now = new Date();

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
  participant: { peerId: 'p1', persona: 'Odin' },
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

  it('calls onSelectAgent when persona button clicked', () => {
    const onSelectAgent = vi.fn();
    render(<RoomMessage message={userMsg} onSelectAgent={onSelectAgent} />);
    fireEvent.click(screen.getByText('Odin'));
    expect(onSelectAgent).toHaveBeenCalledWith('p1');
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

  it('shows detail button when onShowDetail provided', () => {
    const onShowDetail = vi.fn();
    render(<RoomMessage message={userMsg} onShowDetail={onShowDetail} />);
    const detailBtn = screen.getByLabelText('View event stream for Odin');
    fireEvent.click(detailBtn);
    expect(onShowDetail).toHaveBeenCalledWith('p1');
  });

  it('marks persona button selected when selectedAgentId matches', () => {
    render(<RoomMessage message={userMsg} selectedAgentId="p1" />);
    const btn = screen.getByText('Odin').closest('button');
    expect(btn).toHaveClass('niuu-chat-room-persona-btn--selected');
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
  });
});
