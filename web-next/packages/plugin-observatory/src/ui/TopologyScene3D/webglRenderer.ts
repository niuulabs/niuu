/**
 * The WebGL surface the 3D view draws on.
 *
 * Kept apart from the view because it is the one piece that cannot run without
 * a GPU: everything else — the layout, the camera, the scene graph, the
 * picking — is exercised in the test suite against an injected surface, and
 * this is what that surface stands in for.
 *
 * Deliberately plain. It draws the scene and nothing else.
 *
 * There was a bloom-and-tone-mapping chain here for a while, and both were
 * mistakes. Filmic tone mapping is built to roll photographic highlights off
 * toward white, which is precisely wrong for line-work: it took saturated cyan
 * and desaturated it toward grey, so every considered colour arrived on screen
 * as the same washed non-colour. Bloom then spread that grey over its
 * neighbours. Crisp lines at their stated colour need neither.
 */

import {
  NoToneMapping,
  SRGBColorSpace,
  WebGLRenderer,
  type Camera as ThreeCamera,
  type Scene as ThreeScene,
} from 'three';

import { CANVAS } from '../TopologyCanvas/config';

/** The surface `TopologyScene3D` draws through. */
export interface Scene3DRenderer {
  domElement: HTMLCanvasElement;
  setSize(width: number, height: number, updateStyle?: boolean): void;
  setPixelRatio(ratio: number): void;
  render(scene: ThreeScene, camera: ThreeCamera): void;
  dispose(): void;
}

export type Scene3DRendererFactory = () => Scene3DRenderer;

export function createWebGLRenderer(): Scene3DRenderer {
  const renderer = new WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
  // The plan's outer wash: what shows before the scene's backdrop is up.
  renderer.setClearColor(CANVAS.BACKDROP_EDGE, 1);
  // A colour that was chosen is a colour that should arrive.
  renderer.toneMapping = NoToneMapping;
  renderer.outputColorSpace = SRGBColorSpace;
  return renderer;
}

/**
 * Whether this browser can give us a 3D context at all.
 *
 * Asked before the scene is mounted rather than discovered by catching the
 * renderer's constructor, so the answer is available while rendering and the
 * operator gets a sentence explaining the empty stage instead of a black
 * rectangle that looks like an estate with nothing in it.
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
