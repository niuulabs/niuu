import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CliBadge } from './CliBadge';

describe('CliBadge', () => {
  it('renders rune and label', () => {
    render(<CliBadge cli="claude" />);
    expect(screen.getByText('ᛗ')).toBeInTheDocument();
    expect(screen.getByText('Claude Code')).toBeInTheDocument();
  });

  it('renders compact (rune only)', () => {
    render(<CliBadge cli="claude" compact />);
    expect(screen.getByText('ᛗ')).toBeInTheDocument();
    expect(screen.queryByText('Claude Code')).not.toBeInTheDocument();
  });

  it('renders all known CLIs', () => {
    const { rerender } = render(<CliBadge cli="codex" />);
    expect(screen.getByText('Codex')).toBeInTheDocument();

    rerender(<CliBadge cli="gemini" />);
    expect(screen.getByText('Gemini')).toBeInTheDocument();

    rerender(<CliBadge cli="aider" />);
    expect(screen.getByText('Aider')).toBeInTheDocument();
  });

  it('returns null for unknown CLI', () => {
    const { container } = render(<CliBadge cli="unknown" />);
    expect(container.firstChild).toBeNull();
  });
});
