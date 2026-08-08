import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createGlowTexture,
  createLabelTexture,
  createMoteTexture,
  createRingTexture,
} from './textures';
import { installCanvas2DMock, installNoCanvasMock } from './test-helpers';

const originalGetContext = HTMLCanvasElement.prototype.getContext;

afterEach(() => {
  HTMLCanvasElement.prototype.getContext = originalGetContext;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('with a usable 2D context', () => {
  beforeEach(installCanvas2DMock);

  it('builds the sprite textures', () => {
    expect(createGlowTexture()).not.toBeNull();
    expect(createRingTexture()).not.toBeNull();
    expect(createMoteTexture()).not.toBeNull();
  });

  it('builds a label sized to its text', () => {
    const short = createLabelTexture('a');
    const long = createLabelTexture('a much longer label');
    expect(short).not.toBeNull();
    expect(long!.aspect).toBeGreaterThan(short!.aspect);
  });

  it('declines to rasterise nothing', () => {
    expect(createLabelTexture('')).toBeNull();
  });

  it('falls back to an estimate when the context measures nothing', () => {
    // Some contexts answer zero for every measurement. A label sized from that
    // is a one-pixel sprite — invisible, and indistinguishable from a bug.
    HTMLCanvasElement.prototype.getContext = vi.fn().mockImplementation((kind: string) => {
      if (kind !== '2d') return null;
      const base = originalGetContext;
      void base;
      return {
        measureText: () => ({ width: 0 }),
        fillRect: () => {},
        fillText: () => {},
        strokeText: () => {},
        font: '',
        fillStyle: '',
        strokeStyle: '',
        lineWidth: 0,
        lineJoin: 'round',
        textAlign: 'start',
        textBaseline: 'alphabetic',
      } as unknown as CanvasRenderingContext2D;
    });

    const built = createLabelTexture('mimir-shared');
    expect(built).not.toBeNull();
    expect(built!.aspect).toBeGreaterThan(1);
  });
});

describe('without a usable 2D context', () => {
  it('returns null rather than throwing, so the scene still builds', () => {
    installNoCanvasMock();
    expect(createGlowTexture()).toBeNull();
    expect(createRingTexture()).toBeNull();
    expect(createMoteTexture()).toBeNull();
    expect(createLabelTexture('anything')).toBeNull();
  });

  it('returns null when the context is a stub missing the text API', () => {
    HTMLCanvasElement.prototype.getContext = vi
      .fn()
      .mockReturnValue({ fillRect: () => {} } as unknown as CanvasRenderingContext2D);
    expect(createLabelTexture('anything')).toBeNull();
  });

  it('returns null where there is no document at all', () => {
    vi.stubGlobal('document', undefined);
    expect(createGlowTexture()).toBeNull();
    expect(createLabelTexture('anything')).toBeNull();
  });
});
