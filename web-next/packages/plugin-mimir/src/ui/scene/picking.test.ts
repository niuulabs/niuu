import { describe, it, expect } from 'vitest';
import { pickNearestNode, isDragGesture } from './picking';

describe('pickNearestNode', () => {
  const points = [
    { id: 'a', x: 10, y: 10 },
    { id: 'b', x: 100, y: 100 },
    { id: 'c', x: 12, y: 11 },
  ];

  it('picks the nearest point within the radius', () => {
    expect(pickNearestNode(points, 11, 10, 20)).toBe('a');
    expect(pickNearestNode(points, 12, 12, 20)).toBe('c');
  });

  it('returns null when nothing is within the radius', () => {
    expect(pickNearestNode(points, 500, 500, 20)).toBeNull();
  });

  it('returns null for an empty point list', () => {
    expect(pickNearestNode([], 0, 0)).toBeNull();
  });

  it('uses the default radius when none is given', () => {
    expect(pickNearestNode([{ id: 'x', x: 0, y: 0 }], 5, 0)).toBe('x');
    expect(pickNearestNode([{ id: 'x', x: 0, y: 0 }], 5000, 0)).toBeNull();
  });

  it('picks the exact point when the pointer lands on it', () => {
    expect(pickNearestNode(points, 100, 100, 1)).toBe('b');
  });
});

describe('isDragGesture', () => {
  it('is false for a press that barely moved', () => {
    expect(isDragGesture(10, 10, 12, 11, 5)).toBe(false);
  });

  it('is true once movement exceeds the threshold', () => {
    expect(isDragGesture(10, 10, 30, 10, 5)).toBe(true);
  });

  it('is false at exactly zero movement', () => {
    expect(isDragGesture(10, 10, 10, 10)).toBe(false);
  });

  it('uses the default threshold when none is given', () => {
    expect(isDragGesture(0, 0, 1, 1)).toBe(false);
    expect(isDragGesture(0, 0, 100, 100)).toBe(true);
  });
});
