/**
 * The instrument line-work a region wears.
 *
 * A region drawn as a shape says "something is here". A graduated ring around
 * it, with an arc filled to a figure the snapshot actually reports, says how
 * much is inside and whether it is well — which is the difference between
 * decoration and an instrument.
 *
 * Pure geometry builders: they take numbers and return vertex positions on the
 * unit circle, so the shapes they describe can be asserted rather than
 * eyeballed in a spinning canvas.
 */

/**
 * Tick marks around a unit circle, lying in the ground plane.
 *
 * Returned as line-segment pairs: every pair is one tick, running inward from
 * the circle. Majors are longer, so the ring reads as a scale rather than as
 * hatching.
 */
export function graduatedRing(
  ticks: number,
  majorEvery: number,
  minorLength: number,
  majorLength: number,
): Float32Array {
  const safeTicks = Math.max(1, Math.floor(ticks));
  const positions = new Float32Array(safeTicks * 6);

  for (let i = 0; i < safeTicks; i += 1) {
    const angle = (i / safeTicks) * Math.PI * 2;
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    const isMajor = majorEvery > 0 && i % majorEvery === 0;
    const inner = 1 - (isMajor ? majorLength : minorLength);
    positions.set([cos, 0, sin, cos * inner, 0, sin * inner], i * 6);
  }

  return positions;
}

/**
 * An arc of the unit circle, in the ground plane, as a line strip.
 *
 * `fraction` runs 0–1 and is clamped: a gauge that overruns its own dial is
 * worse than one that pins, because it reads as a smaller value.
 *
 * Starts at the far side and runs clockwise, so a gauge fills the way a clock
 * does from any camera that is looking down at it.
 */
export function arcRing(fraction: number, segments: number): Float32Array {
  const clamped = Math.max(0, Math.min(1, fraction));
  const steps = Math.max(1, Math.round(segments * clamped));
  const positions = new Float32Array((steps + 1) * 3);

  for (let i = 0; i <= steps; i += 1) {
    const angle = -Math.PI / 2 + (i / segments) * Math.PI * 2;
    positions.set([Math.cos(angle), 0, Math.sin(angle)], i * 3);
  }

  return positions;
}

/** A full circle in the ground plane, as a closed line loop. */
export function circleRing(segments: number): Float32Array {
  const steps = Math.max(3, Math.floor(segments));
  const positions = new Float32Array(steps * 3);
  for (let i = 0; i < steps; i += 1) {
    const angle = (i / steps) * Math.PI * 2;
    positions.set([Math.cos(angle), 0, Math.sin(angle)], i * 3);
  }
  return positions;
}
