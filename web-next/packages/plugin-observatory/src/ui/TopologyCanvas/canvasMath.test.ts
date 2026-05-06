import { describe, it, expect } from 'vitest';
import {
  clampZoom,
  screenToWorld,
  applyDragPan,
  applyScrollZoom,
  applyKeyPan,
  defaultCamera,
  type Camera,
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
