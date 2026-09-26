import { vi } from 'vitest';
import type { Scene3DRenderer } from './webglRenderer';

/**
 * A fake `Scene3DRenderer` for tests — no GPU, no real pixels. `MemoryScene`
 * builds its whole three.js scene graph against this (pure CPU-side
 * three.js objects; only `render()` would ever need a real context, and
 * this fakes that call out), so every prop-driven behaviour is exercised
 * without a browser's WebGL support.
 */
export function createFakeRenderer(): Scene3DRenderer {
  const canvas = document.createElement('canvas');
  return {
    domElement: canvas,
    setSize: vi.fn(),
    setPixelRatio: vi.fn(),
    render: vi.fn(),
    dispose: vi.fn(),
  };
}
