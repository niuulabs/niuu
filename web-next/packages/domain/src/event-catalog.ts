import { z } from 'zod';

/**
 * Schema field type strings used in event payload definitions.
 *
 * Owner: **Ravn**. Consumed by: **Tyr** (workflow validation), **Observatory** (graph).
 */
export const fieldTypeSchema = z.enum(['string', 'number', 'boolean', 'object', 'array', 'any']);

export type FieldType = z.infer<typeof fieldTypeSchema>;

/**
 * Specification of a single event that can be produced or consumed by ravens.
 *
 * **Canonical owner:** `plugin-ravn` (the EventCatalog is managed in the
 * Ravn Personas form via EventPicker + SchemaEditor).
 *
 * **Consumed by:**
 * - `plugin-tyr` — WorkflowBuilder validates that every `consumes` edge has a
 *   matching producer; fan-in wiring reads the event name.
 * - `plugin-observatory` — Events view renders the produces/consumes graph.
 */
export const eventSpecSchema = z.object({
  /**
   * Globally unique event name (e.g. `code.changed`, `review.approved`).
   * Convention: `<domain>.<verb>` in lower-kebab-case.
   */
  name: z.string().min(1),
  /**
   * Payload shape: field-name → type-string from `FieldType`.
   * An empty record is valid for zero-payload events.
   */
  schema: z.record(z.string(), fieldTypeSchema),
});

export type EventSpec = z.infer<typeof eventSpecSchema>;

/**
 * The complete catalog of events available on this deployment.
 *
 * **Canonical owner:** `plugin-ravn` (EventPicker allows new events to be
 * created inline; the catalog grows as personas are edited).
 *
 * **Consumed by:**
 * - `plugin-tyr` — every workflow node's `produces` / `consumes` wiring is
 *   validated against this catalog.
 * - `plugin-observatory` — Events view renders the full catalog as a graph.
 * - `plugin-mimir` — ravn-bindings screen labels consumed events.
 */
export const eventCatalogSchema = z.array(eventSpecSchema);

export type EventCatalog = z.infer<typeof eventCatalogSchema>;
