import { vi } from 'vitest';

/**
 * A 2D context complete enough for the texture factories to run.
 *
 * The workspace-wide canvas stub deliberately implements only the handful of
 * calls the plan's renderer makes, which means every texture in this view comes
 * back null under it — and a suite that only ever exercises the no-canvas path
 * proves nothing about the path that actually ships. This one answers
 * `measureText` and hands back real image data, so glows, rings and labels are
 * built for real.
 */
export function installCanvas2DMock(): void {
  const context = () =>
    ({
      canvas: { width: 0, height: 0 },
      measureText: (text: string) => ({ width: text.length * 10 }),
      createImageData: (width: number, height: number) => ({
        width,
        height,
        data: new Uint8ClampedArray(width * height * 4),
      }),
      putImageData: () => {},
      createRadialGradient: () => ({ addColorStop: () => {} }),
      createLinearGradient: () => ({ addColorStop: () => {} }),
      fillRect: () => {},
      strokeRect: () => {},
      clearRect: () => {},
      beginPath: () => {},
      arc: () => {},
      stroke: () => {},
      fill: () => {},
      fillText: () => {},
      strokeText: () => {},
      save: () => {},
      restore: () => {},
      translate: () => {},
      scale: () => {},
      setTransform: () => {},
      setLineDash: () => {},
      moveTo: () => {},
      lineTo: () => {},
      font: '',
      fillStyle: '',
      strokeStyle: '',
      lineWidth: 0,
      lineJoin: 'round' as CanvasLineJoin,
      textAlign: 'start' as CanvasTextAlign,
      textBaseline: 'alphabetic' as CanvasTextBaseline,
    }) as unknown as CanvasRenderingContext2D;

  HTMLCanvasElement.prototype.getContext = vi
    .fn()
    .mockImplementation((kind: string) => (kind === '2d' ? context() : null));
}

/** Strip the 2D context entirely, as a headless or SSR environment would. */
export function installNoCanvasMock(): void {
  HTMLCanvasElement.prototype.getContext = vi.fn().mockReturnValue(null);
}
