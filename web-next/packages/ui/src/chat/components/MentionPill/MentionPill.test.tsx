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
