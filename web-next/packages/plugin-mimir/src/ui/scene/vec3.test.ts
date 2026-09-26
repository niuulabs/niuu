import { describe, it, expect } from 'vitest';
import {
  vec3,
  add,
  subtract,
  scale,
  length,
  distance,
  normalize,
  cross,
  lerp,
  emptyBounds,
  growBounds,
  isEmptyBounds,
  boundsCentre,
} from './vec3';

describe('vec3 algebra', () => {
  it('adds and subtracts', () => {
    expect(add(vec3(1, 2, 3), vec3(4, 5, 6))).toEqual({ x: 5, y: 7, z: 9 });
    expect(subtract(vec3(4, 5, 6), vec3(1, 2, 3))).toEqual({ x: 3, y: 3, z: 3 });
  });

  it('scales', () => {
    expect(scale(vec3(1, 2, 3), 2)).toEqual({ x: 2, y: 4, z: 6 });
  });

  it('computes length and distance', () => {
    expect(length(vec3(3, 4, 0))).toBe(5);
    expect(distance(vec3(0, 0, 0), vec3(3, 4, 0))).toBe(5);
  });

  it('normalizes to a unit vector', () => {
    const n = normalize(vec3(3, 4, 0));
    expect(length(n)).toBeCloseTo(1);
  });

  it('normalizes the zero vector to zero', () => {
    expect(normalize(vec3(0, 0, 0))).toEqual({ x: 0, y: 0, z: 0 });
  });

  it('computes the cross product', () => {
    expect(cross(vec3(1, 0, 0), vec3(0, 1, 0))).toEqual({ x: 0, y: 0, z: 1 });
  });

  it('lerps between two points', () => {
    expect(lerp(vec3(0, 0, 0), vec3(10, 10, 10), 0.5)).toEqual({ x: 5, y: 5, z: 5 });
    expect(lerp(vec3(0, 0, 0), vec3(10, 0, 0), 0)).toEqual({ x: 0, y: 0, z: 0 });
    expect(lerp(vec3(0, 0, 0), vec3(10, 0, 0), 1)).toEqual({ x: 10, y: 0, z: 0 });
  });
});

describe('bounds', () => {
  it('starts empty', () => {
    expect(isEmptyBounds(emptyBounds())).toBe(true);
  });

  it('grows to contain points, with optional radius padding', () => {
    const bounds = emptyBounds();
    growBounds(bounds, vec3(1, 1, 1));
    growBounds(bounds, vec3(-1, -1, -1), 0.5);
    expect(isEmptyBounds(bounds)).toBe(false);
    expect(bounds.min).toEqual({ x: -1.5, y: -1.5, z: -1.5 });
    expect(bounds.max).toEqual({ x: 1, y: 1, z: 1 });
  });

  it('centres an empty box at the origin', () => {
    expect(boundsCentre(emptyBounds())).toEqual({ x: 0, y: 0, z: 0 });
  });

  it('centres a populated box at its midpoint', () => {
    const bounds = emptyBounds();
    growBounds(bounds, vec3(0, 0, 0));
    growBounds(bounds, vec3(10, 20, 30));
    expect(boundsCentre(bounds)).toEqual({ x: 5, y: 10, z: 15 });
  });
});
