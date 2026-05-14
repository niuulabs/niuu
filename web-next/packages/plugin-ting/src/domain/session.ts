import { z } from 'zod';

/**
 * Session (Ting run session) domain types.
 *
 * A SessionInfo tracks the live state of an autonomous coding session spawned
 * by a Run. It holds the approval flow state and a chronicle of log lines.
 *
 * NOTE: This is distinct from the Ravn Session type (plugin-ravn owns that).
 *
 * Owner: plugin-ting.
 */

export const tingSessionStatusSchema = z.enum([
  'running',
  'awaiting_approval',
  'approved',
  'rejected',
  'complete',
  'failed',
]);
export type TingSessionStatus = z.infer<typeof tingSessionStatusSchema>;

export const sessionInfoSchema = z.object({
  /** Unique session identifier. */
  sessionId: z.string().min(1),
  /** Current session lifecycle status. */
  status: tingSessionStatusSchema,
  /** Recent log / chronicle output lines. */
  chronicleLines: z.array(z.string()),
  /** Git branch associated with this session. */
  branch: z.string().nullable(),
  /** Confidence score at the time of the last status change (0–100). */
  confidence: z.number().min(0).max(100),
  /** Name of the run this session is executing. */
  runName: z.string(),
  /** Name of the saga this run belongs to. */
  sagaName: z.string(),
  /** Human-readable Volundr cluster label hosting the session. */
  clusterName: z.string(),
});
export type SessionInfo = z.infer<typeof sessionInfoSchema>;
