import { z } from 'zod';
import { personaRoleSchema } from '@niuulabs/domain';

/**
 * Deployment status of a Ravn node.
 */
export const ravnStatusSchema = z.enum(['active', 'idle', 'suspended', 'failed', 'completed']);

export type RavnStatus = z.infer<typeof ravnStatusSchema>;

/**
 * A Mímir mount binding attached to this ravn.
 */
export const ravnMountSchema = z.object({
  /** Mount name. */
  name: z.string().min(1),
  /** Binding role (primary = r/w, archive = append, ro = read-only). */
  role: z.enum(['primary', 'archive', 'ro']),
});

export type RavnMount = z.infer<typeof ravnMountSchema>;

/**
 * A Ravn is a deployed runtime instance bound to a Persona.
 * It represents a live or dormant agent node in the fleet.
 *
 * Owner: plugin-ravn (source of truth).
 */
export const ravnSchema = z.object({
  /** Unique identifier (UUID). */
  id: z.string().uuid(),
  /** Name of the Persona this ravn is bound to. */
  personaName: z.string().min(1),
  /** Current lifecycle state of the ravn. */
  status: ravnStatusSchema,
  /** LLM alias in use (e.g. "claude-sonnet-4-6"). */
  model: z.string().min(1),
  /** ISO-8601 UTC creation timestamp. */
  createdAt: z.string().datetime(),
  /** ISO-8601 UTC last-update timestamp. */
  updatedAt: z.string().datetime().optional(),
  /** Deployment location label (e.g. "eu-west-1", "us-east-1"). */
  location: z.string().optional(),
  /** Deployment environment (e.g. "production", "staging"). */
  deployment: z.string().optional(),
  /** Persona role — cached for display (avatar shape). */
  role: personaRoleSchema.optional(),
  /** Persona letter — cached for display (avatar letter). */
  letter: z.string().optional(),
  /** Persona summary text — cached for identity panel. */
  summary: z.string().optional(),
  /** Persona iteration budget — max iterations per session. */
  iterationBudget: z.number().int().nonnegative().optional(),
  /** Mímir write-routing mode for this ravn. */
  writeRouting: z.enum(['local', 'shared', 'domain']).optional(),
  /** Cascade mode for this ravn (e.g. "sequential", "parallel"). */
  cascade: z.string().optional(),
  /** Mímir mount bindings attached to this ravn. */
  mounts: z.array(ravnMountSchema).optional(),
  /** MCP server names this ravn is connected to. */
  mcpServers: z.array(z.string()).optional(),
  /** Gateway channel names this ravn communicates through. */
  gatewayChannels: z.array(z.string()).optional(),
  /** Event topics this ravn is subscribed to (consumed + produced). */
  eventSubscriptions: z.array(z.string()).optional(),
});

export type Ravn = z.infer<typeof ravnSchema>;
