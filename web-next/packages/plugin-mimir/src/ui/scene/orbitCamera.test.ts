import { describe, it, expect } from 'vitest';
import {
  defaultOrbitCamera,
  lockTo2D,
  eyePosition,
  viewDirection,
  orbitBy,
  panBy,
  dollyBy,
  fitOrbitCamera,
  focusOrbitCamera,
  easeOrbitCamera,
  clampPolar,
  clampDistance,
  worldUnitsPerPixel,
} from './orbitCamera';
import { length } from './vec3';
import { CAMERA2D, CAMERA3D } from './scene3dConfig';

describe('defaultOrbitCamera', () => {
  it('starts at the origin with the configured distance', () => {
    const camera = defaultOrbitCamera();
    expect(camera.target).toEqual({ x: 0, y: 0, z: 0 });
    expect(camera.distance).toBe(CAMERA3D.INITIAL_DISTANCE);
  });
});

describe('lockTo2D', () => {
  it('sets azimuth and polar to the 2D config, keeping target and distance', () => {
    const camera = { target: { x: 1, y: 2, z: 3 }, distance: 50, azimuth: 1, polar: 1 };
    const locked = lockTo2D(camera);
    expect(locked.azimuth).toBe(CAMERA2D.AZIMUTH);
    expect(locked.polar).toBe(CAMERA2D.POLAR);
    expect(locked.target).toEqual(camera.target);
    expect(locked.distance).toBe(camera.distance);
  });

  it('looks straight down in the locked view', () => {
    const locked = lockTo2D(defaultOrbitCamera());
    const dir = viewDirection(locked);
    expect(dir.y).toBeLessThan(-0.99);
  });
});

describe('clamping', () => {
  it('clamps polar within bounds', () => {
    expect(clampPolar(-10)).toBeCloseTo(CAMERA3D.POLAR_MIN);
    expect(clampPolar(10)).toBeCloseTo(CAMERA3D.POLAR_MAX);
    expect(clampPolar(1)).toBe(1);
  });

  it('clamps distance within bounds', () => {
    expect(clampDistance(-10)).toBe(CAMERA3D.MIN_DISTANCE);
    expect(clampDistance(1e6)).toBe(CAMERA3D.MAX_DISTANCE);
  });
});

describe('eyePosition / viewDirection', () => {
  it('places the eye at `distance` from the target', () => {
    const camera = defaultOrbitCamera();
    const eye = eyePosition(camera);
    const dx = eye.x - camera.target.x;
    const dy = eye.y - camera.target.y;
    const dz = eye.z - camera.target.z;
    expect(Math.sqrt(dx * dx + dy * dy + dz * dz)).toBeCloseTo(camera.distance);
  });

  it('view direction is a unit vector pointing from eye to target', () => {
    const camera = defaultOrbitCamera();
    expect(length(viewDirection(camera))).toBeCloseTo(1);
  });
});

describe('orbitBy', () => {
  it('changes azimuth and polar proportionally to drag', () => {
    const camera = defaultOrbitCamera();
    const next = orbitBy(camera, 100, 0);
    expect(next.azimuth).not.toBe(camera.azimuth);
    expect(next.polar).toBe(camera.polar);
  });

  it('clamps polar so the camera never flips past the poles', () => {
    const camera = { ...defaultOrbitCamera(), polar: CAMERA3D.POLAR_MIN };
    const next = orbitBy(camera, 0, 100000);
    expect(next.polar).toBeGreaterThanOrEqual(CAMERA3D.POLAR_MIN);
  });
});

describe('worldUnitsPerPixel', () => {
  it('is zero for a zero-height viewport', () => {
    expect(worldUnitsPerPixel(defaultOrbitCamera(), 0)).toBe(0);
  });

  it('is positive for a normal viewport', () => {
    expect(worldUnitsPerPixel(defaultOrbitCamera(), 600)).toBeGreaterThan(0);
  });
});

