export { MemoryScene } from './MemoryScene';
export type {
  MemorySceneProps,
  SceneView,
  ColourBy,
  SceneFocus,
  FocusDepth,
  SceneAnswer,
  SceneMarker,
  SceneQuestion,
  SceneCameraCommand,
  SceneRenderer,
  SceneRendererFactory,
} from './types';

export { createWebGLRenderer, supportsWebGL } from './webglRenderer';
export type { Scene3DRenderer, Scene3DRendererFactory } from './webglRenderer';

export { memoryPalette, DEFAULT_MEMORY_PALETTE } from './palette';
export type { MemoryPalette, ProofBucket, AgeBucket } from './palette';

export { nodeColour, nodeKindGroup, proofBucket, ageBucket } from './colour';

export { relationLabel, isContradictionRelation } from './relationLabel';

export {
  computeLayout,
  computeLayoutUncached,
  flattenTo2D,
  mountOf,
  radiusForDegree,
} from './layout';
export type { SceneLayout, NodeLayout } from './layout';

export { computeVisibility } from './visibility';
export type {
  SceneVisibility,
  NodeVisibility,
  EdgeVisibility,
  LitLevel,
  VisibilityInput,
} from './visibility';

export { pickNearestNode, isDragGesture } from './picking';
export type { ProjectedPoint } from './picking';

export { layoutLabels } from './labelLayout';
export type { LabelCandidate, PlacedLabel } from './labelLayout';
