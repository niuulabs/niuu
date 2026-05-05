import { z } from 'zod';

/**
 * Session (Tyr raid session) domain types.
 *
 * A SessionInfo tracks the live state of an autonomous coding session spawned
 * by a Raid. It holds the approval flow state and a chronicle of log lines.
 *
 * NOTE: This is distinct from the Ravn Session type (plugin-ravn owns that).
 *
 * Owner: plugin-tyr.
 */

export const tyrSessionStatusSchema = z.enum([
  'running',
  'awaiting_approval',
  'approved',
  'rejected',
  'complete',
  'failed',
]);
export type TyrSessionStatus = z.infer<typeof tyrSessionStatusSchema>;

export const sessionInfoSchema = z.object({
  /** Unique session identifier. */
  sessionId: z.string().min(1),
  /** Current session lifecycle status. */
  status: tyrSessionStatusSchema,
  /** Recent log / chronicle output lines. */
  chronicleLines: z.array(z.string()),
  /** Git branch associated with this session. */
  branch: z.string().nullable(),
  /** Confidence score at the time of the last status change (0–100). */
  confidence: z.number().min(0).max(100),
  /** Name of the raid this session is executing. */
  raidName: z.string(),
  /** Name of the saga this raid belongs to. */
  sagaName: z.string(),
});
export type SessionInfo = z.infer<typeof sessionInfoSchema>;
