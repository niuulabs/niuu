import { z } from 'zod';

/**
 * How a trigger fires — the initiative source.
 *
 * Only 'cron' and 'event' are backed by a resident execution engine
 * (ravn.adapters.triggers.api_source.ApiTriggerSource); the backend's
 * POST /api/v1/ravn/triggers rejects any other kind with 422, so the UI
 * does not offer 'webhook'/'manual' as choices.
 */
export const triggerKindSchema = z.enum(['cron', 'event']);

export type TriggerKind = z.infer<typeof triggerKindSchema>;

/**
 * A Trigger subscribes a Persona (or fleet) to an initiative source.
 * cron   — fires on a schedule (crontab spec in `spec`)
 * event  — fires when a named Sleipnir event type is emitted (in `spec`,
 *          e.g. "github.pr.opened" — must be a real event.domain.registry
 *          constant, and must carry `repo` so it only fires for this realm)
 *
 * Owner: plugin-ravn.
 */
export const triggerSchema = z.object({
  /** Unique identifier (UUID). */
  id: z.string().uuid(),
  /** Initiative source kind. */
  kind: triggerKindSchema,
  /** Persona to dispatch when this trigger fires. */
  personaName: z.string().min(1),
  /**
   * Kind-specific spec:
   * - cron:  "0 * * * *"
   * - event: "github.pr.opened" (a sleipnir.domain.registry event type)
   */
  spec: z.string(),
  /**
   * Required for kind 'event': the repository this trigger reacts to.
   * The Sleipnir event bus is not tenant-scoped, so without this an
   * event-kind trigger would fire on every tenant's matching events.
   * Unused for kind 'cron'.
   */
  repo: z.string().default(''),
  /** Whether the trigger is currently active. */
  enabled: z.boolean(),
  /** ISO-8601 UTC creation timestamp. */
  createdAt: z.string().datetime(),
  /** ISO-8601 UTC timestamp when this trigger last fired. */
  lastFiredAt: z.string().datetime().optional(),
  /** Total number of times this trigger has fired. */
  fireCount: z.number().int().nonnegative().optional(),
});

export type Trigger = z.infer<typeof triggerSchema>;

/**
 * The response to creating a trigger also says whether anything in this
 * deployment will ever execute it — a trigger can be durably stored with
 * nothing polling/running it (ravn.config.TriggerExecutionConfig,
 * operator-declared; the backend cannot detect this on its own). Callers
 * that depend on the trigger actually firing (e.g. realm creation's
 * "schedule standing jobs" step) must check this rather than assume storage
 * implies execution.
 */
export interface CreatedTrigger extends Trigger {
  executionEnabled: boolean;
}
