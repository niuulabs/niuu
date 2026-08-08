/**
 * The little vector algebra the 3D view needs, on plain objects.
 *
 * Deliberately not three.js `Vector3`: everything upstream of the scene graph
 * — elevation, camera, edge routing — is pure geometry, and keeping it on
 * plain data means it can be tested without a renderer and read without
 * knowing the library.
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

/** Point at `t` along the quadratic Bézier through `a`, `control`, `b`. */
export function quadraticBezier(a: Vec3, control: Vec3, b: Vec3, t: number): Vec3 {
  return lerp(lerp(a, control, t), lerp(control, b, t), t);
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

/** Half the box's longest diagonal — the radius of its bounding sphere. */
export function boundsRadius(bounds: Bounds3): number {
  if (isEmptyBounds(bounds)) return 0;
  return (
    length({
      x: bounds.max.x - bounds.min.x,
      y: bounds.max.y - bounds.min.y,
      z: bounds.max.z - bounds.min.z,
    }) / 2
  );
}
