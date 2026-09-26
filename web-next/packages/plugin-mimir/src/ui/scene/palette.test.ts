import { describe, it, expect, afterEach } from 'vitest';
import { memoryPalette, MEMORY_PALETTE_VARS } from './palette';
import { installMemoryPaletteTokens } from './test-helpers';

describe('memoryPalette', () => {
  let uninstall: (() => void) | null = null;

  afterEach(() => {
    uninstall?.();
    uninstall = null;
  });

  it('throws, naming every missing token and the stylesheet to import, when none are defined', () => {
    const el = document.createElement('div');
    document.body.appendChild(el);
    expect(() => memoryPalette(el)).toThrow(/--color-memory-kind-topic.*--color-memory-dispute/s);
    expect(() => memoryPalette(el)).toThrow(/@niuulabs\/design-tokens\/tokens\.css/);
    el.remove();
  });

  it('throws when a single token is missing', () => {
    uninstall = installMemoryPaletteTokens();
    document.documentElement.style.removeProperty('--color-memory-age-older');
    const el = document.createElement('div');
    document.body.appendChild(el);
    expect(() => memoryPalette(el)).toThrow('--color-memory-age-older.');
    el.remove();
  });

  it('reads every token inherited from the document root', () => {
    uninstall = installMemoryPaletteTokens();
    const el = document.createElement('div');
    document.body.appendChild(el);
    const palette = memoryPalette(el);
    expect(palette.kind.topic).toBe(
      `rgb(${MEMORY_PALETTE_VARS.indexOf('--color-memory-kind-topic') + 1}, 1, 1)`,
    );
    expect(palette.dispute).toBe(`rgb(${MEMORY_PALETTE_VARS.length}, 1, 1)`);
    el.remove();
  });

  it('prefers a value set closer to the element', () => {
    uninstall = installMemoryPaletteTokens();
    const parent = document.createElement('div');
    parent.style.setProperty('--color-memory-kind-entity', 'rgb(9, 9, 9)');
    const child = document.createElement('div');
    parent.appendChild(child);
    document.body.appendChild(parent);
    expect(memoryPalette(child).kind.entity).toBe('rgb(9, 9, 9)');
    parent.remove();
  });
});
