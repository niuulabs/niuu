import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { LogoTree } from './LogoTree';

describe('LogoTree', () => {
  it('renders an SVG with default dimensions', () => {
    const { container } = render(<LogoTree />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('56');
    expect(svg?.getAttribute('height')).toBe('56');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 56 56');
  });

  it('applies a custom size', () => {
    const { container } = render(<LogoTree size={80} />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('80');
    expect(svg?.getAttribute('height')).toBe('80');
  });

  it('does not apply a filter when glow is false', () => {
    const { container } = render(<LogoTree glow={false} />);
    expect(container.querySelector('svg')?.getAttribute('style') ?? '').not.toContain(
      'drop-shadow',
    );
  });

  it('applies a drop-shadow filter when glow is true', () => {
    const { container } = render(<LogoTree glow />);
    expect(container.querySelector('svg')?.getAttribute('style')).toContain('drop-shadow');
  });

  it('is marked as aria-hidden', () => {
    const { container } = render(<LogoTree />);
    expect(container.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('renders the trunk path', () => {
    const { container } = render(<LogoTree />);
    const paths = container.querySelectorAll('path');
    expect(paths.length).toBeGreaterThan(0);
  });

  it('renders node circles', () => {
    const { container } = render(<LogoTree />);
    const circles = container.querySelectorAll('circle');
    expect(circles.length).toBeGreaterThanOrEqual(4);
  });
});
