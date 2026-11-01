import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { LogoRuneRing } from './LogoRuneRing';

describe('LogoRuneRing', () => {
  it('renders an SVG with default dimensions', () => {
    const { container } = render(<LogoRuneRing />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('56');
    expect(svg?.getAttribute('height')).toBe('56');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 56 56');
  });

  it('applies a custom size', () => {
    const { container } = render(<LogoRuneRing size={72} />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('72');
    expect(svg?.getAttribute('height')).toBe('72');
  });

  it('does not apply a filter when glow is false', () => {
    const { container } = render(<LogoRuneRing glow={false} />);
    expect(container.querySelector('svg')?.getAttribute('style') ?? '').not.toContain(
      'drop-shadow',
    );
  });

  it('applies a drop-shadow filter when glow is true', () => {
    const { container } = render(<LogoRuneRing glow />);
    expect(container.querySelector('svg')?.getAttribute('style')).toContain('drop-shadow');
  });

  it('is marked as aria-hidden', () => {
    const { container } = render(<LogoRuneRing />);
    expect(container.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('renders 8 orbit runes plus the central rune', () => {
    const { container } = render(<LogoRuneRing />);
    const texts = container.querySelectorAll('text');
    // 8 orbit runes + 1 central ᚾ = 9
    expect(texts.length).toBe(9);
  });

  it('renders the central ᚾ rune', () => {
    const { container } = render(<LogoRuneRing />);
    const texts = Array.from(container.querySelectorAll('text'));
    const central = texts.find((t) => t.textContent === 'ᚾ');
    expect(central).toBeDefined();
  });

  it('renders two circles (orbit ring + inner ring)', () => {
    const { container } = render(<LogoRuneRing />);
    const circles = container.querySelectorAll('circle');
    expect(circles.length).toBe(2);
  });
});
