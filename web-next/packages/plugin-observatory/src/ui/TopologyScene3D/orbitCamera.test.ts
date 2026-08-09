import { describe, expect, it } from 'vitest';
import { CAMERA3D } from './scene3dConfig';
import {
  applyKeyNavigation,
  clampDistance,
  clampPolar,
  defaultOrbitCamera,
  dollyBy,
  driftCamera,
  easeOrbitCamera,
  eyePosition,
  fitOrbitCamera,
  focusOrbitCamera,
  orbitBy,
  panBy,
  screenBasis,
  viewDirection,
  worldUnitsPerPixel,
  type OrbitCamera,
} from './orbitCamera';
import { length, subtract, vec3, type Vec3 } from './vec3';

const VIEWPORT_H = 600;

function camera(overrides: Partial<OrbitCamera> = {}): OrbitCamera {
  return { ...defaultOrbitCamera(), ...overrides };
}

describe('clamping', () => {
  it('keeps the eye off the floor and out of the zenith', () => {
    expect(clampPolar(-5)).toBe(CAMERA3D.POLAR_MIN);
    expect(clampPolar(Math.PI)).toBe(CAMERA3D.POLAR_MAX);
    expect(clampPolar(1)).toBe(1);
  });

  it('keeps the eye within reach', () => {
    expect(clampDistance(0)).toBe(CAMERA3D.MIN_DISTANCE);
    expect(clampDistance(1e9)).toBe(CAMERA3D.MAX_DISTANCE);
  });
});

describe('eyePosition', () => {
  it('sits the eye the stated distance from what it looks at', () => {
    const cam = camera({ target: vec3(100, 20, -40) });
    expect(length(subtract(eyePosition(cam), cam.target))).toBeCloseTo(cam.distance, 6);
  });

  it('looks down at the target from above while the polar angle is acute', () => {
    const cam = camera({ polar: 0.6 });
    expect(eyePosition(cam).y).toBeGreaterThan(cam.target.y);
    expect(viewDirection(cam).y).toBeLessThan(0);
  });
});

describe('screenBasis', () => {
  it('produces an orthonormal right/up pair square to the view', () => {
    const { right, up } = screenBasis(camera({ azimuth: 1.1, polar: 0.9 }));
    expect(length(right)).toBeCloseTo(1, 6);
    expect(length(up)).toBeCloseTo(1, 6);
    expect(right.x * up.x + right.y * up.y + right.z * up.z).toBeCloseTo(0, 6);
  });

  it('keeps right horizontal, so panning never rolls the estate', () => {
    expect(screenBasis(camera({ polar: 0.3 })).right.y).toBeCloseTo(0, 6);
  });
});

describe('orbitBy', () => {
  it('turns on horizontal drag and tilts on vertical drag', () => {
    const start = camera();
    const turned = orbitBy(start, 100, 0);
    expect(turned.azimuth).toBeLessThan(start.azimuth);
    expect(turned.polar).toBe(start.polar);

    const tilted = orbitBy(start, 0, 100);
    expect(tilted.polar).toBeLessThan(start.polar);
  });

  it('will not tip past the floor however far you drag', () => {
    expect(orbitBy(camera(), 0, -100000).polar).toBe(CAMERA3D.POLAR_MAX);
    expect(orbitBy(camera(), 0, 100000).polar).toBe(CAMERA3D.POLAR_MIN);
  });

  it('leaves the target and distance alone', () => {
    const start = camera({ target: vec3(5, 6, 7) });
    const turned = orbitBy(start, 40, 20);
    expect(turned.target).toEqual(start.target);
    expect(turned.distance).toBe(start.distance);
  });
});

describe('panBy', () => {
  it('moves what the camera looks at along its own screen axes', () => {
    const start = camera({ azimuth: 0, polar: 1.2 });
    const panned = panBy(start, 50, 0, VIEWPORT_H);
    const { right } = screenBasis(start);
    const shift = subtract(panned.target, start.target);
    // Dragging right pulls the world left, so the target travels against the
    // camera's right axis.
    expect(shift.x * right.x + shift.y * right.y + shift.z * right.z).toBeLessThan(0);
  });

  it('covers more world per pixel the further back the camera sits', () => {
    const near = panBy(camera({ distance: 200 }), 100, 0, VIEWPORT_H);
    const far = panBy(camera({ distance: 4000 }), 100, 0, VIEWPORT_H);
    expect(length(subtract(far.target, vec3(0, 0, 0)))).toBeGreaterThan(
      length(subtract(near.target, vec3(0, 0, 0))),
    );
  });

  it('does nothing when the viewport has no height to measure against', () => {
    const start = camera();
    expect(panBy(start, 50, 50, 0)).toBe(start);
    expect(worldUnitsPerPixel(start, 0)).toBe(0);
  });
});

