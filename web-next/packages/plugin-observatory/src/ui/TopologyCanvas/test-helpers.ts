import { vi } from 'vitest';

/**
 * Shared canvas 2D context stub. jsdom does not implement
 * CanvasRenderingContext2D.
 *
 * Spied methods are typed loosely on purpose: the drawing code calls them
 * through the real `CanvasRenderingContext2D` type, and tests only ever read
 * `.mock.calls` off them.
 */
export interface CtxMock extends Record<string, unknown> {
  strokeStyles: string[];
  fillStyles: string[];
  strokeStyle: string;
  fillStyle: string;
}

export function makeCtxMock(): CtxMock {
  const gradient = { addColorStop: vi.fn() };
  const ctx = {
    clearRect: vi.fn(),
    fillRect: vi.fn(),
    strokeRect: vi.fn(),
    beginPath: vi.fn(),
    closePath: vi.fn(),
    roundRect: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    bezierCurveTo: vi.fn(),
    quadraticCurveTo: vi.fn(),
    arc: vi.fn(),
    arcTo: vi.fn(),
    ellipse: vi.fn(),
    rotate: vi.fn(),
    fill: vi.fn(),
    stroke: vi.fn(),
    fillText: vi.fn(),
    strokeText: vi.fn(),
    measureText: vi.fn((text: string) => ({ width: text.length * 7 })),
    save: vi.fn(),
    restore: vi.fn(),
    translate: vi.fn(),
    scale: vi.fn(),
    setTransform: vi.fn(),
    setLineDash: vi.fn(),
    createRadialGradient: vi.fn().mockReturnValue(gradient),
    createLinearGradient: vi.fn().mockReturnValue(gradient),
    // Every colour the caller assigns, in order. A canvas mock that only keeps
    // the last value cannot answer "was this drawn in amber?" for a frame that
    // paints many things — which is exactly what mesh highlighting needs.
    strokeStyles: [] as string[],
    fillStyles: [] as string[],
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 1,
    font: '',
    textAlign: 'left' as CanvasTextAlign,
    textBaseline: 'alphabetic' as CanvasTextBaseline,
    lineCap: 'butt' as CanvasLineCap,
    lineDashOffset: 0,
  };
  for (const prop of ['strokeStyle', 'fillStyle'] as const) {
    const log = ctx[`${prop}s`];
    let current = '';
    Object.defineProperty(ctx, prop, {
      configurable: true,
      get: () => current,
      set: (value: string) => {
        current = value;
        log.push(value);
      },
    });
  }
  return ctx;
}
