/**
 * Pure pan / zoom math functions — no DOM, no React.
 * All are independently testable with plain inputs/outputs.
 */

import { CANVAS } from './config';

export interface Point {
  x: number;
  y: number;
}

export interface Camera {
  x: number;
  y: number;
  zoom: number;
}

export interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

/** Clamp zoom to the [ZOOM_MIN, ZOOM_MAX] range. */
export function clampZoom(zoom: number): number {
  return Math.max(CANVAS.ZOOM_MIN, Math.min(CANVAS.ZOOM_MAX, zoom));
}

/**
 * Convert a screen-space point to world-space coordinates.
 *
 * @param sx    Screen x (pixels from canvas left)
 * @param sy    Screen y (pixels from canvas top)
 * @param cam   Current camera
 * @param viewW Canvas width in CSS pixels
 * @param viewH Canvas height in CSS pixels
 */
export function screenToWorld(
  sx: number,
  sy: number,
  cam: Camera,
  viewW: number,
  viewH: number,
): Point {
  return {
    x: (sx - viewW / 2) / cam.zoom + cam.x,
    y: (sy - viewH / 2) / cam.zoom + cam.y,
  };
}

/**
 * Compute new camera position after a drag gesture.
 *
 * @param startCam  Camera position at the start of the drag
 * @param dx        Mouse delta X since drag start (screen pixels)
 * @param dy        Mouse delta Y since drag start (screen pixels)
 * @param zoom      Current zoom level
 */
export function applyDragPan(startCam: Camera, dx: number, dy: number): Point {
  return {
    x: startCam.x - dx / startCam.zoom,
    y: startCam.y - dy / startCam.zoom,
  };
}

/**
 * Compute new camera state after a scroll-wheel zoom event.
 * Zooms toward the cursor position so the world point under the
 * cursor stays fixed on screen.
 *
 * @param cam   Current camera
 * @param deltaY Positive = scroll down = zoom out
 * @param mx    Cursor X in screen pixels
 * @param my    Cursor Y in screen pixels
 * @param viewW Canvas width in CSS pixels
 * @param viewH Canvas height in CSS pixels
 */
export function applyScrollZoom(
  cam: Camera,
  deltaY: number,
  mx: number,
  my: number,
  viewW: number,
  viewH: number,
): Camera {
  const factor = deltaY < 0 ? CANVAS.ZOOM_STEP : 1 / CANVAS.ZOOM_STEP;
  const newZoom = clampZoom(cam.zoom * factor);

  // Derive camera translation so the world point under the cursor is preserved.
  const newX = (mx - viewW / 2) / cam.zoom + cam.x - (mx - viewW / 2) / newZoom;
  const newY = (my - viewH / 2) / cam.zoom + cam.y - (my - viewH / 2) / newZoom;

  return { x: newX, y: newY, zoom: newZoom };
}

/**
 * Compute new camera position after an arrow-key press.
 *
 * @param cam  Current camera
 * @param key  'ArrowUp' | 'ArrowDown' | 'ArrowLeft' | 'ArrowRight'
 * @param step World units to pan per keypress
 */
export function applyKeyPan(cam: Camera, key: string, step: number): Camera {
  switch (key) {
    case 'ArrowUp':
      return { ...cam, y: cam.y - step };
    case 'ArrowDown':
      return { ...cam, y: cam.y + step };
    case 'ArrowLeft':
      return { ...cam, x: cam.x - step };
    case 'ArrowRight':
      return { ...cam, x: cam.x + step };
    default:
      return cam;
  }
}

/** Build a default camera centred on the world origin. */
export function defaultCamera(): Camera {
  return { x: 0, y: 0, zoom: CANVAS.INITIAL_ZOOM };
}

/** Fit the camera to a world-space bounding box with pixel padding. */
export function fitCameraToBounds(
  bounds: Bounds | null,
  viewW: number,
  viewH: number,
  padding = 96,
): Camera {
  if (!bounds || viewW <= 0 || viewH <= 0) return defaultCamera();

  const width = Math.max(bounds.maxX - bounds.minX, 1);
  const height = Math.max(bounds.maxY - bounds.minY, 1);
  const usableW = Math.max(viewW - padding * 2, 1);
  const usableH = Math.max(viewH - padding * 2, 1);
  const zoom = clampZoom(Math.min(usableW / width, usableH / height));

  return {
    x: (bounds.minX + bounds.maxX) / 2,
    y: (bounds.minY + bounds.maxY) / 2,
    zoom,
  };
}
