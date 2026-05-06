import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { FooterChip, FooterChipSep } from './FooterChip';

describe('FooterChip', () => {
  it('renders name, dot, and value', () => {
    render(<FooterChip name="api" state="ok" value="connected" />);
    const chip = screen.getByTestId('footer-chip-api');
    expect(chip).toBeInTheDocument();
    expect(chip.textContent).toContain('api');
    expect(chip.textContent).toContain('connected');
  });

  it('sets data-state on the dot element', () => {
    const { container } = render(<FooterChip name="db" state="warn" value="slow" />);
    const dot = container.querySelector('.niuu-shell__footer-chip-dot');
    expect(dot?.getAttribute('data-state')).toBe('warn');
  });

  it('does not render a trailing separator', () => {
    const { container } = render(<FooterChip name="x" state="ok" value="y" />);
    expect(container.querySelector('.niuu-shell__footer-chip-sep')).not.toBeInTheDocument();
  });
});

describe('FooterChipSep', () => {
  it('renders separator character', () => {
    const { container } = render(<FooterChipSep />);
    const sep = container.querySelector('.niuu-shell__footer-chip-sep');
    expect(sep).toBeInTheDocument();
    expect(sep?.textContent).toBe('│');
  });
});
