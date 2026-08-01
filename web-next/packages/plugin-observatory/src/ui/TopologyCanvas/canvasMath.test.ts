import { describe, it, expect } from 'vitest';
import {
  clampZoom,
  screenToWorld,
  applyDragPan,
  applyScrollZoom,
  applyKeyPan,
  defaultCamera,
  fitCameraToBounds,
  type Camera,
  centroid,
  convexHull,
  expandFromCentroid,
} from './canvasMath';
import { CANVAS } from './config';

// ── clampZoom ─────────────────────────────────────────────────────────────────

describe('clampZoom', () => {
  it('clamps below ZOOM_MIN', () => {
    expect(clampZoom(0.0)).toBe(CANVAS.ZOOM_MIN);
    expect(clampZoom(0.1)).toBe(CANVAS.ZOOM_MIN);
    expect(clampZoom(-5)).toBe(CANVAS.ZOOM_MIN);
  });

  it('clamps above ZOOM_MAX', () => {
    expect(clampZoom(10)).toBe(CANVAS.ZOOM_MAX);
    expect(clampZoom(4)).toBe(CANVAS.ZOOM_MAX);
    expect(clampZoom(100)).toBe(CANVAS.ZOOM_MAX);
  });

  it('passes through a value within bounds', () => {
    expect(clampZoom(1.0)).toBe(1.0);
    expect(clampZoom(0.5)).toBe(0.5);
    expect(clampZoom(2.5)).toBe(2.5);
  });

  it('accepts exactly ZOOM_MIN', () => {
    expect(clampZoom(CANVAS.ZOOM_MIN)).toBe(CANVAS.ZOOM_MIN);
  });

  it('accepts exactly ZOOM_MAX', () => {
    expect(clampZoom(CANVAS.ZOOM_MAX)).toBe(CANVAS.ZOOM_MAX);
  });
});

// ── screenToWorld ─────────────────────────────────────────────────────────────

describe('screenToWorld', () => {
  it('converts the screen centre to the camera position at zoom=1', () => {
    const cam: Camera = { x: 100, y: 200, zoom: 1 };
    const result = screenToWorld(500, 400, cam, 1000, 800);
    expect(result.x).toBeCloseTo(100);
    expect(result.y).toBeCloseTo(200);
  });

  it('converts the top-left corner correctly at zoom=1', () => {
    const cam: Camera = { x: 0, y: 0, zoom: 1 };
    const result = screenToWorld(0, 0, cam, 1000, 800);
    expect(result.x).toBeCloseTo(-500);
    expect(result.y).toBeCloseTo(-400);
  });

  it('applies zoom factor — zoom=2 halves the world extent', () => {
    const cam: Camera = { x: 0, y: 0, zoom: 2 };
    const result = screenToWorld(600, 400, cam, 800, 800);
    // (600 - 400) / 2 + 0 = 100
    expect(result.x).toBeCloseTo(100);
    // (400 - 400) / 2 + 0 = 0
    expect(result.y).toBeCloseTo(0);
  });

  it('is consistent with zoom=0.5', () => {
    const cam: Camera = { x: 0, y: 0, zoom: 0.5 };
    const result = screenToWorld(200, 100, cam, 400, 200);
    // (200 - 200) / 0.5 + 0 = 0
    expect(result.x).toBeCloseTo(0);
    // (100 - 100) / 0.5 + 0 = 0
    expect(result.y).toBeCloseTo(0);
  });
});

// ── applyDragPan ──────────────────────────────────────────────────────────────

