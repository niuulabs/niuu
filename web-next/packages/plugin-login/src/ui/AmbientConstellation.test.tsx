import { describe, it, expect, vi, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import { AmbientConstellation } from './AmbientConstellation';

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

describe('AmbientConstellation', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders an SVG element', () => {
    stubMatchMedia(false);
    const { getByTestId } = render(<AmbientConstellation />);
    expect(getByTestId('ambient-constellation').tagName.toLowerCase()).toBe('svg');
  });

  it('marks the SVG aria-hidden', () => {
    stubMatchMedia(false);
    const { getByTestId } = render(<AmbientConstellation />);
    expect(getByTestId('ambient-constellation').getAttribute('aria-hidden')).toBe('true');
  });

  it('applies the login-ambient CSS class', () => {
    stubMatchMedia(false);
    const { getByTestId } = render(<AmbientConstellation />);
    expect(getByTestId('ambient-constellation')).toHaveClass('login-ambient');
  });

  it('renders 80 star circles', () => {
    stubMatchMedia(false);
    const { container } = render(<AmbientConstellation />);
    // 80 star circles + the gradient background rect circle count
    const circles = container.querySelectorAll('circle');
    expect(circles.length).toBe(80);
  });

  it('renders animate elements when motion is allowed', () => {
    stubMatchMedia(false);
    const { container } = render(<AmbientConstellation />);
    expect(container.querySelectorAll('animate').length).toBe(80);
  });

  it('omits animate elements when prefers-reduced-motion is set', () => {
    stubMatchMedia(true);
    const { container } = render(<AmbientConstellation />);
    expect(container.querySelectorAll('animate').length).toBe(0);
  });

  it('sets static opacity on stars when reduced-motion is set', () => {
    stubMatchMedia(true);
    const { container } = render(<AmbientConstellation />);
    const circles = container.querySelectorAll('circle');
    circles.forEach((c) => {
      expect(c.getAttribute('opacity')).toBe('0.5');
    });
  });

  it('has a radial gradient definition', () => {
    stubMatchMedia(false);
    const { container } = render(<AmbientConstellation />);
    expect(container.querySelector('radialGradient')).not.toBeNull();
  });
});
