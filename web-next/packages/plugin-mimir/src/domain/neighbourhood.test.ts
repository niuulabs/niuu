import { describe, it, expect } from 'vitest';
import { pageLabel, ringLayout } from './neighbourhood';
import type { RelatedPage } from './evidence';

function related(path: string, overrides: Partial<RelatedPage> = {}): RelatedPage {
  return { path, hop: 1, rel: null, direction: 'out', ...overrides };
}

const OPTIONS = { limit: 8, width: 300, height: 220, radius: 78 };

describe('pageLabel', () => {
  it('takes the last segment', () => {
    expect(pageLabel('/arch/overview')).toBe('overview');
  });

  it('drops the .md suffix used by the live API', () => {
    expect(pageLabel('technical/session-store.md')).toBe('session-store');
  });

  it('falls back to the whole path when there is no segment', () => {
    expect(pageLabel('/')).toBe('/');
  });
});

describe('ringLayout', () => {
  it('puts a single neighbour above the centre', () => {
    const layout = ringLayout([related('/a')], OPTIONS);
    expect(layout.nodes[0]?.x).toBeCloseTo(layout.centre.x);
    expect(layout.nodes[0]?.y).toBeCloseTo(layout.centre.y - OPTIONS.radius);
  });

  it('spreads neighbours evenly on the ring', () => {
    const layout = ringLayout(
      [related('/a'), related('/b'), related('/c'), related('/d')],
      OPTIONS,
    );
    for (const node of layout.nodes) {
      const dx = node.x - layout.centre.x;
      const dy = node.y - layout.centre.y;
      expect(Math.hypot(dx, dy)).toBeCloseTo(OPTIONS.radius);
    }
    expect(
      new Set(layout.nodes.map((node) => `${node.x.toFixed(2)},${node.y.toFixed(2)}`)).size,
    ).toBe(4);
  });

  it('carries the label, rel and direction of each neighbour', () => {
    const layout = ringLayout(
      [related('/entities/asyncpg', { rel: 'uses', direction: 'in' })],
      OPTIONS,
    );
    expect(layout.nodes[0]).toMatchObject({ label: 'asyncpg', rel: 'uses', direction: 'in' });
  });

  it('counts the neighbours that did not fit', () => {
    const layout = ringLayout(
      Array.from({ length: 11 }, (_, index) => related(`/p${index}`)),
      OPTIONS,
    );
    expect(layout.nodes).toHaveLength(8);
    expect(layout.overflow).toBe(3);
  });

  it('is empty, not broken, without neighbours', () => {
    const layout = ringLayout([], OPTIONS);
    expect(layout.nodes).toEqual([]);
    expect(layout.overflow).toBe(0);
  });
});
