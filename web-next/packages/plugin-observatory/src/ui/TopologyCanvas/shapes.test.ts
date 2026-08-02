import { describe, it, expect, vi } from 'vitest';
import { drawGlyph, glyphRadius, isKnownShape, polygonPath, roundRectPath } from './shapes';
import { entityShapeSchema } from '@niuulabs/domain';
import { makeCtxMock } from './test-helpers';

type Mock = ReturnType<typeof vi.fn>;

const BASE = {
  x: 100,
  y: 100,
  size: 12,
  colour: [56, 189, 248] as const,
  alpha: 1,
  now: 1000,
  zoom: 1,
};

function draw(shape: string, overrides: Record<string, unknown> = {}) {
  const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
  drawGlyph(ctx, { ...BASE, shape, ...overrides });
  return ctx;
}

describe('shape vocabulary', () => {
  it('draws every shape the domain schema allows', () => {
    // The schema is the contract between the registry and the canvas. A name
    // it permits but the canvas cannot draw is a silent fallback in
    // production, so the two have to stay in step.
    for (const shape of entityShapeSchema.options) {
      expect(isKnownShape(shape)).toBe(true);
    }
  });

  it('does not claim to know a shape it cannot draw', () => {
    expect(isKnownShape('spiral')).toBe(false);
  });

  it('has no synonyms — one name per mark', () => {
    // Aliases are how a vocabulary rots: two names for one glyph and nobody
    // can say which is current.
    expect(isKnownShape('diamond')).toBe(false);
    expect(isKnownShape('chevron')).toBe(false);
    expect(isKnownShape('rounded-rect')).toBe(false);
    expect(isKnownShape('dot')).toBe(false);
    expect(isKnownShape('square')).toBe(false);
  });

  it('falls back to the boxed dot rather than drawing nothing', () => {
    // A registry that has drifted must never be able to blank the canvas.
    const unknown = draw('spiral');
    const fallback = draw('box');
    expect((unknown.arcTo as Mock).mock.calls.length).toBe(
      (fallback.arcTo as Mock).mock.calls.length,
    );
  });
});

describe('individual glyphs', () => {
  it('draws a triangle as three straight sides', () => {
    const ctx = draw('triangle');
    expect((ctx.moveTo as Mock).mock.calls.length).toBe(1);
    expect((ctx.lineTo as Mock).mock.calls.length).toBe(2);
    expect((ctx.closePath as Mock).mock.calls.length).toBe(1);
  });

  it('draws a pentagon as five', () => {
    const ctx = draw('pentagon');
    expect((ctx.lineTo as Mock).mock.calls.length).toBe(4);
  });

  it('turns the resident orbit unless motion is reduced', () => {
    expect((draw('agent').rotate as Mock).mock.calls.length).toBe(1);
    expect((draw('agent', { reducedMotion: true }).rotate as Mock).mock.calls.length).toBe(0);
  });

  it('lights a pip on a resident that is waiting on someone', () => {
    const waiting = draw('agent', { state: 'waiting' });
    const working = draw('agent', { state: 'healthy' });
    expect((waiting.arc as Mock).mock.calls.length).toBeGreaterThan(
      (working.arc as Mock).mock.calls.length,
    );
  });

  it('draws three shelves on a rack', () => {
    const ctx = draw('rack');
    // One rounded face plus three shelf lines.
    expect((ctx.lineTo as Mock).mock.calls.length).toBe(3);
    expect((ctx.arcTo as Mock).mock.calls.length).toBe(4);
  });

  it('rings a model with its utilisation only when there is some', () => {
    const idle = draw('hex-flat', { progress: 0 });
    const busy = draw('hex-flat', { progress: 0.8 });
    expect((idle.arc as Mock).mock.calls.length).toBe(0);
    expect((busy.arc as Mock).mock.calls.length).toBe(1);
  });

  it('pings a beacon unless motion is reduced', () => {
    expect((draw('beacon').arc as Mock).mock.calls.length).toBe(2);
    expect((draw('beacon', { reducedMotion: true }).arc as Mock).mock.calls.length).toBe(1);
  });

  it('gives a cylinder its elliptical top', () => {
    expect((draw('cylinder').ellipse as Mock).mock.calls.length).toBe(1);
  });

  it('spins the halo of a live session', () => {
    const ctx = draw('halo');
    expect((ctx.rotate as Mock).mock.calls.length).toBe(1);
    expect((ctx.arc as Mock).mock.calls.length).toBe(5); // 3 arcs + core + inner dot
  });

  it('leaves no dash set behind for the next glyph', () => {
    const ctx = draw('ring');
    const last = (ctx.setLineDash as Mock).mock.calls.at(-1);
    expect(last?.[0]).toEqual([]);
  });
});

describe('stroke weight', () => {
  it('scales strokes so an outline holds its screen weight as the camera pulls back', () => {
    const near = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const widths: number[] = [];
    Object.defineProperty(near, 'lineWidth', {
      set(value: number) {
        widths.push(value);
      },
      get() {
        return 1;
      },
    });
    drawGlyph(near, { ...BASE, shape: 'triangle', zoom: 0.25 });
    // 1.6px at quarter zoom has to be drawn as 6.4 world units.
    expect(widths).toContain(6.4);
  });

  it('does not divide by a zero or nonsense zoom', () => {
    expect(() => draw('triangle', { zoom: 0 })).not.toThrow();
    expect(() => draw('triangle', { zoom: Number.NaN })).not.toThrow();
  });
});

describe('glyphRadius', () => {
  it('reaches past the glyph for marks that carry an orbit or halo', () => {
    expect(glyphRadius('agent', 12)).toBeGreaterThan(12);
    expect(glyphRadius('halo', 12)).toBeGreaterThan(glyphRadius('agent', 12));
  });

  it('accounts for a rack being wider than it is tall', () => {
    expect(glyphRadius('rack', 12)).toBe(15);
  });

  it('falls back to the nominal size for a mark with no reach', () => {
    expect(glyphRadius('triangle', 12)).toBe(12);
    expect(glyphRadius('spiral', 12)).toBe(12);
  });
});

describe('path helpers', () => {
  it('clamps a corner radius to what the box can hold', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    roundRectPath(ctx, 0, 0, 10, 4, 99);
    const firstMove = (ctx.moveTo as Mock).mock.calls[0];
    expect(firstMove).toEqual([2, 0]);
  });

  it('closes a polygon of any side count', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    polygonPath(ctx, 0, 0, 10, 8, 0);
    expect((ctx.lineTo as Mock).mock.calls.length).toBe(7);
    expect(ctx.closePath).toHaveBeenCalled();
  });
});
