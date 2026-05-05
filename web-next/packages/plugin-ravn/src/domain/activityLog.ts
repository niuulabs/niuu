import { z } from 'zod';

/**
 * Kind of fleet activity event shown in the overview log tail.
 *
 * - session  — a ravn session was created or changed state
 * - trigger  — an automation trigger fired
 * - emit     — a ravn emitted a domain event
 */
export const activityLogKindSchema = z.enum(['session', 'trigger', 'emit']);

export type ActivityLogKind = z.infer<typeof activityLogKindSchema>;

/**
 * A single row in the recent-activity log tail on the Overview page.
 *
 * Owner: plugin-ravn (derived from sessions + triggers).
 */
export const activityLogEntrySchema = z.object({
  /** Stable key for React rendering. */
  id: z.string(),
  /** ISO-8601 UTC timestamp. */
  ts: z.string().datetime(),
  /** Event kind — drives badge colour. */
  kind: activityLogKindSchema,
  /** Short ravn / persona identifier shown in the "ravn" column. */
  ravnId: z.string(),
  /** Human-readable summary of the event. */
  message: z.string(),
});

export type ActivityLogEntry = z.infer<typeof activityLogEntrySchema>;