describe('dollyBy', () => {
  it('pulls back above one and pushes in below it', () => {
    expect(dollyBy(camera({ distance: 1000 }), 2).distance).toBe(2000);
    expect(dollyBy(camera({ distance: 1000 }), 0.5).distance).toBe(500);
  });

  it('stops at the limits', () => {
    expect(dollyBy(camera(), 1e6).distance).toBe(CAMERA3D.MAX_DISTANCE);
    expect(dollyBy(camera(), 1e-6).distance).toBe(CAMERA3D.MIN_DISTANCE);
  });
});

describe('applyKeyNavigation', () => {
  it('turns on the arrows', () => {
    const start = camera();
    expect(applyKeyNavigation(start, 'ArrowLeft', false, VIEWPORT_H).azimuth).toBeGreaterThan(
      start.azimuth,
    );
    expect(applyKeyNavigation(start, 'ArrowRight', false, VIEWPORT_H).azimuth).toBeLessThan(
      start.azimuth,
    );
    expect(applyKeyNavigation(start, 'ArrowUp', false, VIEWPORT_H).polar).toBeGreaterThan(
      start.polar,
    );
    expect(applyKeyNavigation(start, 'ArrowDown', false, VIEWPORT_H).polar).toBeLessThan(
      start.polar,
    );
  });

  it('slides when shift is held, leaving the angle untouched', () => {
    const start = camera();
    for (const key of ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown']) {
      const next = applyKeyNavigation(start, key, true, VIEWPORT_H);
      expect(next.azimuth).toBe(start.azimuth);
      expect(next.polar).toBe(start.polar);
      expect(next.target).not.toEqual(start.target);
    }
  });

  it('ignores keys it does not own', () => {
    const start = camera();
    expect(applyKeyNavigation(start, 'Enter', false, VIEWPORT_H)).toBe(start);
  });
});

describe('driftCamera', () => {
  it('turns slowly, and only as far as the frame it was given', () => {
    // Frame-length deltas, below the stall cap the next test covers.
    const start = camera();
    const oneFrame = driftCamera(start, 0.1, 0);
    const twoFrames = driftCamera(start, 0.2, 0);
    expect(oneFrame.azimuth).toBeGreaterThan(start.azimuth);
    expect(twoFrames.azimuth - start.azimuth).toBeCloseTo(
      (oneFrame.azimuth - start.azimuth) * 2,
      9,
    );
  });

  it('does not lurch when a stalled tab hands back an enormous frame', () => {
    // A drift that honoured a ten-second delta would spin the estate a third
    // of a turn the instant a background tab came forward.
    const start = camera();
    const resumed = driftCamera(start, 10, 0);
    const capped = driftCamera(start, 0.25, 0);
    expect(resumed.azimuth).toBeCloseTo(capped.azimuth, 9);
  });

  it('ignores a delta that runs backwards', () => {
    const start = camera();
    expect(driftCamera(start, -5, 0).azimuth).toBe(start.azimuth);
  });

  it('sways around wherever the camera was resting, not around a fixed origin', () => {
    // Half a sway period apart, the two nudges pull opposite ways.
    const start = camera({ polar: 1 });
    const rising = driftCamera(start, 1, 0).polar;
    const falling = driftCamera(start, 1, CAMERA3D.IDLE_SWAY_PERIOD_MS / 2).polar;
    expect(rising).toBeGreaterThan(start.polar);
    expect(falling).toBeLessThan(start.polar);
  });

  it('will not drift the eye through the floor', () => {
    let drifting = camera({ polar: CAMERA3D.POLAR_MAX });
    for (let frame = 0; frame < 200; frame += 1) drifting = driftCamera(drifting, 0.1, 0);
    expect(drifting.polar).toBeLessThanOrEqual(CAMERA3D.POLAR_MAX);
    expect(drifting.polar).toBeGreaterThanOrEqual(CAMERA3D.POLAR_MIN);
  });
});

