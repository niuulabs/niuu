import { z } from 'zod';

/**
 * How a trigger fires — the initiative source.
 */
export const triggerKindSchema = z.enum(['cron', 'event', 'webhook', 'manual']);

export type TriggerKind = z.infer<typeof triggerKindSchema>;

/**
 * A Trigger subscribes a Persona (or fleet) to an initiative source.
 * cron   — fires on a schedule (crontab spec in `spec`)
 * event  — fires when a named event is emitted (event name in `spec`)
 * webhook— fires on an inbound HTTP POST (route path in `spec`)
 * manual — fires on explicit user action (label/slug in `spec`)
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
   * - cron:    "0 * * * *"
   * - event:   "code.changed"
   * - webhook: "/hooks/deploy"
   * - manual:  "run-health-check"
   */
  spec: z.string(),
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
