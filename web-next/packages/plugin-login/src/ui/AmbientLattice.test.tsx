import { describe, it, expect, vi, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import { AmbientLattice } from './AmbientLattice';

function stubMatchMedia(matches: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

describe('AmbientLattice', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders an SVG element', () => {
    stubMatchMedia(false);
    const { getByTestId } = render(<AmbientLattice />);
    expect(getByTestId('ambient-lattice').tagName.toLowerCase()).toBe('svg');
  });

  it('marks the SVG aria-hidden', () => {
    stubMatchMedia(false);
    const { getByTestId } = render(<AmbientLattice />);
    expect(getByTestId('ambient-lattice').getAttribute('aria-hidden')).toBe('true');
  });

  it('applies the login-ambient CSS class', () => {
    stubMatchMedia(false);
    const { getByTestId } = render(<AmbientLattice />);
    expect(getByTestId('ambient-lattice')).toHaveClass('login-ambient');
  });

  it('renders the rune band group', () => {
    stubMatchMedia(false);
    const { getByTestId } = render(<AmbientLattice />);
    expect(getByTestId('lattice-rune-band')).toBeInTheDocument();
  });

  it('renders 12 runes in the band', () => {
    stubMatchMedia(false);
    const { getByTestId } = render(<AmbientLattice />);
    const band = getByTestId('lattice-rune-band');
    const texts = band.querySelectorAll('text');
    expect(texts.length).toBe(12);
  });

  it('renders animateTransform when motion is allowed', () => {
    stubMatchMedia(false);
    const { container } = render(<AmbientLattice />);
    expect(container.querySelector('animateTransform')).not.toBeNull();
  });

  it('omits animateTransform when prefers-reduced-motion is set', () => {
    stubMatchMedia(true);
    const { container } = render(<AmbientLattice />);
    expect(container.querySelector('animateTransform')).toBeNull();
  });

  it('has 4 concentric circles', () => {
    stubMatchMedia(false);
    const { getByTestId } = render(<AmbientLattice />);
    const mainG = getByTestId('lattice-main');
    const circles = mainG.querySelectorAll('circle[data-ring]');
    expect(circles.length).toBe(4);
  });

  it('has 12 spoke ticks', () => {
    stubMatchMedia(false);
    const { getByTestId } = render(<AmbientLattice />);
    const mainG = getByTestId('lattice-main');
    const lines = mainG.querySelectorAll('line[data-spoke]');
    expect(lines.length).toBe(12);
  });
});
