import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Rune } from './Rune';

describe('Rune', () => {
  it('renders the glyph', () => {
    render(<Rune glyph="ᚠ" title="fehu" />);
    const el = screen.getByText('ᚠ');
    expect(el).toHaveClass('niuu-rune');
    expect(el).toHaveAttribute('title', 'fehu');
  });

  it('applies size via inline style', () => {
    render(<Rune glyph="ᚢ" size={32} />);
    const el = screen.getByText('ᚢ');
    expect(el.getAttribute('style') ?? '').toContain('32px');
  });

  it('applies muted modifier', () => {
    render(<Rune glyph="ᛗ" muted />);
    expect(screen.getByText('ᛗ')).toHaveClass('niuu-rune--muted');
  });
});
