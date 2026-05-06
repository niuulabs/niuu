import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PageTypeGlyph } from './PageTypeGlyph';

describe('PageTypeGlyph', () => {
  it('renders the rune glyph for each page type', () => {
    const types = ['entity', 'topic', 'directive', 'preference', 'decision'] as const;
    for (const type of types) {
      const { unmount } = render(<PageTypeGlyph type={type} />);
      // aria-label matches the type
      expect(screen.getByLabelText(type)).toBeInTheDocument();
      unmount();
    }
  });

  it('shows the label when showLabel=true', () => {
    render(<PageTypeGlyph type="topic" showLabel />);
    expect(screen.getByText('topic')).toBeInTheDocument();
  });

  it('does not show the label by default', () => {
    render(<PageTypeGlyph type="topic" />);
    // The label text "topic" should not be in a label span
    // (the aria-label is on the container, glyph text is aria-hidden)
    const container = screen.getByLabelText('topic');
    expect(container.querySelector('.mm-page-type-glyph__label')).toBeNull();
  });

  it('applies data-type attribute for CSS styling', () => {
    render(<PageTypeGlyph type="directive" />);
    const el = screen.getByLabelText('directive');
    expect(el).toHaveAttribute('data-type', 'directive');
  });
});
