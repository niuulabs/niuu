/**
 * Which names actually get drawn.
 *
 * Distance alone is not enough. Two agents a metre apart in a rack are both
 * "near", so both label, and at overview their names land on top of each other
 * — and the estate ends up wearing a drift of overlapping text that says less
 * than no text at all would.
 *
 * So the last word belongs to the screen: a name is drawn if there is room for
 * it where it would land, and skipped if something more important is already
 * there. This is the same rule a map uses, and it is why a map stays readable
 * at every zoom without anyone tuning a threshold per continent.
 */

/** A name asking for room, in screen pixels. */
export interface LabelCandidate {
  id: string;
  /** Where the name would sit, in screen pixels from the top-left. */
  x: number;
  y: number;
  width: number;
  height: number;
  /**
   * Who yields to whom. Higher wins the space.
   *
   * What the operator is pointing at or has selected outranks everything, then
   * regions, then the entities that carry the story, then distance.
   */
  priority: number;
}

/**
 * Choose the names that fit.
 *
 * Greedy by priority: the highest-priority name takes its space, and every
 * later one is drawn only if it does not touch a name already placed. Greedy
 * is the right trade here — an optimal packing would let a cluster of small
 * names displace an important one, which is precisely backwards.
 */
export function placeLabels(
  candidates: readonly LabelCandidate[],
  maxLive: number,
  padding = 0,
): Set<string> {
  const placed: LabelCandidate[] = [];
  const chosen = new Set<string>();

  const ordered = [...candidates].sort((a, b) => b.priority - a.priority);

  for (const candidate of ordered) {
    if (chosen.size >= maxLive) break;
    if (placed.some((taken) => overlaps(taken, candidate, padding))) continue;
    placed.push(candidate);
    chosen.add(candidate.id);
  }

  return chosen;
}

/** Whether two names would touch, once each is given its clear air. */
export function overlaps(a: LabelCandidate, b: LabelCandidate, padding = 0): boolean {
  const gapX = Math.abs(a.x - b.x) - (a.width + b.width) / 2 - padding;
  const gapY = Math.abs(a.y - b.y) - (a.height + b.height) / 2 - padding;
  return gapX < 0 && gapY < 0;
}
