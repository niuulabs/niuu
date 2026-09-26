/**
 * Orbit camera math — pure functions over a plain camera record.
 *
 * The camera is stored as a point it looks at plus a direction and a
 * distance, rather than a position and a rotation. That is what makes "fly
 * to the node I just focused" and "frame everything" one-liners: both are a
 * change of target and distance with the viewing angle left alone.
 *
 * Nothing here touches three.js or the DOM, so every gesture the scene
 * supports can be exercised without a renderer.
 */

import { CAMERA2D, CAMERA3D } from './scene3dConfig';
import {
  add,
  cross,
  distance,
  emptyBounds,
  growBounds,
  normalize,
  scale,
  subtract,
  boundsCentre,
  type Vec3,
} from './vec3';

export interface OrbitCamera {
  /** The world point the camera looks at, and orbits around. */
  target: Vec3;
  /** Eye distance from the target. */
  distance: number;
  /** Rotation about the world Y axis, in radians. */
  azimuth: number;
  /** Angle down from straight up, in radians. */
  polar: number;
}

/** World up. */
export const WORLD_UP: Vec3 = { x: 0, y: 1, z: 0 };

export function clampPolar(polar: number): number {
  return Math.max(CAMERA3D.POLAR_MIN, Math.min(CAMERA3D.POLAR_MAX, polar));
}

export function clampDistance(value: number): number {
  return Math.max(CAMERA3D.MIN_DISTANCE, Math.min(CAMERA3D.MAX_DISTANCE, value));
}

export function defaultOrbitCamera(): OrbitCamera {
  return {
    target: { x: 0, y: 0, z: 0 },
    distance: CAMERA3D.INITIAL_DISTANCE,
    azimuth: CAMERA3D.INITIAL_AZIMUTH,
    polar: CAMERA3D.INITIAL_POLAR,
  };
}

/** The 2D view: same model, looking straight down with rotation locked. */
export function lockTo2D(camera: OrbitCamera): OrbitCamera {
  return { ...camera, azimuth: CAMERA2D.AZIMUTH, polar: CAMERA2D.POLAR };
}

/** Where the eye actually is, given what it is looking at and from where. */
export function eyePosition(camera: OrbitCamera): Vec3 {
  const sinPolar = Math.sin(camera.polar);
  return add(camera.target, {
    x: camera.distance * sinPolar * Math.sin(camera.azimuth),
    y: camera.distance * Math.cos(camera.polar),
    z: camera.distance * sinPolar * Math.cos(camera.azimuth),
  });
}

/** Unit vector from the eye toward the target. */
export function viewDirection(camera: OrbitCamera): Vec3 {
  return normalize(subtract(camera.target, eyePosition(camera)));
}

/** The camera's own right and up axes, for screen-relative panning. */
export function screenBasis(camera: OrbitCamera): { right: Vec3; up: Vec3 } {
  const forward = viewDirection(camera);
  const right = normalize(cross(forward, WORLD_UP));
  const up = normalize(cross(right, forward));
  return { right, up };
}

/** Drag to turn. Vertical drag raises the eye; horizontal swings it round. */
export function orbitBy(camera: OrbitCamera, dxPixels: number, dyPixels: number): OrbitCamera {
  return {
    ...camera,
    azimuth: camera.azimuth - dxPixels * CAMERA3D.ORBIT_PER_PX,
    polar: clampPolar(camera.polar - dyPixels * CAMERA3D.ORBIT_PER_PX),
  };
}

/** World units one screen pixel covers at the target's depth. */
export function worldUnitsPerPixel(camera: OrbitCamera, viewportHeight: number): number {
  if (viewportHeight <= 0) return 0;
  return (2 * camera.distance * Math.tan((CAMERA3D.FOV * Math.PI) / 360)) / viewportHeight;
}

