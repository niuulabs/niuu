import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ThreadGroup } from './ThreadGroup';
import type { ChatMessage } from '../../types';

const now = new Date();
const messages: ChatMessage[] = [
  {
    id: 'm1',
    role: 'assistant',
    content: 'First message',
    createdAt: now,
    visibility: 'internal',
    threadId: 't1',
  },
  {
    id: 'm2',
    role: 'assistant',
    content: 'Second message',
    createdAt: now,
    visibility: 'internal',
    threadId: 't1',
  },
];

describe('ThreadGroup', () => {
  it('renders thread header with count', () => {
    render(<ThreadGroup messages={messages} isCollapsed={true} onToggle={vi.fn()} />);
    expect(screen.getByTestId('thread-group')).toBeInTheDocument();
    expect(screen.getByText(/2 msgs/)).toBeInTheDocument();
  });

  it('shows messages when expanded', () => {
    render(<ThreadGroup messages={messages} isCollapsed={false} onToggle={vi.fn()} />);
    expect(screen.getByText('First message')).toBeInTheDocument();
  });

  it('calls onToggle when header clicked', () => {
    const onToggle = vi.fn();
    render(<ThreadGroup messages={messages} isCollapsed={true} onToggle={onToggle} />);
    fireEvent.click(screen.getAllByRole('button')[0]);
    expect(onToggle).toHaveBeenCalled();
  });
});
