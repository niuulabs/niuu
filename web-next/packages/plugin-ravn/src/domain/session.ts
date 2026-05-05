import { z } from 'zod';
import { personaRoleSchema, type PersonaRole } from '@niuulabs/domain';

/**
 * Live status of a Session.
 */
export const sessionStatusSchema = z.enum(['running', 'idle', 'stopped', 'failed']);

export type SessionStatus = z.infer<typeof sessionStatusSchema>;

/**
 * A Session is a live interaction thread between a user and a Ravn.
 * It holds state for an ongoing or completed conversation.
 *
 * Owner: plugin-ravn.
 */
export const sessionSchema = z.object({
  /** Unique identifier (UUID). */
  id: z.string().uuid(),
  /** ID of the Ravn that owns this session. */
  ravnId: z.string().min(1),
  /** Persona bound to the ravn at session start. */
  personaName: z.string().min(1),
  /** Functional role of the persona (drives avatar shape). */
  personaRole: personaRoleSchema.optional(),
  /** Single display letter for the persona avatar. */
  personaLetter: z.string().optional(),
  /** Current session status. */
  status: sessionStatusSchema,
  /** LLM alias used for this session. */
  model: z.string().min(1),
  /** ISO-8601 UTC creation timestamp. */
  createdAt: z.string().datetime(),
  /** Human-readable title for this session. */
  title: z.string().optional(),
  /** Number of messages in this session. */
  messageCount: z.number().int().nonnegative().optional(),
  /** Total token count across all messages in this session. */
  tokenCount: z.number().int().nonnegative().optional(),
  /** Total cost of this session in USD. */
  costUsd: z.number().nonnegative().optional(),
});

export type Session = z.infer<typeof sessionSchema>;

/** Re-export for consumers that need the persona role type. */
export type { PersonaRole };
