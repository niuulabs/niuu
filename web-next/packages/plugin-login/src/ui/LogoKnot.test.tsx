import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { LogoKnot } from './LogoKnot';

describe('LogoKnot', () => {
  it('renders an SVG with default dimensions', () => {
    const { container } = render(<LogoKnot />);
    const svg = container.querySelector('svg');
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute('width')).toBe('56');
    expect(svg?.getAttribute('height')).toBe('56');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 56 56');
  });

  it('applies a custom size', () => {
    const { container } = render(<LogoKnot size={72} />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('72');
    expect(svg?.getAttribute('height')).toBe('72');
  });

  it('does not apply a filter when glow is false', () => {
    const { container } = render(<LogoKnot glow={false} />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('style') ?? '').not.toContain('drop-shadow');
  });

  it('applies a drop-shadow filter when glow is true', () => {
    const { container } = render(<LogoKnot glow />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('style')).toContain('drop-shadow');
  });

  it('uses a custom stroke width', () => {
    const { container } = render(<LogoKnot stroke={2.5} />);
    const g = container.querySelector('g');
    expect(g?.getAttribute('stroke-width')).toBe('2.5');
  });

  it('is marked as aria-hidden', () => {
    const { container } = render(<LogoKnot />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('aria-hidden')).toBe('true');
  });
});
