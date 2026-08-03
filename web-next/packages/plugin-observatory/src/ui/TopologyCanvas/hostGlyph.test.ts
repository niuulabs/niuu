import { describe, it, expect } from 'vitest';
import { drawGlyph } from './shapes';
import { makeCtxMock } from './test-helpers';

function draw(variant?: string) {
  const ctx = makeCtxMock();
  drawGlyph(ctx as unknown as CanvasRenderingContext2D, {
    shape: 'rack',
    x: 0,
    y: 0,
    size: 10,
    colour: [255, 255, 255],
    alpha: 1,
    now: 0,
    zoom: 1,
    variant,
  });
  return ctx;
}

/**
 * The straight segments inside the rack.
 *
 * The outline is a rounded rect built from `arcTo`, so it contributes one
 * `moveTo` and no `lineTo` — pairing the two by index picks out only the
 * interior strokes.
 */
function segments(ctx: ReturnType<typeof makeCtxMock>) {
  const calls = (fn: unknown) => (fn as { mock: { calls: number[][] } }).mock.calls;
  const tos = calls(ctx.lineTo);
  const froms = calls(ctx.moveTo).slice(-tos.length);
  return tos.map((to, i) => ({ from: froms[i]!, to }));
}

function orientations(ctx: ReturnType<typeof makeCtxMock>) {
  const kinds = segments(ctx).map(({ from, to }) =>
    from[1] === to[1] ? 'horizontal' : from[0] === to[0] ? 'vertical' : 'other',
  );
  return { kinds, count: kinds.length };
}

describe('the host glyph', () => {
  it('draws a GPU machine with vertical fins rather than shelves', () => {
    // Orientation, not count: a fin still reads as a fin when the glyph is a
    // few pixels tall, where a fourth shelf would not.
    const { kinds, count } = orientations(draw('gpu'));

    expect(count).toBeGreaterThan(1);
    expect(kinds.every((kind) => kind === 'vertical')).toBe(true);
  });

  it('draws a worker with horizontal shelves', () => {
    const { kinds, count } = orientations(draw('worker'));

    expect(count).toBeGreaterThan(1);
    expect(kinds.every((kind) => kind === 'horizontal')).toBe(true);
  });

  it('gives the control plane a solid header rail no other machine has', () => {
    const withRail = draw('control-plane');
    const plain = draw('worker');

    expect((withRail.fillRect as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBe(
      1,
    );
    expect((plain.fillRect as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBe(0);
  });

  it('dashes the shelves of a machine nothing declares a role for', () => {
    // Dashes are what the canvas already uses for something it cannot assert.
    const unknown = draw('unknown');
    const worker = draw('worker');

    expect(unknown.lineDashes.some((pattern) => pattern.length > 0)).toBe(true);
    expect(worker.lineDashes.some((pattern) => pattern.length > 0)).toBe(false);
  });

  it('leaves the silhouette alone, so a machine still reads as a machine', () => {
    // The outline is four arcs whatever the machine turns out to be.
    for (const variant of ['gpu', 'control-plane', 'worker', 'unknown']) {
      const ctx = draw(variant);
      expect((ctx.arcTo as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBe(4);
    }
  });
});