describe('fitOrbitCamera', () => {
  /** The eight corners of a box, as the point set the estate would supply. */
  function corners(min: [number, number, number], max: [number, number, number]): Vec3[] {
    const points: Vec3[] = [];
    for (const x of [min[0], max[0]]) {
      for (const y of [min[1], max[1]]) {
        for (const z of [min[2], max[2]]) points.push(vec3(x, y, z));
      }
    }
    return points;
  }

  it('falls back to the default framing when there is nothing to frame', () => {
    expect(fitOrbitCamera([], 1.5)).toEqual(defaultOrbitCamera());
  });

  it('frames a single point without dividing by nothing', () => {
    const fitted = fitOrbitCamera([vec3(10, 20, 30)], 1.5);
    expect(fitted.target).toEqual({ x: 10, y: 20, z: 30 });
    // Nothing has any extent, so the fit bottoms out at how close the eye is
    // allowed to come rather than at zero.
    expect(fitted.distance).toBeCloseTo(CAMERA3D.MIN_DISTANCE * CAMERA3D.FIT_PADDING, 6);
  });

  it('looks at the middle of what it is framing and keeps the chosen angle', () => {
    const from = camera({ azimuth: 0.7, polar: 1.1 });
    const fitted = fitOrbitCamera(corners([-100, 0, -200], [300, 80, 400]), 1.5, from);
    expect(fitted.target).toEqual({ x: 100, y: 40, z: 100 });
    expect(fitted.azimuth).toBe(from.azimuth);
    expect(fitted.polar).toBe(from.polar);
  });

  it('centres on the space the estate spans, not on where it is crowded', () => {
    // Forty things at one end and one at the other is a normal estate: a mean
    // would sit at roughly -980 and push the lone realm off the far edge of
    // the window. The midpoint of the extremes frames both.
    const crowded: Vec3[] = [vec3(1000, 0, 0)];
    for (let i = 0; i < 40; i += 1) crowded.push(vec3(-1000 + i, 0, 0));
    expect(fitOrbitCamera(crowded, 1.5).target.x).toBeCloseTo(0, 6);
  });

  it('stands further back for a bigger estate', () => {
    const small = fitOrbitCamera(corners([-100, 0, -100], [100, 50, 100]), 1.5);
    const large = fitOrbitCamera(corners([-2000, 0, -2000], [2000, 500, 2000]), 1.5);
    expect(large.distance).toBeGreaterThan(small.distance);
  });

  it('actually contains the estate — every point falls inside the frustum', () => {
    // The regression this guards cuts both ways: framed too loosely the estate
    // arrives as a smudge in the middle of the window, and framed too tightly
    // its edges are cut off. Only checking the frustum catches both.
    const aspect = 1.6;
    const points = corners([-1500, -150, -900], [1500, 460, 900]);
    const fitted = fitOrbitCamera(points, aspect, camera({ azimuth: -0.9, polar: 1.0 }));
    const eye = eyePosition(fitted);
    const forward = viewDirection(fitted);
    const { right, up } = screenBasis(fitted);
    const tanVertical = Math.tan((CAMERA3D.FOV * Math.PI) / 360);
    const tanHorizontal = tanVertical * aspect;

    for (const point of points) {
      const offset = subtract(point, eye);
      const depth = offset.x * forward.x + offset.y * forward.y + offset.z * forward.z;
      const lateral = Math.abs(offset.x * right.x + offset.y * right.y + offset.z * right.z);
      const vertical = Math.abs(offset.x * up.x + offset.y * up.y + offset.z * up.z);
      expect(depth).toBeGreaterThan(0);
      expect(lateral).toBeLessThanOrEqual(depth * tanHorizontal + 1e-6);
      expect(vertical).toBeLessThanOrEqual(depth * tanVertical + 1e-6);
    }
  });

  it('stands closer than a bounding-sphere fit would, for a wide flat estate', () => {
    // A plan two thousand units across with five hundred of decks on top of it
    // is exactly the shape a sphere fit handles worst.
    const points = corners([-1500, 0, -900], [1500, 460, 900]);
    const spread = Math.hypot(3000, 460, 1800) / 2;
    const sphereFit = spread / Math.sin((CAMERA3D.FOV * Math.PI) / 360);
    expect(fitOrbitCamera(points, 1.6).distance).toBeLessThan(sphereFit);
  });
});

describe('focusOrbitCamera', () => {
  it('travels to the point without pulling further back than it already is', () => {
    const close = camera({ distance: 200 });
    expect(focusOrbitCamera(close, vec3(1, 2, 3))).toEqual({
      ...close,
      target: { x: 1, y: 2, z: 3 },
      distance: 200,
    });
    expect(focusOrbitCamera(camera({ distance: 9000 }), vec3(0, 0, 0)).distance).toBe(
      CAMERA3D.FOCUS_DISTANCE,
    );
  });
});

describe('easeOrbitCamera', () => {
  it('closes the gap without overshooting, and reports arrival', () => {
    const destination = camera({ target: vec3(500, 100, -300), distance: 400 });
    let current = camera();
    let arrived = false;

    for (let frame = 0; frame < 400 && !arrived; frame += 1) {
      const step = easeOrbitCamera(current, destination);
      current = step.camera;
      arrived = step.arrived;
    }

    expect(arrived).toBe(true);
    expect(current).toEqual(destination);
  });

  it('snaps exactly onto the destination once inside the settle radius', () => {
    const destination = camera({ target: vec3(0, 0, 0), distance: 400 });
    const nearly = camera({ target: vec3(0.2, 0, 0), distance: 400.1 });
    expect(easeOrbitCamera(nearly, destination, 0.5)).toEqual({
      camera: destination,
      arrived: true,
    });
  });
});
