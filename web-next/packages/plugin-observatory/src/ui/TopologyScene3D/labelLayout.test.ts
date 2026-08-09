import { describe, expect, it } from 'vitest';
import { overlaps, placeLabels, type LabelCandidate } from './labelLayout';

function label(
  id: string,
  x: number,
  y: number,
  priority = 0,
  width = 100,
  height = 20,
): LabelCandidate {
  return { id, x, y, width, height, priority };
}

describe('overlaps', () => {
  it('sees names that would touch, and lets past ones that clear', () => {
    expect(overlaps(label('a', 0, 0), label('b', 50, 0))).toBe(true);
    expect(overlaps(label('a', 0, 0), label('b', 120, 0))).toBe(false);
    // Clear on either axis is clear.
    expect(overlaps(label('a', 0, 0), label('b', 0, 40))).toBe(false);
  });

  it('honours the clear air a name insists on', () => {
    expect(overlaps(label('a', 0, 0), label('b', 105, 0))).toBe(false);
    expect(overlaps(label('a', 0, 0), label('b', 105, 0), 20)).toBe(true);
  });
});

describe('placeLabels', () => {
  it('draws a name where there is room for it', () => {
    const chosen = placeLabels([label('a', 0, 0), label('b', 400, 0)], 10);
    expect([...chosen].sort()).toEqual(['a', 'b']);
  });

  it('drops the lesser of two names that would land on each other', () => {
    // Two agents a metre apart in the same rack are both "near"; without this
    // their names sit on top of each other and say less than none would.
    const chosen = placeLabels([label('quiet', 0, 0, 1), label('loud', 20, 0, 99)], 10);
    expect([...chosen]).toEqual(['loud']);
  });

  it('lets what is pointed at take the space from whatever is nearer', () => {
    const chosen = placeLabels([label('near', 0, 0, 10), label('selected', 10, 0, 1e6)], 10);
    expect([...chosen]).toEqual(['selected']);
  });

  it('stops at the cap, however much room is left', () => {
    const many = Array.from({ length: 40 }, (_unused, i) => label(`n${i}`, i * 300, 0, 40 - i));
    expect(placeLabels(many, 5).size).toBe(5);
  });

  it('is decided by priority, not by the order it was handed', () => {
    const forwards = placeLabels([label('a', 0, 0, 1), label('b', 20, 0, 2)], 10);
    const backwards = placeLabels([label('b', 20, 0, 2), label('a', 0, 0, 1)], 10);
    expect([...forwards]).toEqual([...backwards]);
  });

  it('chooses nothing out of nothing', () => {
    expect(placeLabels([], 10).size).toBe(0);
  });
});
