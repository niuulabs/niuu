import { describe, it, expect, afterEach } from 'vitest';
import { memoryPalette, DEFAULT_MEMORY_PALETTE } from './palette';

describe('memoryPalette', () => {
  afterEach(() => {
    document.documentElement.removeAttribute('style');
  });

  it('falls back to the default palette when no tokens are defined', () => {
    const el = document.createElement('div');
    document.body.appendChild(el);
    const palette = memoryPalette(el);
    expect(palette).toEqual(DEFAULT_MEMORY_PALETTE);
    el.remove();
  });

  it('reads a defined CSS custom property over the fallback', () => {
    const el = document.createElement('div');
    el.style.setProperty('--color-memory-kind-topic', 'rgb(1, 2, 3)');
    el.style.setProperty('--color-memory-proof-high', 'rgb(4, 5, 6)');
    el.style.setProperty('--color-memory-age-today', 'rgb(7, 8, 9)');
    el.style.setProperty('--color-memory-dispute', 'rgb(10, 11, 12)');
    document.body.appendChild(el);

    const palette = memoryPalette(el);
    expect(palette.kind.topic).toBe('rgb(1, 2, 3)');
    expect(palette.proof.high).toBe('rgb(4, 5, 6)');
    expect(palette.age.today).toBe('rgb(7, 8, 9)');
    expect(palette.dispute).toBe('rgb(10, 11, 12)');
    // Untouched tokens still fall back.
    expect(palette.kind.entity).toBe(DEFAULT_MEMORY_PALETTE.kind.entity);
    el.remove();
  });

  it('inherits a custom property set on an ancestor', () => {
    const parent = document.createElement('div');
    parent.style.setProperty('--color-memory-kind-entity', 'rgb(9, 9, 9)');
    const child = document.createElement('div');
    parent.appendChild(child);
    document.body.appendChild(parent);

    expect(memoryPalette(child).kind.entity).toBe('rgb(9, 9, 9)');
    parent.remove();
  });
});
