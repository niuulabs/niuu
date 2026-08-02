/**
 * How a node is drawn: its glyph, its size, and its colour.
 *
 * Shape and size come from the registry, so both are operator-editable and a
 * new entity type needs no canvas change. Colour does not: it encodes whose
 * silicon the thing runs on, which is a property of the node rather than of
 * its type — the same `model` type is green on our own hardware and violet
 * when the call leaves the building.
 */

import type { Registry, TopologyNode } from '../../domain';
import { computeClassOf, type ComputeClass } from '../../domain/computeClass';
import { isKnownShape, glyphRadius } from './shapes';
import { NODE_SIZE } from './config';

export type Rgb = readonly [number, number, number];

/**
 * The compute ramp, matching `docs/mockups/observatory/index.html` and the
 * `--color-accent-*` tokens. Three hues, far enough apart to separate at a
 * glance at overview zoom where a glyph is a few pixels across.
 */
export const COMPUTE_COLOUR: Readonly<Record<ComputeClass, Rgb>> = {
  k8s: [56, 189, 248], // ice
  own: [143, 212, 0], // spring
  outside: [139, 124, 240], // violet
};

/** Health colours. These override the compute hue — a fault outranks placement. */
export const STATUS_COLOUR: Readonly<Record<string, Rgb>> = {
  degraded: [245, 158, 11], // amber
  failed: [239, 68, 68], // red
  error: [239, 68, 68],
  unknown: [94, 108, 134],
};

export interface NodeStyle {
  shape: string;
  size: number;
  colour: Rgb;
  computeClass: ComputeClass;
  /** Outer reach of the glyph, for edge trimming and label placement. */
  radius: number;
}

/**
 * Build a `typeId → {shape, size}` lookup from the registry.
 *
 * Falls back to the canvas' own size table for a type the registry has not
 * been taught yet, so an entity discovered before its type is registered still
 * draws at a sensible scale instead of collapsing to a point.
 */
export function buildTypeStyles(
  registry: Registry | null,
): Map<string, { shape: string; size: number }> {
  const styles = new Map<string, { shape: string; size: number }>();
  for (const type of registry?.types ?? []) {
    const shape = typeof type.shape === 'string' && isKnownShape(type.shape) ? type.shape : 'dot';
    const size =
      Number.isFinite(type.size) && type.size > 0 ? type.size : (NODE_SIZE[type.id] ?? 8);
    styles.set(type.id, { shape, size });
  }
  return styles;
}

export function nodeStyle(
  node: TopologyNode,
  typeStyles: Map<string, { shape: string; size: number }>,
  /**
   * Pre-resolved class from `computeClassMap`. Passed in because placement
   * needs the whole graph — a node inherits its cluster from its ancestry when
   * the adapter did not stamp the field.
   */
  computeClass = computeClassOf(node),
): NodeStyle {
  const declared = typeStyles.get(node.typeId);
  const shape = declared?.shape ?? 'dot';
  const size = declared?.size ?? NODE_SIZE[node.typeId] ?? 8;
  const colour = STATUS_COLOUR[node.status] ?? COMPUTE_COLOUR[computeClass];
  return { shape, size, colour, computeClass, radius: glyphRadius(shape, size) };
}
