import { z } from 'zod';

/**
 * Saga domain types — migrated from web/src/modules/tyr/models/saga.ts.
 *
 * Owner: plugin-tyr (source of truth).
 */

export const sagaStatusSchema = z.enum(['active', 'complete', 'failed']);
export type SagaStatus = z.infer<typeof sagaStatusSchema>;

export const phaseStatusSchema = z.enum(['pending', 'active', 'gated', 'complete']);
export type PhaseStatus = z.infer<typeof phaseStatusSchema>;

export const raidStatusSchema = z.enum([
  'pending',
  'queued',
  'running',
  'review',
  'escalated',
  'merged',
  'failed',
]);
export type RaidStatus = z.infer<typeof raidStatusSchema>;

export const confidenceEventTypeSchema = z.enum([
  'ci_pass',
  'ci_fail',
  'scope_breach',
  'retry',
  'human_reject',
]);
export type ConfidenceEventType = z.infer<typeof confidenceEventTypeSchema>;

export const sagaPhaseSummarySchema = z.object({
  total: z.number().int().nonnegative(),
  completed: z.number().int().nonnegative(),
});
export type SagaPhaseSummary = z.infer<typeof sagaPhaseSummarySchema>;

export const sagaSchema = z.object({
  /** Unique identifier (UUID). */
  id: z.string().uuid(),
  /** External tracker issue/milestone ID. */
  trackerId: z.string(),
  /** Tracker system type (e.g. "linear", "github"). */
  trackerType: z.string(),
  /** URL-safe slug derived from saga name. */
  slug: z.string(),
  /** Human-readable saga name. */
  name: z.string().min(1),
  /** List of repository identifiers this saga targets. */
  repos: z.array(z.string()),
  /** Git feature branch for all work in this saga. */
  featureBranch: z.string(),
  /** Current lifecycle status. */
  status: sagaStatusSchema,
  /** Aggregate confidence score (0–100). */
  confidence: z.number().min(0).max(100),
  /** ISO-8601 UTC creation timestamp. */
  createdAt: z.string().datetime(),
  /** Phase progress summary. */
  phaseSummary: sagaPhaseSummarySchema,
  /** Applied workflow identifier. */
  workflowId: z.string().uuid().optional(),
  /** Applied workflow name (e.g. "ship", "scaffold"). */
  workflow: z.string().optional(),
  /** Applied workflow version string (e.g. "1.4.2"). */
  workflowVersion: z.string().optional(),
  /** Base branch all feature work merges into (e.g. "main"). */
  baseBranch: z.string().default('main'),
});
export type Saga = z.infer<typeof sagaSchema>;

export const raidSchema = z.object({
  id: z.string().uuid(),
  phaseId: z.string(),
  trackerId: z.string(),
  name: z.string().min(1),
  description: z.string(),
  acceptanceCriteria: z.array(z.string()),
  declaredFiles: z.array(z.string()),
  estimateHours: z.number().nullable(),
  status: raidStatusSchema,
  confidence: z.number().min(0).max(100),
  sessionId: z.string().nullable(),
  reviewerSessionId: z.string().nullable(),
  reviewRound: z.number().int().nonnegative(),
  branch: z.string().nullable(),
  chronicleSummary: z.string().nullable(),
  retryCount: z.number().int().nonnegative(),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
});
export type Raid = z.infer<typeof raidSchema>;

export const phaseSchema = z.object({
  id: z.string().uuid(),
  sagaId: z.string(),
  trackerId: z.string(),
  number: z.number().int().positive(),
  name: z.string().min(1),
  status: phaseStatusSchema,
  confidence: z.number().min(0).max(100),
  raids: z.array(raidSchema),
});
export type Phase = z.infer<typeof phaseSchema>;

export const confidenceEventSchema = z.object({
  id: z.string().uuid(),
  raidId: z.string(),
  eventType: confidenceEventTypeSchema,
  delta: z.number(),
  scoreAfter: z.number().min(0).max(100),
  createdAt: z.string().datetime(),
});
export type ConfidenceEvent = z.infer<typeof confidenceEventSchema>;
