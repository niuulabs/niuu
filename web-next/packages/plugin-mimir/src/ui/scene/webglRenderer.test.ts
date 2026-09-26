import { describe, it, expect, vi, afterEach } from 'vitest';
import { supportsWebGL } from './webglRenderer';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('supportsWebGL', () => {
  it('returns true when the canvas can produce a webgl2 context', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((kind: string) =>
      kind === 'webgl2' ? ({} as unknown as WebGL2RenderingContext) : null,
    );
    expect(supportsWebGL()).toBe(true);
  });

  it('falls back to a plain webgl context when webgl2 is unavailable', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((kind: string) =>
      kind === 'webgl' ? ({} as unknown as WebGLRenderingContext) : null,
    );
    expect(supportsWebGL()).toBe(true);
  });

  it('returns false when neither context is available', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    expect(supportsWebGL()).toBe(false);
  });

  it('returns false rather than throwing when getContext itself throws', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => {
      throw new Error('no GPU');
    });
    expect(supportsWebGL()).toBe(false);
  });
});
