import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { Sparkline } from './Sparkline';

describe('Sparkline', () => {
  it('renders an svg element', () => {
    const { container } = render(<Sparkline />);
    expect(container.querySelector('svg')).toBeTruthy();
  });

  it('uses default dimensions', () => {
    const { container } = render(<Sparkline />);
    const svg = container.querySelector('svg')!;
    expect(svg.getAttribute('width')).toBe('120');
    expect(svg.getAttribute('height')).toBe('28');
  });

  it('accepts custom dimensions', () => {
    const { container } = render(<Sparkline width={200} height={40} />);
    const svg = container.querySelector('svg')!;
    expect(svg.getAttribute('width')).toBe('200');
    expect(svg.getAttribute('height')).toBe('40');
  });

  it('is aria-hidden (presentational)', () => {
    const { container } = render(<Sparkline />);
    expect(container.querySelector('svg')!.getAttribute('aria-hidden')).toBe('true');
  });

  it('renders a line path when values provided', () => {
    const { container } = render(<Sparkline values={[0, 0.5, 1, 0.5]} />);
    const paths = container.querySelectorAll('path');
    // fill area + line = 2 paths
    expect(paths.length).toBeGreaterThanOrEqual(1);
  });

  it('renders area fill by default', () => {
    const { container } = render(<Sparkline values={[0.2, 0.8, 0.4]} />);
    const paths = container.querySelectorAll('path');
    // With fill=true there should be 2 paths (area + line)
    expect(paths.length).toBe(2);
  });

  it('suppresses fill when fill=false', () => {
    const { container } = render(<Sparkline values={[0.2, 0.8, 0.4]} fill={false} />);
    const paths = container.querySelectorAll('path');
    // Only the line path
    expect(paths.length).toBe(1);
  });

  it('renders an empty svg for empty values array', () => {
    const { container } = render(<Sparkline values={[]} />);
    const svg = container.querySelector('svg')!;
    // No paths rendered for empty data
    expect(svg.querySelectorAll('path').length).toBe(0);
    expect(svg.querySelectorAll('circle').length).toBe(0);
  });

  it('renders a dot for a single value', () => {
    const { container } = render(<Sparkline values={[0.5]} />);
    expect(container.querySelector('circle')).toBeTruthy();
    expect(container.querySelectorAll('path').length).toBe(0);
  });

  it('is deterministic for the same id', () => {
    const { container: a } = render(<Sparkline id="test-seed" />);
    const { container: b } = render(<Sparkline id="test-seed" />);
    const svgA = a.querySelector('svg')!.innerHTML;
    const svgB = b.querySelector('svg')!.innerHTML;
    expect(svgA).toBe(svgB);
  });

  it('produces different output for different ids', () => {
    const { container: a } = render(<Sparkline id="seed-alpha" />);
    const { container: b } = render(<Sparkline id="seed-beta" />);
    const svgA = a.querySelector('svg')!.innerHTML;
    const svgB = b.querySelector('svg')!.innerHTML;
    expect(svgA).not.toBe(svgB);
  });

  it('forwards className', () => {
    const { container } = render(<Sparkline className="extra" />);
    expect(container.querySelector('svg')!.classList.contains('extra')).toBe(true);
  });

  it('generates 24 samples for deterministic fallback', () => {
    // Verify that path data changes with different sample counts by checking
    // path d attribute contains expected number of coordinates
    const { container } = render(<Sparkline id="x" />);
    const linePath = container.querySelectorAll('path')[1]!;
    // 24 points → "M..." + 23 "L..." segments = 24 coordinate pairs
    const segments = linePath
      .getAttribute('d')!
      .split(/(?=[ML])/)
      .filter(Boolean);
    expect(segments.length).toBe(24);
  });
});
