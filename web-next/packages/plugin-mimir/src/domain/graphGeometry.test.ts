import { expect, it } from 'vitest';
import { BASE_VIEWBOX, clearForeground, projectPosition } from './graphGeometry';

it('clears close foreground nodes while keeping distant nodes and graph data stable', () => {
  const node = { id: 'near', title: 'Near', path: 'near', category: 'notes' };
  const front = projectPosition({ node, x: 550, y: 375, z: 250 }, 0, 0);
  const zoomed = { x: 450, y: 300, w: 200, h: 150 };
  expect(clearForeground(front, BASE_VIEWBOX)).toEqual(front);
  const behind = { ...front, depth: -1000 };
  expect(clearForeground(behind, zoomed)).toEqual(behind);
  const moved = clearForeground(front, zoomed);
  expect(Math.hypot(moved.x - 550, moved.y - 375)).toBeGreaterThan(20);
  expect(moved.node).toBe(node);
  expect(moved.depth).toBe(front.depth);
  expect(front.x).toBe(550);
  expect(clearForeground(front, zoomed)).toEqual(moved);
  const side = { ...front, x: 800 };
  expect(clearForeground(side, zoomed)).toEqual(side);
});

it('dollies through depth when zooming instead of uniformly magnifying nodes', () => {
  const node = { id: 'near', title: 'Near', path: 'near', category: 'notes' };
  const view = { x: 450, y: 300, w: 200, h: 150 };
  const front = projectPosition({ node, x: 560, y: 375, z: 250 }, 0, 0, view);
  const back = projectPosition({ node, x: 560, y: 375, z: -250 }, 0, 0, view);
  expect(front.scale).toBeGreaterThan(back.scale * 3);
  const atOrigin = projectPosition({ node, x: 550, y: 375, z: 0 }, 0, 0, view);
  const cleared = clearForeground(atOrigin, view);
  expect(Math.hypot(cleared.x - 550, cleared.y - 375)).toBeGreaterThan(40);
});
