import { z } from 'zod';

/**
 * Provider group a tool belongs to.
 * Used to bucket tools in the ToolPicker modal (Ravn persona form).
 *
 * Owner: **Ravn**. Consumed by: **Tyr** (persona library in the workflow editor).
 */
export const toolGroupSchema = z.enum([
  'fs',
  'shell',
  'git',
  'mimir',
  'observe',
  'security',
  'bus',
]);

export type ToolGroup = z.infer<typeof toolGroupSchema>;

/**
 * A single tool entry in the registry.
 *
 * **Canonical owner:** `plugin-ravn` (the TOOL_REGISTRY is defined alongside
 * the persona editor; destructive-flag UI logic lives in ToolPicker).
 *
 * **Consumed by:**
 * - `plugin-tyr` — workflow inspector shows tool access summary per persona.
 * - Any plugin rendering a `ToolPicker` or tool allowlist.
 */
export const toolSchema = z.object({
  /**
   * Unique token identifying this tool (e.g. `read`, `bash`,
   * `git.checkout`, `mimir.write`).
   */
  id: z.string().min(1),
  /** Provider group — used to cluster tools in the ToolPicker modal. */
  group: toolGroupSchema,
  /**
   * Whether this tool can cause irreversible side effects.
   * Destructive tools render with a red warning stripe in the allow-list
   * and require explicit acknowledgement in safe permission mode.
   */
  destructive: z.boolean(),
  /** One-line description shown in the ToolPicker tooltip. */
  desc: z.string().min(1),
});

export type Tool = z.infer<typeof toolSchema>;

/**
 * The complete set of tools available to ravens on this deployment.
 * Plugins and personas reference tools by their `id`.
 *
 * **Canonical owner:** `plugin-ravn`.
 *
 * **Consumed by:**
 * - `plugin-tyr` — validates that personas referenced in a workflow have
 *   the tools required by their stage.
 * - `plugin-mimir` — ravn-bindings screen shows tool access per raven.
 */
export const toolRegistrySchema = z.array(toolSchema);

export type ToolRegistry = z.infer<typeof toolRegistrySchema>;
