import { z } from 'zod';

/**
 * Shape primitives used by the Observatory canvas and Registry editor.
 * Every EntityType maps to one of these SVG shapes (20×20 viewBox).
 *
 * Owner: **Observatory** (`plugin-observatory`).
 */
export const entityShapeSchema = z.enum([
  'ring',
  'ring-dashed',
  'rounded-rect',
  'diamond',
  'triangle',
  'hex',
  'chevron',
  'square',
  'square-sm',
  'pentagon',
  'halo',
  'mimir',
  'mimir-small',
  'dot',
]);

export type EntityShape = z.infer<typeof entityShapeSchema>;

/**
 * Grouping categories for the Registry editor type grid.
 *
 * Owner: **Observatory**. Consumed by: **Tyr** (saga member badges).
 */
export const entityCategorySchema = z.enum([
  'topology',
  'hardware',
  'agent',
  'coordinator',
  'knowledge',
  'infrastructure',
  'device',
  'composite',
]);

export type EntityCategory = z.infer<typeof entityCategorySchema>;

/**
 * A single entry in the Observatory type registry (SDD §4.1).
 *
 * Each entity type defines the shape, color, rune, and containment rules
 * used by both the topology canvas and the drag-reparent registry editor.
 *
 * **Canonical owner:** `plugin-observatory` (the Registry screen manages
 * the full `TypeRegistry`; drag-reparent rewrites `parentTypes` /
 * `canContain` with cycle-protection).
 *
 * **Consumed by:**
 * - `plugin-tyr` — topology sidebar labels raid participants with their
 *   entity type's shape + rune.
 * - `plugin-mimir` — entity pages reference `entity_type` by id.
 * - `plugin-ravn` — raven nodes in the fleet console use the `agent`
 *   category shape.
 */
export const entityTypeSchema = z.object({
  /** Unique stable identifier (e.g. `realm`, `raven`, `bifrost`). */
  id: z.string().min(1),
  /** Human-readable display name shown in the Registry grid. */
  label: z.string().min(1),
  /** Grouping category for the Registry type grid. */
  category: entityCategorySchema,
  /**
   * Single-character runic identity glyph.
   * Must not be one of the historically appropriated symbols:
   * ᛟ ᛊ ᛏ ᛉ ᚺ ᚻ.
   */
  rune: z.string().length(1),
  /** SVG shape primitive used on the canvas and in registry rows. */
  shape: entityShapeSchema,
  /** CSS color token or hex for this entity type's accent color. */
  color: z.string().min(1),
  /** One-sentence description shown in the Registry drawer. */
  description: z.string(),
  /**
   * Ids of types that can contain this type (single-parent model).
   * Drag-reparent in the Registry rewrites this to `[newParentId]`.
   */
  parentTypes: z.array(z.string()),
  /**
   * Ids of types this type is allowed to contain as direct children.
   * Cycle-protection in the Registry editor prevents a type from being
   * reparented onto one of its own descendants.
   */
  canContain: z.array(z.string()),
});

export type EntityType = z.infer<typeof entityTypeSchema>;

/**
 * The full Observatory type registry, versioned for optimistic concurrency.
 *
 * **Canonical owner:** `plugin-observatory`.
 *
 * **Consumed by:**
 * - `plugin-ravn` — `PluginCtx.registry` is threaded through the shell so
 *   Ravn can resolve entity types for live topology labels.
 * - `plugin-tyr` — same shell context; saga members resolve their entity type
 *   shape for the raid panel.
 */
export const typeRegistrySchema = z.object({
  /** Monotonically incrementing version, bumped on every registry edit. */
  version: z.number().int().min(0),
  /** ISO-8601 timestamp of the last edit. */
  updatedAt: z.string().min(1),
  /** All entity types in this registry. */
  types: z.array(entityTypeSchema),
});

export type TypeRegistry = z.infer<typeof typeRegistrySchema>;
