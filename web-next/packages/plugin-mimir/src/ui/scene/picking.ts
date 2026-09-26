/**
 * Pure screen-space picking and click/drag gesture classification.
 *
 * `MemoryScene` projects every visible node to 2D screen coordinates each
 * frame (three.js's job) and hands the flat list here — nothing in this
 * module knows about three.js, so pick behaviour is testable on plain
 * numbers.
 */

import { CLICK_DRAG_THRESHOLD_PX, PICK_RADIUS_PX } from './scene3dConfig';

export interface ProjectedPoint {
  id: string;
  /** Screen-space X, in CSS pixels, origin top-left. */
  x: number;
  /** Screen-space Y, in CSS pixels, origin top-left. */
  y: number;
}

/** The nearest node within `radiusPx` of the pointer, or null when nothing is close enough. */
export function pickNearestNode(
  points: readonly ProjectedPoint[],
  pointerX: number,
  pointerY: number,
  radiusPx: number = PICK_RADIUS_PX,
): string | null {
  let bestId: string | null = null;
  let bestDist = Infinity;
  for (const point of points) {
    const dist = Math.hypot(point.x - pointerX, point.y - pointerY);
    if (dist <= radiusPx && dist < bestDist) {
      bestDist = dist;
      bestId = point.id;
    }
  }
  return bestId;
}

/**
 * Whether a press-then-release should count as a drag (camera gesture)
 * rather than a click (select/background click). A press that never moved
 * more than `thresholdPx` is a click even if it took a while.
 */
export function isDragGesture(
  startX: number,
  startY: number,
  endX: number,
  endY: number,
  thresholdPx: number = CLICK_DRAG_THRESHOLD_PX,
): boolean {
  return Math.hypot(endX - startX, endY - startY) > thresholdPx;
}
