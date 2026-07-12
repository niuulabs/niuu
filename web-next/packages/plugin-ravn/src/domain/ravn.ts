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

export const residentBackendSchema = z.enum(['helmrelease', 'openshell']);
export type ResidentBackend = z.infer<typeof residentBackendSchema>;

export const residentEngineSchema = z.enum(['ravn', 'openclaw', 'hermes']);
export type ResidentEngine = z.infer<typeof residentEngineSchema>;

export const residentCapabilitySchema = z.enum([
  'chat',
  'session.list',
  'session.create',
  'session.delete',
  'steer',
  'interrupt',
  'approvals',
  'runtime.restart',
  'runtime.suspend',
  'logs',
  'metrics',
  'usage',
]);
export type ResidentCapability = z.infer<typeof residentCapabilitySchema>;

export const residentEndpointSchema = z.object({
  kind: z.string(),
  protocol: z.string(),
  url: z.string(),
});
export type ResidentEndpoint = z.infer<typeof residentEndpointSchema>;

export const residentConditionSchema = z.object({
  type: z.string(),
  status: z.enum(['true', 'false', 'unknown']),
  reason: z.string(),
  message: z.string(),
  lastTransitionAt: z.string(),
});
export type ResidentCondition = z.infer<typeof residentConditionSchema>;

export interface ResidentDeploymentProfile {
  id: string;
  displayName: string;
  description: string;
  backend: ResidentBackend;
  engine: ResidentEngine;
  capabilities: ResidentCapability[];
  defaultModel: string;
  allowedModels: string[];
  labels: string[];
  instanceId: string;
  instanceName: string;
  instanceSlug: string;
}

/**
 * A Ravn is a deployed runtime instance bound to a Persona.
 * It represents a live or dormant agent node in the fleet.
 *
 * Owner: plugin-ravn (source of truth).
 */
export const ravnSchema = z
  .object({
    /** Unique identifier (UUID). */
    id: z.string().uuid(),
    /** Name of the bound Persona; managed residents may intentionally omit one. */
    personaName: z.string(),
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
    /** Display name of the resident session (resident ravens only). */
    residentName: z.string().optional(),
    /** Mesh peer id of the resident session (resident ravens only). */
    peerId: z.string().optional(),
    /** What backs this ravn: a long-lived resident session or a persona deployment. */
    kind: z.enum(['resident', 'persona']).optional(),
    /** Skuld WebSocket chat endpoint (same protocol as Volundr live sessions). */
    chatEndpoint: z.string().nullable().optional(),
    /** Backing session id (resident ravens only). */
    sessionId: z.string().optional(),
    /** Infrastructure and engine contract for managed residents. */
    backend: residentBackendSchema.optional(),
    engine: residentEngineSchema.optional(),
    profileId: z.string().optional(),
    desiredState: z.enum(['running', 'suspended', 'deleted']).optional(),
    observedState: z
      .enum(['pending', 'deploying', 'active', 'suspended', 'failed', 'deleting'])
      .optional(),
    backendRef: z.record(z.string(), z.unknown()).optional(),
    capabilities: z.array(residentCapabilitySchema).optional(),
    conditions: z.array(residentConditionSchema).optional(),
    endpoints: z.array(residentEndpointSchema).optional(),
    managed: z.boolean().optional(),
    instanceId: z.string().optional(),
    instanceName: z.string().optional(),
    instanceSlug: z.string().optional(),
    messageCount: z.number().int().nonnegative().optional(),
    tokenCount: z.number().int().nonnegative().optional(),
    costUsd: z.number().nonnegative().optional(),
  })
  .superRefine((ravn, context) => {
    if (ravn.personaName || (ravn.kind === 'resident' && ravn.managed)) return;
    context.addIssue({
      code: 'custom',
      path: ['personaName'],
      message: 'personaName is required for non-resident ravens',
    });
  });

export type Ravn = z.infer<typeof ravnSchema>;