/** Drag to slide. Moves what the camera looks at, keeping the angle. */
export function panBy(
  camera: OrbitCamera,
  dxPixels: number,
  dyPixels: number,
  viewportHeight: number,
): OrbitCamera {
  const perPixel = worldUnitsPerPixel(camera, viewportHeight);
  if (perPixel === 0) return camera;
  const { right, up } = screenBasis(camera);
  const shift = add(scale(right, -dxPixels * perPixel), scale(up, dyPixels * perPixel));
  return { ...camera, target: add(camera.target, shift) };
}

/** Wheel or button. Values above 1 pull back, below 1 push in. */
export function dollyBy(camera: OrbitCamera, factor: number): OrbitCamera {
  return { ...camera, distance: clampDistance(camera.distance * factor) };
}

/**
 * Frame every point, keeping the current viewing angle.
 *
 * Solves for the nearest the eye can stand while every point still falls
 * inside the frustum — points rather than a bounding sphere, since a sphere
 * sized to a box's diagonal over-frames anything not roughly cube-shaped.
 */
export function fitOrbitCamera(
  points: readonly Vec3[],
  aspect: number,
  from: OrbitCamera = defaultOrbitCamera(),
): OrbitCamera {
  if (points.length === 0) {
    return { ...from, target: { x: 0, y: 0, z: 0 }, distance: CAMERA3D.INITIAL_DISTANCE };
  }

  const centre = centroidOfExtremes(points);
  const framing: OrbitCamera = { ...from, target: centre };
  const forward = viewDirection(framing);
  const { right, up } = screenBasis(framing);

  const tanVertical = Math.tan((CAMERA3D.FOV * Math.PI) / 360);
  const tanHorizontal = tanVertical * Math.max(aspect, 0.01);

  let required: number = CAMERA3D.MIN_DISTANCE;
  for (const point of points) {
    const offset = subtract(point, centre);
    const depth = offset.x * forward.x + offset.y * forward.y + offset.z * forward.z;
    const lateral = Math.abs(offset.x * right.x + offset.y * right.y + offset.z * right.z);
    const vertical = Math.abs(offset.x * up.x + offset.y * up.y + offset.z * up.z);
    required = Math.max(required, lateral / tanHorizontal - depth, vertical / tanVertical - depth);
  }

  return { ...framing, distance: clampDistance(required * CAMERA3D.FIT_PADDING) };
}

function centroidOfExtremes(points: readonly Vec3[]): Vec3 {
  const bounds = emptyBounds();
  for (const point of points) growBounds(bounds, point);
  return boundsCentre(bounds);
}

/** Where the camera should end up when the operator focuses a node. */
export function focusOrbitCamera(camera: OrbitCamera, point: Vec3): OrbitCamera {
  return {
    ...camera,
    target: point,
    distance: Math.min(camera.distance, CAMERA3D.FOCUS_DISTANCE),
  };
}

/** One frame of eased travel toward a destination. Reports `arrived` so the caller can stop steering. */
export function easeOrbitCamera(
  camera: OrbitCamera,
  destination: OrbitCamera,
  t: number = CAMERA3D.FOCUS_EASING,
): { camera: OrbitCamera; arrived: boolean } {
  const next: OrbitCamera = {
    target: {
      x: camera.target.x + (destination.target.x - camera.target.x) * t,
      y: camera.target.y + (destination.target.y - camera.target.y) * t,
      z: camera.target.z + (destination.target.z - camera.target.z) * t,
    },
    distance: camera.distance + (destination.distance - camera.distance) * t,
    azimuth: camera.azimuth + (destination.azimuth - camera.azimuth) * t,
    polar: clampPolar(camera.polar + (destination.polar - camera.polar) * t),
  };

  const arrived =
    distance(next.target, destination.target) < CAMERA3D.FOCUS_SETTLE_DISTANCE &&
    Math.abs(next.distance - destination.distance) < CAMERA3D.FOCUS_SETTLE_DISTANCE;

  return { camera: arrived ? destination : next, arrived };
}
