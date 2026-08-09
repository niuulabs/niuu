/**
 * Orbit camera math — pure functions over a plain camera record.
 *
 * The camera is stored as a point it looks at plus a direction and a distance,
 * rather than as a position and a rotation. That is what makes "fly to the
 * thing I just selected" and "frame the whole estate" one-liners: both are a
 * change of target and distance with the viewing direction left alone, so the
 * operator keeps the angle they had chosen.
 *
 * Nothing here touches three.js or the DOM, so every gesture the view supports
 * can be exercised without a renderer.
 */

import { CAMERA3D } from './scene3dConfig';
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

/** World up. The estate is laid out in decks, so up never tilts. */
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

/**
 * The camera's own right and up axes.
 *
 * Panning has to move the target across the screen, not across the world, or
 * dragging left after orbiting halfway round sends the estate the wrong way.
 */
export function screenBasis(camera: OrbitCamera): { right: Vec3; up: Vec3 } {
  const forward = viewDirection(camera);
  const right = normalize(cross(forward, WORLD_UP));
  // Degenerate only when looking straight down, which `clampPolar` prevents.
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

/**
 * World units one screen pixel covers at the target's depth.
 *
 * Panning that ignores this feels like a different gesture at every distance:
 * the same drag crawls when framing the estate and flings when inspecting one
 * host.
 */
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

/** Keyboard navigation: arrows orbit, shift+arrows pan. */
export function applyKeyNavigation(
  camera: OrbitCamera,
  key: string,
  shift: boolean,
  viewportHeight: number,
): OrbitCamera {
  const orbitStep = CAMERA3D.KEY_ORBIT_STEP / CAMERA3D.ORBIT_PER_PX;
  const panStep =
    CAMERA3D.KEY_PAN_STEP / Math.max(worldUnitsPerPixel(camera, viewportHeight), 1e-6);

  switch (key) {
    case 'ArrowLeft':
      return shift ? panBy(camera, -panStep, 0, viewportHeight) : orbitBy(camera, -orbitStep, 0);
    case 'ArrowRight':
      return shift ? panBy(camera, panStep, 0, viewportHeight) : orbitBy(camera, orbitStep, 0);
    case 'ArrowUp':
      return shift ? panBy(camera, 0, -panStep, viewportHeight) : orbitBy(camera, 0, -orbitStep);
    case 'ArrowDown':
      return shift ? panBy(camera, 0, panStep, viewportHeight) : orbitBy(camera, 0, orbitStep);
    default:
      return camera;
  }
}

/**
 * One frame of the idle drift.
 *
 * Stateless: the sway is the integral of a cosine, so it rises and falls around
 * wherever the camera happened to be resting rather than around a remembered
 * origin the operator never chose. Handing it the clock and the frame's own
 * delta keeps the speed the same whether the tab is running at 120fps or has
 * been throttled to a crawl in a background window.
 */
export function driftCamera(camera: OrbitCamera, deltaSeconds: number, now: number): OrbitCamera {
  // A stalled tab hands back an enormous first delta on resume; a drift that
  // honoured it would spin the estate half a turn in one frame.
  const step = Math.max(0, Math.min(deltaSeconds, 0.25));
  return {
    ...camera,
    azimuth: camera.azimuth + CAMERA3D.IDLE_AZIMUTH_PER_SECOND * step,
    polar: clampPolar(
      camera.polar +
        CAMERA3D.IDLE_POLAR_SWAY *
          Math.cos((now / CAMERA3D.IDLE_SWAY_PERIOD_MS) * Math.PI * 2) *
          step,
    ),
  };
}

/**
 * Frame everything, keeping the current viewing angle.
 *
 * Given the points the estate actually occupies, this solves for the nearest
 * the eye can stand while every one of them still falls inside the frustum.
 *
 * Points rather than a bounding box, and an exact solve rather than a
 * bounding sphere, because both shortcuts fail in the same direction and the
 * errors compound. A box adds four corners the estate does not reach into —
 * for a wide plan with a tall stack of decks, that is most of the volume — and
 * a sphere then sizes itself to that box's diagonal. Framed that way the
 * estate arrives occupying a third of the window with sky on every side.
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
    // Depth along the view axis: a point beyond the centre is further away and
    // needs less room than one in front of it.
    const depth = offset.x * forward.x + offset.y * forward.y + offset.z * forward.z;
    const lateral = Math.abs(offset.x * right.x + offset.y * right.y + offset.z * right.z);
    const vertical = Math.abs(offset.x * up.x + offset.y * up.y + offset.z * up.z);
    required = Math.max(required, lateral / tanHorizontal - depth, vertical / tanVertical - depth);
  }

  return { ...framing, distance: clampDistance(required * CAMERA3D.FIT_PADDING) };
}

/**
 * The middle of the space the points span.
 *
 * The midpoint of their extremes rather than their mean: the mean is pulled
 * toward wherever the estate happens to be dense, which puts a realm holding
 * forty agents in the centre of the window and a realm holding two off the
 * edge of it.
 */
function centroidOfExtremes(points: readonly Vec3[]): Vec3 {
  const bounds = emptyBounds();
  for (const point of points) growBounds(bounds, point);
  return boundsCentre(bounds);
}

/** Where the camera should end up when the operator picks a node. */
export function focusOrbitCamera(camera: OrbitCamera, point: Vec3): OrbitCamera {
  return {
    ...camera,
    target: point,
    distance: Math.min(camera.distance, CAMERA3D.FOCUS_DISTANCE),
  };
}

/**
 * One frame of travel toward a destination.
 *
 * Eased rather than cut: this graph has no landmarks, and a jump costs the
 * operator any sense of where they were. Reports `arrived` so the caller can
 * stop steering and hand control back.
 */
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
