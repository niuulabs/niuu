import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { LogoStars } from './LogoStars';

describe('LogoStars', () => {
  it('renders an SVG with default dimensions', () => {
    const { container } = render(<LogoStars />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('56');
    expect(svg?.getAttribute('height')).toBe('56');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 56 56');
  });

  it('applies a custom size', () => {
    const { container } = render(<LogoStars size={100} />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('100');
    expect(svg?.getAttribute('height')).toBe('100');
  });

  it('does not apply a filter when glow is false', () => {
    const { container } = render(<LogoStars glow={false} />);
    expect(container.querySelector('svg')?.getAttribute('style') ?? '').not.toContain(
      'drop-shadow',
    );
  });

  it('applies a drop-shadow filter when glow is true', () => {
    const { container } = render(<LogoStars glow />);
    expect(container.querySelector('svg')?.getAttribute('style')).toContain('drop-shadow');
  });

  it('is marked as aria-hidden', () => {
    const { container } = render(<LogoStars />);
    expect(container.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('renders 13 dot circles', () => {
    const { container } = render(<LogoStars />);
    const circles = container.querySelectorAll('circle');
    expect(circles.length).toBe(13);
  });

  it('renders letter paths', () => {
    const { container } = render(<LogoStars />);
    const paths = container.querySelectorAll('path');
    expect(paths.length).toBeGreaterThan(0);
  });
});
