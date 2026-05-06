import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { LogoFlokk } from './LogoFlokk';

describe('LogoFlokk', () => {
  it('renders an SVG with default dimensions', () => {
    const { container } = render(<LogoFlokk />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('56');
    expect(svg?.getAttribute('height')).toBe('56');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 56 56');
  });

  it('applies a custom size', () => {
    const { container } = render(<LogoFlokk size={64} />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('64');
    expect(svg?.getAttribute('height')).toBe('64');
  });

  it('does not apply a filter when glow is false', () => {
    const { container } = render(<LogoFlokk glow={false} />);
    expect(container.querySelector('svg')?.getAttribute('style') ?? '').not.toContain(
      'drop-shadow',
    );
  });

  it('applies a drop-shadow filter when glow is true', () => {
    const { container } = render(<LogoFlokk glow />);
    expect(container.querySelector('svg')?.getAttribute('style')).toContain('drop-shadow');
  });

  it('is marked as aria-hidden', () => {
    const { container } = render(<LogoFlokk />);
    expect(container.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('renders two hexagon polygons', () => {
    const { container } = render(<LogoFlokk />);
    const polygons = container.querySelectorAll('polygon');
    expect(polygons.length).toBe(2);
  });

  it('renders a center dot circle', () => {
    const { container } = render(<LogoFlokk />);
    const circles = container.querySelectorAll('circle');
    expect(circles.length).toBe(1);
  });
});
