import { z } from 'zod';

/**
 * Ting Settings domain types.
 *
 * Covers: flock config, dispatch defaults, notification settings, and audit log entries.
 * Owner: plugin-ting.
 */

// ---------------------------------------------------------------------------
// FlockConfig — global configuration for a Ting "flock" (managed project group)
// ---------------------------------------------------------------------------

export const flockConfigSchema = z.object({
  /** Human-readable name for this flock deployment. */
  flockName: z.string().min(1),
  /** Default base branch used when creating new Sagas. */
  defaultBaseBranch: z.string().min(1),
  /** Default tracker type for new Sagas ('linear' | 'github' | 'mock'). */
  defaultTrackerType: z.string().min(1),
  /** Comma-separated list of default repos (org/repo) applied to new Sagas. */
  defaultRepos: z.array(z.string()),
  /** Maximum concurrent Sagas allowed in 'active' state. */
  maxActiveSagas: z.number().int().positive(),
  /** Whether Ting should automatically create tracker milestones for new Phases. */
  autoCreateMilestones: z.boolean(),
  /** ISO-8601 UTC timestamp of last config save. */
  updatedAt: z.string().datetime(),
});
export type FlockConfig = z.infer<typeof flockConfigSchema>;

// ---------------------------------------------------------------------------
// DispatchDefaults — default values applied to the dispatcher on new deployments
// ---------------------------------------------------------------------------

export const retryPolicySchema = z.object({
  /** Maximum number of retries per Run before escalation. */
  maxRetries: z.number().int().min(0),
  /** Delay in seconds between retries (linear backoff). */
  retryDelaySeconds: z.number().int().min(0),
  /** Whether to escalate to human review after exhausting retries. */
  escalateOnExhaustion: z.boolean(),
});
export type RetryPolicy = z.infer<typeof retryPolicySchema>;

export const dispatchDefaultsSchema = z.object({
  /** Minimum confidence score (0–100) required before a Run is dispatched. */
  confidenceThreshold: z.number().min(0).max(100),
  /** Maximum concurrent Runs. */
  maxConcurrentRuns: z.number().int().positive(),
  /** Whether the dispatcher starts in auto-continue mode. */
  autoContinue: z.boolean(),
  /** Batch size: how many queued Runs to evaluate per dispatch cycle. */
  batchSize: z.number().int().positive(),
  /** Retry policy applied to failed Runs. */
  retryPolicy: retryPolicySchema,
  /** UTC time window where the dispatcher will not start new runs. */
  quietHours: z.string(),
  /** Duration after which pending reviews are auto-escalated. */
  escalateAfter: z.string(),
  /** ISO-8601 UTC timestamp of last config save. */
  updatedAt: z.string().datetime(),
});
export type DispatchDefaults = z.infer<typeof dispatchDefaultsSchema>;

// ---------------------------------------------------------------------------
// NotificationSettings — per-event notification toggles
// ---------------------------------------------------------------------------

export const notificationChannelSchema = z.enum(['telegram', 'email', 'webhook', 'none']);
export type NotificationChannel = z.infer<typeof notificationChannelSchema>;

export const notificationSettingsSchema = z.object({
  /** Preferred delivery channel. */
  channel: notificationChannelSchema,
  /** Notify when a Run enters 'awaiting_approval'. */
  onRunPendingApproval: z.boolean(),
  /** Notify when a Run is merged. */
  onRunMerged: z.boolean(),
  /** Notify when a Run fails. */
  onRunFailed: z.boolean(),
  /** Notify when a Saga completes (all Runs merged). */
  onSagaComplete: z.boolean(),
  /** Notify when the dispatcher stops due to an error. */
  onDispatcherError: z.boolean(),
  /** Optional webhook URL (required when channel is 'webhook'). */
  webhookUrl: z.string().nullable(),
  /** ISO-8601 UTC timestamp of last config save. */
  updatedAt: z.string().datetime(),
});
export type NotificationSettings = z.infer<typeof notificationSettingsSchema>;

// ---------------------------------------------------------------------------
// AuditEntry — immutable record of a settings change or dispatch event
// ---------------------------------------------------------------------------

export const auditEntryKindSchema = z.enum([
  'settings.flock_config.updated',
  'settings.dispatch_defaults.updated',
  'settings.notifications.updated',
  'dispatcher.started',
  'dispatcher.stopped',
  'dispatcher.threshold_changed',
  'dispatcher.batch_size_changed',
  'run.dispatched',
  'run.merged',
  'run.failed',
  'run.escalated',
  'saga.created',
  'saga.completed',
]);
export type AuditEntryKind = z.infer<typeof auditEntryKindSchema>;

export const auditEntrySchema = z.object({
  id: z.string().uuid(),
  kind: auditEntryKindSchema,
  /** Human-readable summary of what changed. */
  summary: z.string().min(1),
  /** Actor who triggered the event (user id, 'system', or 'dispatcher'). */
  actor: z.string().min(1),
  /** Optional JSON blob of the changed values (before/after). */
  payload: z.record(z.string(), z.unknown()).nullable(),
  /** ISO-8601 UTC timestamp of the event. */
  createdAt: z.string().datetime(),
});
export type AuditEntry = z.infer<typeof auditEntrySchema>;

export const auditFilterSchema = z.object({
  kinds: z.array(auditEntryKindSchema).optional(),
  actor: z.string().optional(),
  /** ISO-8601 date range start. */
  since: z.string().datetime().optional(),
  /** ISO-8601 date range end. */
  until: z.string().datetime().optional(),
  limit: z.number().int().positive().optional(),
});
export type AuditFilter = z.infer<typeof auditFilterSchema>;