describe('applyDragPan', () => {
  it('pans left: positive dx moves camera left (increases world x)', () => {
    const cam: Camera = { x: 0, y: 0, zoom: 1 };
    const result = applyDragPan(cam, 100, 0);
    expect(result.x).toBe(-100);
    expect(result.y).toBe(0);
  });

  it('pans down: positive dy moves camera down (increases world y)', () => {
    const cam: Camera = { x: 0, y: 0, zoom: 1 };
    const result = applyDragPan(cam, 0, 50);
    expect(result.x).toBe(0);
    expect(result.y).toBe(-50);
  });

  it('scales delta by zoom — zoom=2 halves the pan', () => {
    const cam: Camera = { x: 0, y: 0, zoom: 2 };
    const result = applyDragPan(cam, 100, 0);
    expect(result.x).toBeCloseTo(-50);
  });

  it('starts from startCam position, not origin', () => {
    const cam: Camera = { x: 50, y: 80, zoom: 1 };
    const result = applyDragPan(cam, 20, 10);
    expect(result.x).toBe(30);
    expect(result.y).toBe(70);
  });

  it('zero delta returns startCam position unchanged', () => {
    const cam: Camera = { x: 123, y: 456, zoom: 1 };
    const result = applyDragPan(cam, 0, 0);
    expect(result.x).toBe(123);
    expect(result.y).toBe(456);
  });
});

// ── applyScrollZoom ───────────────────────────────────────────────────────────

describe('applyScrollZoom', () => {
  it('negative deltaY zooms in (increases zoom)', () => {
    const cam: Camera = { x: 0, y: 0, zoom: 1 };
    const result = applyScrollZoom(cam, -100, 400, 300, 800, 600);
    expect(result.zoom).toBeGreaterThan(1);
  });

  it('positive deltaY zooms out (decreases zoom)', () => {
    const cam: Camera = { x: 0, y: 0, zoom: 1 };
    const result = applyScrollZoom(cam, 100, 400, 300, 800, 600);
    expect(result.zoom).toBeLessThan(1);
  });

  it('clamps to ZOOM_MAX when already near max', () => {
    const cam: Camera = { x: 0, y: 0, zoom: CANVAS.ZOOM_MAX };
    const result = applyScrollZoom(cam, -100, 400, 300, 800, 600);
    expect(result.zoom).toBe(CANVAS.ZOOM_MAX);
  });

  it('clamps to ZOOM_MIN when already near min', () => {
    const cam: Camera = { x: 0, y: 0, zoom: CANVAS.ZOOM_MIN };
    const result = applyScrollZoom(cam, 100, 400, 300, 800, 600);
    expect(result.zoom).toBe(CANVAS.ZOOM_MIN);
  });

  it('preserves the world point under the cursor (zoom toward cursor)', () => {
    const cam: Camera = { x: 0, y: 0, zoom: 1 };
    const viewW = 1000;
    const viewH = 800;
    // Cursor at screen centre — world point stays the same
    const result = applyScrollZoom(cam, -1, viewW / 2, viewH / 2, viewW, viewH);
    expect(result.x).toBeCloseTo(cam.x);
    expect(result.y).toBeCloseTo(cam.y);
  });
});

// ── applyKeyPan ───────────────────────────────────────────────────────────────

describe('applyKeyPan', () => {
  const cam: Camera = { x: 0, y: 0, zoom: 1 };
  const step = 80;

  it('ArrowUp decreases y', () => {
    const result = applyKeyPan(cam, 'ArrowUp', step);
    expect(result.y).toBe(-step);
    expect(result.x).toBe(0);
  });

  it('ArrowDown increases y', () => {
    const result = applyKeyPan(cam, 'ArrowDown', step);
    expect(result.y).toBe(step);
  });

  it('ArrowLeft decreases x', () => {
    const result = applyKeyPan(cam, 'ArrowLeft', step);
    expect(result.x).toBe(-step);
  });

  it('ArrowRight increases x', () => {
    const result = applyKeyPan(cam, 'ArrowRight', step);
    expect(result.x).toBe(step);
  });

  it('unknown key returns camera unchanged', () => {
    const result = applyKeyPan(cam, 'Enter', step);
    expect(result).toEqual(cam);
  });

  it('preserves zoom and other fields', () => {
    const cam2: Camera = { x: 10, y: 20, zoom: 2 };
    const result = applyKeyPan(cam2, 'ArrowUp', step);
    expect(result.zoom).toBe(2);
    expect(result.x).toBe(10);
  });
});

// ── defaultCamera ─────────────────────────────────────────────────────────────

