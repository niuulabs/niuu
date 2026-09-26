import { describe, it, expect } from 'vitest';
import { layoutLabels } from './labelLayout';

const BOX = { width: 40, height: 10 };

describe('layoutLabels', () => {
  it('places a single label above its anchor', () => {
    const [placed] = layoutLabels([{ id: 'a', anchorX: 100, anchorY: 100, priority: 1 }], BOX);
    expect(placed!.visible).toBe(true);
    expect(placed!.y).toBeLessThan(100);
  });

  it('places two far-apart labels without collision, both visible', () => {
    const placed = layoutLabels(
      [
        { id: 'a', anchorX: 0, anchorY: 0, priority: 1 },
        { id: 'b', anchorX: 1000, anchorY: 1000, priority: 1 },
      ],
      BOX,
    );
    expect(placed.every((p) => p.visible)).toBe(true);
  });

  it('displaces a lower-priority label to a side slot when it collides above', () => {
    const placed = layoutLabels(
      [
        { id: 'high', anchorX: 100, anchorY: 100, priority: 2 },
        { id: 'low', anchorX: 101, anchorY: 100, priority: 1 },
      ],
      BOX,
    );
    const high = placed.find((p) => p.id === 'high')!;
    const low = placed.find((p) => p.id === 'low')!;
    expect(high.visible).toBe(true);
    expect(low.visible).toBe(true);
    // They must not end up in the same box.
    expect(high.x === low.x && high.y === low.y).toBe(false);
  });

  it('hides a label when every candidate slot around it is already taken', () => {
    // Four high-priority labels packed at the same anchor take all four
    // candidate slots (above/right/left/below), leaving none for the fifth.
    const packed = Array.from({ length: 4 }, (_, i) => ({
      id: `p${i}`,
      anchorX: 500,
      anchorY: 500,
      priority: 10,
    }));
    const overflow = { id: 'overflow', anchorX: 500, anchorY: 500, priority: 1 };
    const placed = layoutLabels([...packed, overflow], BOX);
    expect(placed.find((p) => p.id === 'overflow')?.visible).toBe(false);
  });

  it('never displaces an already-placed higher-priority label', () => {
    const placed = layoutLabels(
      [
        { id: 'low', anchorX: 100, anchorY: 100, priority: 1 },
        { id: 'high', anchorX: 100, anchorY: 100, priority: 5 },
      ],
      BOX,
    );
    // "high" is processed first regardless of input order and gets the
    // default above-anchor slot.
    const high = placed.find((p) => p.id === 'high')!;
    expect(high.y).toBeLessThan(100);
  });

  it('preserves the caller-supplied ordering of the result array', () => {
    const input = [
      { id: 'z', anchorX: 0, anchorY: 0, priority: 1 },
      { id: 'a', anchorX: 10, anchorY: 10, priority: 2 },
    ];
    const placed = layoutLabels(input, BOX);
    expect(placed.map((p) => p.id)).toEqual(['z', 'a']);
  });

  it('returns an empty array for no candidates', () => {
    expect(layoutLabels([], BOX)).toEqual([]);
  });
});
