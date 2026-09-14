/**
 * Neighbourhood layout — the small ring drawn beside a page.
 *
 * Not the graph view: this is a one-glance picture of what a single page links
 * to and what links back. Pure geometry so it can be tested without a DOM.
 */

import type { RelatedPage } from './evidence';

export interface NeighbourNode {
  path: string;
  /** Last path segment, without the `.md` suffix — what the ring labels. */
  label: string;
  rel: string | null;
  direction: 'in' | 'out';
  x: number;
  y: number;
}

export interface Neighbourhood {
  width: number;
  height: number;
  centre: { x: number; y: number };
  nodes: NeighbourNode[];
  /** Neighbours that did not fit on the ring. */
  overflow: number;
}

/** Full turn, in radians — the ring spreads neighbours evenly across it. */
const FULL_TURN = Math.PI * 2;
/** Start at the top so a single neighbour sits above the page. */
const START_ANGLE = -Math.PI / 2;

export function pageLabel(path: string): string {
  const last = path.split('/').filter(Boolean).pop() ?? path;
  return last.replace(/\.md$/, '');
}

export function ringLayout(
  related: RelatedPage[],
  options: { limit: number; width: number; height: number; radius: number },
): Neighbourhood {
  const { limit, width, height, radius } = options;
  const centre = { x: width / 2, y: height / 2 };
  const shown = related.slice(0, limit);
  const nodes = shown.map((entry, index) => {
    const angle = START_ANGLE + (FULL_TURN * index) / Math.max(1, shown.length);
    return {
      path: entry.path,
      label: pageLabel(entry.path),
      rel: entry.rel,
      direction: entry.direction,
      x: centre.x + radius * Math.cos(angle),
      y: centre.y + radius * Math.sin(angle),
    };
  });
  return {
    width,
    height,
    centre,
    nodes,
    overflow: Math.max(0, related.length - shown.length),
  };
}