describe('defaultCamera', () => {
  it('returns origin at INITIAL_ZOOM', () => {
    const cam = defaultCamera();
    expect(cam.x).toBe(0);
    expect(cam.y).toBe(0);
    expect(cam.zoom).toBe(CANVAS.INITIAL_ZOOM);
  });

  it('zoom is within allowed bounds', () => {
    const { zoom } = defaultCamera();
    expect(zoom).toBeGreaterThanOrEqual(CANVAS.ZOOM_MIN);
    expect(zoom).toBeLessThanOrEqual(CANVAS.ZOOM_MAX);
  });
});

describe('fitCameraToBounds', () => {
  it('centres the camera on the bounds midpoint', () => {
    const cam = fitCameraToBounds({ minX: -200, minY: -100, maxX: 600, maxY: 300 }, 1200, 800);
    expect(cam.x).toBe(200);
    expect(cam.y).toBe(100);
  });

  it('returns a zoom within allowed bounds', () => {
    const cam = fitCameraToBounds({ minX: -100, minY: -100, maxX: 100, maxY: 100 }, 1200, 800);
    expect(cam.zoom).toBeGreaterThanOrEqual(CANVAS.ZOOM_MIN);
    expect(cam.zoom).toBeLessThanOrEqual(CANVAS.ZOOM_MAX);
  });

  it('falls back to default camera for missing bounds', () => {
    expect(fitCameraToBounds(null, 1200, 800)).toEqual(defaultCamera());
  });
});

// ── Hull geometry ─────────────────────────────────────────────────────────────

describe('centroid', () => {
  it('averages the points', () => {
    expect(
      centroid([
        { x: 0, y: 0 },
        { x: 10, y: 0 },
        { x: 10, y: 10 },
        { x: 0, y: 10 },
      ]),
    ).toEqual({
      x: 5,
      y: 5,
    });
  });

  it('returns the origin for an empty set rather than NaN', () => {
    expect(centroid([])).toEqual({ x: 0, y: 0 });
  });
});

describe('convexHull', () => {
  it('drops a point enclosed by the others', () => {
    const hull = convexHull([
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 10, y: 10 },
      { x: 0, y: 10 },
      { x: 5, y: 5 },
    ]);
    expect(hull).toHaveLength(4);
    expect(hull).not.toContainEqual({ x: 5, y: 5 });
  });

  it('returns the input when there is no hull to compute', () => {
    expect(convexHull([])).toEqual([]);
    expect(convexHull([{ x: 1, y: 2 }])).toEqual([{ x: 1, y: 2 }]);
    const pair = [
      { x: 0, y: 0 },
      { x: 4, y: 4 },
    ];
    expect(convexHull(pair)).toEqual(pair);
  });

  it('falls back to the input for collinear points instead of collapsing', () => {
    const line = [
      { x: 0, y: 0 },
      { x: 1, y: 1 },
      { x: 2, y: 2 },
    ];
    expect(convexHull(line)).toEqual(line);
  });

  it('does not mutate its input', () => {
    const points = [
      { x: 3, y: 0 },
      { x: 0, y: 0 },
      { x: 0, y: 3 },
    ];
    const snapshot = JSON.stringify(points);
    convexHull(points);
    expect(JSON.stringify(points)).toBe(snapshot);
  });
});

describe('expandFromCentroid', () => {
  it('pushes each vertex outward by the padding', () => {
    const origin = { x: 0, y: 0 };
    const expanded = expandFromCentroid(
      [
        { x: 10, y: 0 },
        { x: 0, y: 10 },
      ],
      origin,
      5,
    );
    expect(expanded[0]).toEqual({ x: 15, y: 0 });
    expect(expanded[1]).toEqual({ x: 0, y: 15 });
  });

  it('offsets a point sitting on the centroid rather than dividing by zero', () => {
    const expanded = expandFromCentroid([{ x: 2, y: 2 }], { x: 2, y: 2 }, 6);
    expect(expanded[0]).toEqual({ x: 8, y: 2 });
  });
});
