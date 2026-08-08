import { describe, expect, it } from 'vitest';
import {
  add,
  boundsCentre,
  boundsRadius,
  cross,
  distance,
  emptyBounds,
  growBounds,
  isEmptyBounds,
  length,
  lerp,
  normalize,
  quadraticBezier,
  scale,
  subtract,
  vec3,
} from './vec3';

describe('vec3', () => {
  it('adds, subtracts and scales', () => {
    expect(add(vec3(1, 2, 3), vec3(4, 5, 6))).toEqual({ x: 5, y: 7, z: 9 });
    expect(subtract(vec3(4, 5, 6), vec3(1, 2, 3))).toEqual({ x: 3, y: 3, z: 3 });
    expect(scale(vec3(1, -2, 3), 2)).toEqual({ x: 2, y: -4, z: 6 });
  });

  it('measures length and distance', () => {
    expect(length(vec3(3, 4, 0))).toBe(5);
    expect(distance(vec3(1, 0, 0), vec3(4, 4, 0))).toBe(5);
  });

  it('normalises, and returns zero rather than NaN for the zero vector', () => {
    expect(normalize(vec3(0, 5, 0))).toEqual({ x: 0, y: 1, z: 0 });
    expect(normalize(vec3(0, 0, 0))).toEqual({ x: 0, y: 0, z: 0 });
  });

  it('crosses right-handed', () => {
    expect(cross(vec3(1, 0, 0), vec3(0, 1, 0))).toEqual({ x: 0, y: 0, z: 1 });
  });

  it('interpolates', () => {
    expect(lerp(vec3(0, 0, 0), vec3(10, 20, 30), 0.5)).toEqual({ x: 5, y: 10, z: 15 });
  });

  it('walks a quadratic Bézier through its ends and past its control point', () => {
    const a = vec3(0, 0, 0);
    const control = vec3(5, 10, 0);
    const b = vec3(10, 0, 0);

    expect(quadraticBezier(a, control, b, 0)).toEqual(a);
    expect(quadraticBezier(a, control, b, 1)).toEqual(b);
    // The curve is pulled toward the control point without reaching it.
    const mid = quadraticBezier(a, control, b, 0.5);
    expect(mid.x).toBeCloseTo(5);
    expect(mid.y).toBeCloseTo(5);
    expect(mid.y).toBeLessThan(control.y);
  });
});

describe('bounds', () => {
  it('starts empty and reports so', () => {
    const bounds = emptyBounds();
    expect(isEmptyBounds(bounds)).toBe(true);
    expect(boundsCentre(bounds)).toEqual({ x: 0, y: 0, z: 0 });
    expect(boundsRadius(bounds)).toBe(0);
  });

  it('grows around points and their radii', () => {
    const bounds = emptyBounds();
    growBounds(bounds, vec3(0, 0, 0), 1);
    growBounds(bounds, vec3(10, 0, 0));

    expect(isEmptyBounds(bounds)).toBe(false);
    expect(bounds.min).toEqual({ x: -1, y: -1, z: -1 });
    expect(bounds.max).toEqual({ x: 10, y: 1, z: 1 });
    expect(boundsCentre(bounds)).toEqual({ x: 4.5, y: 0, z: 0 });
  });

  it('reports half the box diagonal as its radius', () => {
    const bounds = emptyBounds();
    growBounds(bounds, vec3(0, 0, 0));
    growBounds(bounds, vec3(6, 8, 0));
    expect(boundsRadius(bounds)).toBe(5);
  });
});
