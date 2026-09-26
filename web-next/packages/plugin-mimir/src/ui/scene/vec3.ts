/**
 * The small vector algebra the memory scene needs, on plain objects.
 *
 * Deliberately not three.js `Vector3`: layout, camera and picking are pure
 * geometry, and keeping it on plain data means it can be tested without a
 * renderer.
 *
 * (This mirrors a similarly small, generic `vec3.ts` written independently
 * for plugin-observatory's 3D topology view. Plugins must not import from
 * each other, so this is its own copy rather than a shared import — see the
 * report for a promotion note.)
 */

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

export interface Bounds3 {
  min: Vec3;
  max: Vec3;
}

export function vec3(x: number, y: number, z: number): Vec3 {
  return { x, y, z };
}

export function add(a: Vec3, b: Vec3): Vec3 {
  return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z };
}

export function subtract(a: Vec3, b: Vec3): Vec3 {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
}

export function scale(a: Vec3, k: number): Vec3 {
  return { x: a.x * k, y: a.y * k, z: a.z * k };
}

export function length(a: Vec3): number {
  return Math.sqrt(a.x * a.x + a.y * a.y + a.z * a.z);
}

export function distance(a: Vec3, b: Vec3): number {
  return length(subtract(a, b));
}

/** Unit vector, or the zero vector when there is no direction to normalise. */
export function normalize(a: Vec3): Vec3 {
  const len = length(a);
  if (len === 0) return { x: 0, y: 0, z: 0 };
  return scale(a, 1 / len);
}

export function cross(a: Vec3, b: Vec3): Vec3 {
  return {
    x: a.y * b.z - a.z * b.y,
    y: a.z * b.x - a.x * b.z,
    z: a.x * b.y - a.y * b.x,
  };
}

export function lerp(a: Vec3, b: Vec3, t: number): Vec3 {
  return {
    x: a.x + (b.x - a.x) * t,
    y: a.y + (b.y - a.y) * t,
    z: a.z + (b.z - a.z) * t,
  };
}

/** An empty box, ready to be grown by `growBounds`. */
export function emptyBounds(): Bounds3 {
  return {
    min: { x: Infinity, y: Infinity, z: Infinity },
    max: { x: -Infinity, y: -Infinity, z: -Infinity },
  };
}

/** Grow `bounds` in place to contain `point`, optionally padded by `radius`. */
export function growBounds(bounds: Bounds3, point: Vec3, radius = 0): void {
  bounds.min.x = Math.min(bounds.min.x, point.x - radius);
  bounds.min.y = Math.min(bounds.min.y, point.y - radius);
  bounds.min.z = Math.min(bounds.min.z, point.z - radius);
  bounds.max.x = Math.max(bounds.max.x, point.x + radius);
  bounds.max.y = Math.max(bounds.max.y, point.y + radius);
  bounds.max.z = Math.max(bounds.max.z, point.z + radius);
}

/** True when nothing has been added to the box yet. */
export function isEmptyBounds(bounds: Bounds3): boolean {
  return !Number.isFinite(bounds.min.x) || !Number.isFinite(bounds.max.x);
}

export function boundsCentre(bounds: Bounds3): Vec3 {
  if (isEmptyBounds(bounds)) return { x: 0, y: 0, z: 0 };
  return {
    x: (bounds.min.x + bounds.max.x) / 2,
    y: (bounds.min.y + bounds.max.y) / 2,
    z: (bounds.min.z + bounds.max.z) / 2,
  };
}
