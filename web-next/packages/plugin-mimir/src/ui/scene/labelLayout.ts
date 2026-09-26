/**
 * Simple greedy collision avoidance for the DOM label overlay.
 *
 * Labels are placed above their anchor by default and pushed to a few
 * alternate sides when that collides with a higher-priority label already
 * placed. A label that still can't find a free slot is marked not visible
 * rather than drawn overlapping — better a missing label than a
 * unreadable pile of text.
 */

import { LABEL_BOX } from './scene3dConfig';

export interface LabelCandidate {
  id: string;
  anchorX: number;
  anchorY: number;
  /** Higher priority labels are placed first and are never displaced by a later one. */
  priority: number;
}

export interface PlacedLabel {
  id: string;
  /** Top-left of the label box, in the same screen-pixel space as the anchors. */
  x: number;
  y: number;
  visible: boolean;
}

interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

function overlaps(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
}

/** Candidate offsets from the anchor, tried in order: above, right, left, below. */
function candidateOffsets(width: number, height: number): Array<{ dx: number; dy: number }> {
  const gap = 6;
  return [
    { dx: -width / 2, dy: -height - gap },
    { dx: gap, dy: -height / 2 },
    { dx: -width - gap, dy: -height / 2 },
    { dx: -width / 2, dy: gap },
  ];
}

/** Place `candidates` avoiding overlap, higher `priority` placed (and never displaced) first. */
export function layoutLabels(
  candidates: readonly LabelCandidate[],
  box: { width: number; height: number } = LABEL_BOX,
): PlacedLabel[] {
  const ordered = [...candidates].sort((a, b) => b.priority - a.priority);
  const placedRects: Rect[] = [];
  const results: PlacedLabel[] = [];

  for (const candidate of ordered) {
    let chosen: Rect | null = null;
    for (const offset of candidateOffsets(box.width, box.height)) {
      const rect: Rect = {
        x: candidate.anchorX + offset.dx,
        y: candidate.anchorY + offset.dy,
        width: box.width,
        height: box.height,
      };
      if (!placedRects.some((existing) => overlaps(existing, rect))) {
        chosen = rect;
        break;
      }
    }
    if (chosen) {
      placedRects.push(chosen);
      results.push({ id: candidate.id, x: chosen.x, y: chosen.y, visible: true });
    } else {
      results.push({
        id: candidate.id,
        x: candidate.anchorX,
        y: candidate.anchorY,
        visible: false,
      });
    }
  }

  // Restore the caller's original ordering — priority only decided placement order.
  const byId = new Map(results.map((r) => [r.id, r]));
  return candidates.map((c) => byId.get(c.id)!);
}
