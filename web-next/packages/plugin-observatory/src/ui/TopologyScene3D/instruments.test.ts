import { describe, expect, it } from 'vitest';
import { arcRing, circleRing, graduatedRing } from './instruments';

/** Every vertex's distance from the centre, in the ground plane. */
function radii(positions: Float32Array): number[] {
  const out: number[] = [];
  for (let i = 0; i < positions.length; i += 3) {
    out.push(Math.hypot(positions[i]!, positions[i + 2]!));
  }
  return out;
}

describe('graduatedRing', () => {
  it('draws one tick per graduation, running inward from the circle', () => {
    const positions = graduatedRing(12, 3, 0.05, 0.1);
    expect(positions.length).toBe(12 * 6);

    for (let tick = 0; tick < 12; tick += 1) {
      const [outer, inner] = [tick * 6, tick * 6 + 3];
      const outerRadius = Math.hypot(positions[outer]!, positions[outer + 2]!);
      const innerRadius = Math.hypot(positions[inner]!, positions[inner + 2]!);
      expect(outerRadius).toBeCloseTo(1, 6);
      expect(innerRadius).toBeLessThan(outerRadius);
    }
  });

  it('makes the majors longer, so the scale reads as a scale', () => {
    // Ticks all one length is hatching; a scale needs somewhere to count from.
    const positions = graduatedRing(12, 4, 0.05, 0.2);
    const inner = (tick: number) => Math.hypot(positions[tick * 6 + 3]!, positions[tick * 6 + 5]!);
    expect(inner(0)).toBeCloseTo(0.8, 6);
    expect(inner(4)).toBeCloseTo(0.8, 6);
    expect(inner(1)).toBeCloseTo(0.95, 6);
  });

  it('lies flat in the ground plane', () => {
    const positions = graduatedRing(8, 2, 0.05, 0.1);
    for (let i = 1; i < positions.length; i += 3) expect(positions[i]).toBe(0);
  });

  it('survives being asked for no graduations at all', () => {
    expect(graduatedRing(0, 1, 0.05, 0.1).length).toBe(6);
  });
});

describe('arcRing', () => {
  it('spans a share of the circle proportional to the figure', () => {
    const half = arcRing(0.5, 96).length / 3;
    const quarter = arcRing(0.25, 96).length / 3;
    expect(half).toBeGreaterThan(quarter);
    // A full gauge closes the circle.
    expect(arcRing(1, 96).length / 3).toBe(97);
  });

  it('pins rather than overrunning its own dial', () => {
    // A gauge that wraps past the top reads as a smaller value than it is.
    expect(arcRing(4, 96).length).toBe(arcRing(1, 96).length);
    expect(arcRing(-2, 96).length).toBe(arcRing(0, 96).length);
  });

  it('keeps every vertex on the unit circle, in the ground plane', () => {
    const positions = arcRing(0.7, 64);
    for (const radius of radii(positions)) expect(radius).toBeCloseTo(1, 6);
    for (let i = 1; i < positions.length; i += 3) expect(positions[i]).toBe(0);
  });
});

describe('circleRing', () => {
  it('closes a full circle on the unit radius', () => {
    const positions = circleRing(48);
    expect(positions.length).toBe(48 * 3);
    for (const radius of radii(positions)) expect(radius).toBeCloseTo(1, 6);
  });

  it('will not collapse below a shape with an inside', () => {
    expect(circleRing(1).length / 3).toBe(3);
  });
});
