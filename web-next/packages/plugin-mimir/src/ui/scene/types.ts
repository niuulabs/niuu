/**
 * The contract between the memory view's panels and its 3D scene.
 *
 * The scene is a controlled component: everything it shows is decided by the
 * page (focus, answers, replay time, markers) and handed down as props. The
 * scene owns only what cannot be state: the camera, the WebGL surface, the
 * per-frame animation, and the DOM labels it projects over the canvas.
 */

import type { MimirGraph } from '../../domain/api-types';
import type { KindGroup } from '../../domain/memoryKinds';

export type ColourBy = 'type' | 'proof' | 'age';

export type SceneView = '3d' | '2d';

export type FocusDepth = 1 | 2 | 3;

export interface SceneFocus {
  nodeId: string;
  /** Hops of neighbourhood kept lit around the focused page. */
  depth: FocusDepth;
}

/** A page that answered a question, badged with its number in the answer card. */
export interface SceneAnswer {
  nodeId: string;
  /** 1-based, matches the quoted fact's number. */
  n: number;
}

/** Someone reading or writing a page right now. */
export interface SceneMarker {
  id: string;
  nodeId: string;
  kind: 'read' | 'write';
  /** Actor shown on the marker, e.g. "muninn". Null when unauthenticated. */
  actor: string | null;
  /** ISO-8601 time of the event, for "2 min" on the marker. */
  timestamp: string;
}

/**
 * A question memory could not answer, drawn as a hollow "?" node beside the
 * page closest to it (or free-floating when nothing is close).
 */
export interface SceneQuestion {
  id: string;
  label: string;
  nearNodeId: string | null;
}

/** A camera instruction. `key` changes each time so the same target can be flown to twice. */
export type SceneCameraCommand =
  { kind: 'fit'; key: number } | { kind: 'fly-to'; nodeIds: string[]; key: number };

/** The WebGL surface; injected so the scene runs in tests without a GPU. */
export interface SceneRenderer {
  domElement: HTMLCanvasElement;
  setSize(width: number, height: number, updateStyle?: boolean): void;
  setPixelRatio(ratio: number): void;
  render(scene: unknown, camera: unknown): void;
  dispose(): void;
}

export type SceneRendererFactory = () => SceneRenderer;

export interface MemorySceneProps {
  graph: MimirGraph;
  colourBy: ColourBy;
  /** Legend groups switched off; their pages and links are not drawn. */
  hiddenGroups: ReadonlySet<KindGroup>;
  /** Whether unanswered-question nodes are drawn. */
  showQuestions: boolean;
  view: SceneView;
  focus: SceneFocus | null;
  /** Pages that answered the current question; empty when nothing is asked. */
  answers: readonly SceneAnswer[];
  /** Node ids along a traced path (shift-click), in order; null when none. */
  path: readonly string[] | null;
  /**
   * Replay time (ISO-8601). Pages first seen after it are not drawn; pages
   * first seen within the scene's "born" window before it carry a birth ring.
   * Null shows memory as it is now.
   */
  asOf: string | null;
  markers: readonly SceneMarker[];
  questions: readonly SceneQuestion[];
  /** Node ids of pages flagged with a contradiction (lint L02); drawn with an amber ring. */
  disputedIds: ReadonlySet<string>;
  camera: SceneCameraCommand | null;
  /** Click on a page. `shift` is true for shift-click (trace a path). */
  onSelectNode: (nodeId: string, options: { shift: boolean }) => void;
  /** Click on empty space. */
  onBackgroundClick: () => void;
  /** Injected WebGL surface for tests; the real WebGL renderer when omitted. */
  createRenderer?: SceneRendererFactory;
}
