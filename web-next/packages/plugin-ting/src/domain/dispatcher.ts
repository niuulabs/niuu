import { z } from 'zod';

/**
 * Dispatcher domain types.
 *
 * The Dispatcher is the autonomous run-execution engine. It picks runs from
 * the queue when confidence is above threshold and capacity is available.
 *
 * Owner: plugin-ting.
 */

export const dispatcherStateSchema = z.object({
  /** Unique row identifier (UUID). */
  id: z.string().uuid(),
  /** Whether the dispatcher is actively processing the queue. */
  running: z.boolean(),
  /** Minimum confidence score (0–100) a run must have to be dispatched. */
  threshold: z.number().min(0).max(100),
  /** Maximum number of runs allowed to run concurrently. */
  maxConcurrentRuns: z.number().int().positive(),
  /** If true, the dispatcher automatically continues after each run completes. */
  autoContinue: z.boolean(),
  /** ISO-8601 UTC last-update timestamp. */
  updatedAt: z.string().datetime(),
});
export type DispatcherState = z.infer<typeof dispatcherStateSchema>;

export const dispatchRuleSchema = z.object({
  id: z.string().uuid(),
  /** Human-readable rule name. */
  name: z.string().min(1),
  /** Optional saga filter: only apply this rule when the saga matches. */
  sagaId: z.string().nullable(),
  /** Overrides the global threshold for matched runs. */
  thresholdOverride: z.number().min(0).max(100).nullable(),
  /** ISO-8601 UTC creation timestamp. */
  createdAt: z.string().datetime(),
});
export type DispatchRule = z.infer<typeof dispatchRuleSchema>;
