import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MentionPill } from './MentionPill';

describe('MentionPill', () => {
  const agentMention = {
    kind: 'agent' as const,
    participant: { peerId: 'peer-1', persona: 'Odin', color: '#38bdf8' },
  };

  const fileMention = {
    kind: 'file' as const,
    entry: { name: 'index.ts', path: '/src/index.ts', type: 'file' as const },
  };

  const eventMention = {
    kind: 'agent' as const,
    participant: { peerId: 'peer-2', persona: 'reviewer', displayName: 'Hermes reviewer' },
    eventType: 'review.requested',
  };

  it('renders agent pill with persona name', () => {
    render(<MentionPill mention={agentMention} onRemove={vi.fn()} />);
    expect(screen.getByTestId('mention-pill-agent')).toBeInTheDocument();
    expect(screen.getByText('Odin')).toBeInTheDocument();
  });

  it('calls onRemove with peerId for agent', () => {
    const onRemove = vi.fn();
    render(<MentionPill mention={agentMention} onRemove={onRemove} />);
    fireEvent.click(screen.getByRole('button'));
    expect(onRemove).toHaveBeenCalledWith('peer-1');
  });

  it('renders an event pill with event and subscriber labels', () => {
    const onRemove = vi.fn();
    render(<MentionPill mention={eventMention} onRemove={onRemove} />);

    expect(screen.getByTestId('mention-pill-event')).toHaveTextContent(
      'review.requested · Hermes reviewer',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Remove event review.requested' }));
    expect(onRemove).toHaveBeenCalledWith('peer-2:review.requested');
  });

  it('renders file pill with path', () => {
    render(<MentionPill mention={fileMention} onRemove={vi.fn()} />);
    expect(screen.getByTestId('mention-pill-file')).toBeInTheDocument();
    expect(screen.getByText('/src/index.ts')).toBeInTheDocument();
  });

  it('calls onRemove with path for file', () => {
    const onRemove = vi.fn();
    render(<MentionPill mention={fileMention} onRemove={onRemove} />);
    fireEvent.click(screen.getByRole('button'));
    expect(onRemove).toHaveBeenCalledWith('/src/index.ts');
  });
});
