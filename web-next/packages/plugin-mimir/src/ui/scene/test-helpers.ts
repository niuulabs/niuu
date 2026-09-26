import { vi } from 'vitest';
import type { Scene3DRenderer } from './webglRenderer';
import { MEMORY_PALETTE_VARS, type MemoryPalette } from './palette';

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

/** A complete palette with distinct values, for pure colour tests. */
export const TEST_PALETTE: MemoryPalette = {
  kind: {
    topic: 'rgb(1, 0, 0)',
    entity: 'rgb(2, 0, 0)',
    decision: 'rgb(3, 0, 0)',
    directive: 'rgb(4, 0, 0)',
    preference: 'rgb(5, 0, 0)',
    goal: 'rgb(6, 0, 0)',
    observation: 'rgb(7, 0, 0)',
    thread: 'rgb(8, 0, 0)',
    page: 'rgb(9, 0, 0)',
  },
  proof: {
    high: 'rgb(0, 1, 0)',
    medium: 'rgb(0, 2, 0)',
    low: 'rgb(0, 3, 0)',
    none: 'rgb(0, 4, 0)',
  },
  age: {
    today: 'rgb(0, 0, 1)',
    'this-week': 'rgb(0, 0, 2)',
    'this-month': 'rgb(0, 0, 3)',
    older: 'rgb(0, 0, 4)',
  },
  dispute: 'rgb(9, 9, 0)',
};

/**
 * Define every memory colour token on `<html>`, as the design-tokens
 * stylesheet does on a real page. Returns a function that removes them.
 */
export function installMemoryPaletteTokens(): () => void {
  const root = document.documentElement;
  MEMORY_PALETTE_VARS.forEach((name, i) => root.style.setProperty(name, `rgb(${i + 1}, 1, 1)`));
  return () => MEMORY_PALETTE_VARS.forEach((name) => root.style.removeProperty(name));
}