describe('panBy', () => {
  it('moves the target when the viewport has height', () => {
    const camera = defaultOrbitCamera();
    const next = panBy(camera, 50, 0, 600);
    expect(next.target).not.toEqual(camera.target);
  });

  it('is a no-op for a zero-height viewport', () => {
    const camera = defaultOrbitCamera();
    expect(panBy(camera, 50, 0, 0)).toBe(camera);
  });
});

describe('dollyBy', () => {
  it('scales distance by the given factor, clamped', () => {
    const camera = defaultOrbitCamera();
    expect(dollyBy(camera, 2).distance).toBe(clampDistance(camera.distance * 2));
    expect(dollyBy(camera, 0.5).distance).toBe(clampDistance(camera.distance * 0.5));
  });
});

describe('fitOrbitCamera', () => {
  it('returns the default distance/target for an empty point set', () => {
    const fitted = fitOrbitCamera([], 1);
    expect(fitted.target).toEqual({ x: 0, y: 0, z: 0 });
    expect(fitted.distance).toBe(CAMERA3D.INITIAL_DISTANCE);
  });

  it('centres on the midpoint of the extremes', () => {
    const points = [
      { x: -10, y: 0, z: 0 },
      { x: 10, y: 0, z: 0 },
    ];
    const fitted = fitOrbitCamera(points, 1);
    expect(fitted.target.x).toBeCloseTo(0);
  });

  it('grows distance as the point spread grows', () => {
    const small = fitOrbitCamera(
      [
        { x: -1, y: 0, z: 0 },
        { x: 1, y: 0, z: 0 },
      ],
      1,
    );
    const large = fitOrbitCamera(
      [
        { x: -50, y: 0, z: 0 },
        { x: 50, y: 0, z: 0 },
      ],
      1,
    );
    expect(large.distance).toBeGreaterThan(small.distance);
  });

  it('never goes below the minimum distance', () => {
    const fitted = fitOrbitCamera([{ x: 0, y: 0, z: 0 }], 1);
    expect(fitted.distance).toBeGreaterThanOrEqual(CAMERA3D.MIN_DISTANCE);
  });
});

describe('focusOrbitCamera', () => {
  it('targets the given point and never zooms out past the current distance', () => {
    const camera = { ...defaultOrbitCamera(), distance: 10 };
    const focused = focusOrbitCamera(camera, { x: 5, y: 5, z: 5 });
    expect(focused.target).toEqual({ x: 5, y: 5, z: 5 });
    expect(focused.distance).toBeLessThanOrEqual(10);
  });

  it('zooms in toward FOCUS_DISTANCE when currently further out', () => {
    const camera = { ...defaultOrbitCamera(), distance: CAMERA3D.MAX_DISTANCE };
    const focused = focusOrbitCamera(camera, { x: 0, y: 0, z: 0 });
    expect(focused.distance).toBe(CAMERA3D.FOCUS_DISTANCE);
  });
});

describe('easeOrbitCamera', () => {
  it('moves partway toward the destination each frame', () => {
    const camera = defaultOrbitCamera();
    const destination = { ...camera, target: { x: 100, y: 0, z: 0 } };
    const { camera: next, arrived } = easeOrbitCamera(camera, destination);
    expect(next.target.x).toBeGreaterThan(0);
    expect(next.target.x).toBeLessThan(100);
    expect(arrived).toBe(false);
  });

  it('reports arrival and snaps exactly once within the settle distance', () => {
    const camera = defaultOrbitCamera();
    const destination = { ...camera, target: { x: 0.001, y: 0, z: 0 } };
    const { camera: next, arrived } = easeOrbitCamera(camera, destination);
    expect(arrived).toBe(true);
    expect(next).toEqual(destination);
  });

  it('converges to the destination over repeated frames', () => {
    let camera = defaultOrbitCamera();
    const destination = { target: { x: 20, y: 0, z: 0 }, distance: 40, azimuth: 1, polar: 1 };
    let arrived = false;
    for (let i = 0; i < 200 && !arrived; i += 1) {
      const step = easeOrbitCamera(camera, destination);
      camera = step.camera;
      arrived = step.arrived;
    }
    expect(arrived).toBe(true);
    expect(camera).toEqual(destination);
  });
});
