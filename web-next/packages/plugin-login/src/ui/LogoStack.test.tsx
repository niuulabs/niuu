import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { LogoStack } from './LogoStack';

describe('LogoStack', () => {
  it('renders an SVG with default dimensions', () => {
    const { container } = render(<LogoStack />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('56');
    expect(svg?.getAttribute('height')).toBe('56');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 56 56');
  });

  it('applies a custom size', () => {
    const { container } = render(<LogoStack size={48} />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('48');
    expect(svg?.getAttribute('height')).toBe('48');
  });

  it('does not apply a filter when glow is false', () => {
    const { container } = render(<LogoStack glow={false} />);
    expect(container.querySelector('svg')?.getAttribute('style') ?? '').not.toContain(
      'drop-shadow',
    );
  });

  it('applies a drop-shadow filter when glow is true', () => {
    const { container } = render(<LogoStack glow />);
    expect(container.querySelector('svg')?.getAttribute('style')).toContain('drop-shadow');
  });

  it('is marked as aria-hidden', () => {
    const { container } = render(<LogoStack />);
    expect(container.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('renders 6 paths (3 staves + 3 cross-strokes)', () => {
    const { container } = render(<LogoStack />);
    const paths = container.querySelectorAll('path');
    expect(paths.length).toBe(6);
  });
});
