import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import { AmbientTopology } from './AmbientTopology';

const mockCtx = {
  clearRect: vi.fn(),
  beginPath: vi.fn(),
  moveTo: vi.fn(),
  lineTo: vi.fn(),
  arc: vi.fn(),
  fill: vi.fn(),
  stroke: vi.fn(),
  setTransform: vi.fn(),
  strokeStyle: '',
  lineWidth: 0,
  fillStyle: '',
};

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

describe('AmbientTopology', () => {
  beforeEach(() => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      mockCtx as unknown as CanvasRenderingContext2D,
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders a canvas element', () => {
    stubMatchMedia(false);
    const { getByTestId } = render(<AmbientTopology />);
    const canvas = getByTestId('ambient-topology');
    expect(canvas.tagName.toLowerCase()).toBe('canvas');
  });

  it('marks the canvas aria-hidden', () => {
    stubMatchMedia(false);
    const { getByTestId } = render(<AmbientTopology />);
    expect(getByTestId('ambient-topology').getAttribute('aria-hidden')).toBe('true');
  });

  it('applies the login-ambient CSS class', () => {
    stubMatchMedia(false);
    const { getByTestId } = render(<AmbientTopology />);
    expect(getByTestId('ambient-topology')).toHaveClass('login-ambient');
  });

  it('does not start requestAnimationFrame when prefers-reduced-motion is set', () => {
    const rafSpy = vi.spyOn(window, 'requestAnimationFrame');
    stubMatchMedia(true);
    render(<AmbientTopology />);
    expect(rafSpy).not.toHaveBeenCalled();
  });

  it('starts requestAnimationFrame when motion is allowed', () => {
    const rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 0);
    stubMatchMedia(false);
    render(<AmbientTopology />);
    expect(rafSpy).toHaveBeenCalled();
  });

  it('cancels requestAnimationFrame on unmount', () => {
    const cancelSpy = vi.spyOn(window, 'cancelAnimationFrame');
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 42);
    stubMatchMedia(false);
    const { unmount } = render(<AmbientTopology />);
    unmount();
    expect(cancelSpy).toHaveBeenCalled();
  });
});
