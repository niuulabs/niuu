/**
 * The WebGL surface the memory scene draws on.
 *
 * Kept apart from the view because it is the one piece that cannot run
 * without a GPU: everything else — layout, camera, colour, visibility,
 * picking — is plain data and functions, exercised in the test suite
 * against an injected surface. `MemoryScene` never calls the real WebGL
 * renderer directly; it always goes through `createRenderer` (a prop that
 * defaults to `createWebGLRenderer`), so a fake can stand in for tests.
 */

import { NoToneMapping, SRGBColorSpace, WebGLRenderer } from 'three';
import type { SceneRenderer, SceneRendererFactory } from './types';

// The contract's `SceneRenderer` types `render(scene, camera)` as
// `(unknown, unknown)` so `types.ts` never has to import three.js. Re-export
// under this module's own names so the rest of the scene can talk about
// "the WebGL surface" without reaching back into the contract file for it.
export type { SceneRenderer as Scene3DRenderer, SceneRendererFactory as Scene3DRendererFactory };

export function createWebGLRenderer(): SceneRenderer {
  const renderer = new WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
  // A colour that was chosen for a node or edge is a colour that should
  // arrive on screen — no filmic tone mapping desaturating the palette.
  renderer.toneMapping = NoToneMapping;
  renderer.outputColorSpace = SRGBColorSpace;
  return renderer;
}

/**
 * Whether this browser can give us a 3D context at all.
 *
 * Asked before the scene is mounted rather than discovered by catching the
 * renderer's constructor, so `MemoryScene` can render an accessible message
 * explaining the empty stage instead of a black rectangle.
 */
export function supportsWebGL(): boolean {
  if (typeof document === 'undefined') return false;
  try {
    const probe = document.createElement('canvas');
    return Boolean(probe.getContext('webgl2') ?? probe.getContext('webgl'));
  } catch {
    return false;
  }
}
