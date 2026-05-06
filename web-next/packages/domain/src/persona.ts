import { z } from 'zod';
import { fieldTypeSchema } from './event-catalog';

/**
 * Canonical roles a Persona may fulfil in a workflow.
 * Used by the role→shape mapping in PersonaAvatar (Ravn, Tyr).
 *
 * Owner: **Ravn** (`plugin-ravn`).
 * Consumed by: **Tyr** (workflow DAG, dispatch), **Mimir** (write-routing).
 */
export const personaRoleSchema = z.enum([
  'arbiter',
  'audit',
  'autonomy',
  'build',
  'coord',
  'gate',
  'index',
  'investigate',
  'knowledge',
  'observe',
  'plan',
  'qa',
  'report',
  'review',
  'ship',
  'verify',
  'write',
]);

export type PersonaRole = z.infer<typeof personaRoleSchema>;

/**
 * LLM configuration attached to a Persona.
 *
 * Owner: **Ravn**. Consumed by: **Tyr** (dispatch feasibility check).
 */
export const llmConfigSchema = z.object({
  /** Short human-readable model alias (e.g. "claude-sonnet-4-6"). */
  alias: z.string().min(1),
  /** Whether extended thinking is enabled for this persona. */
  thinking: z.boolean(),
  /** Hard token cap per invocation. */
  maxTokens: z.number().int().positive(),
  /** Sampling temperature. Omit to use the model default. */
  temperature: z.number().min(0).max(2).optional(),
});

export type LlmConfig = z.infer<typeof llmConfigSchema>;

/**
 * A single event this Persona subscribes to, plus optional inject catalog
 * entries to slot into the prompt and a producer-trust threshold.
 *
 * Owner: **Ravn**. Consumed by: **Tyr** (fan-in wiring).
 */
export const consumedEventSchema = z.object({
  /** Event name, matches an entry in EventCatalog. */
  name: z.string().min(1),
  /** Inject catalog keys to embed when this event fires. */
  injects: z.array(z.string()).optional(),
  /** Minimum trust score required from the producing persona (0–1). */
  trust: z.number().min(0).max(1).optional(),
});

export type ConsumedEvent = z.infer<typeof consumedEventSchema>;

/**
 * The single event this Persona emits on completion.
 *
 * Owner: **Ravn**. Consumed by: **Tyr** (fan-in wiring), **Observatory** (graph).
 */
export const producedEventSchema = z.object({
  /** Event name, must exist in EventCatalog or be created there. */
  event: z.string().min(1),
  /** Payload field definitions: field-name → {@link FieldType} string. */
  schema: z.record(z.string(), fieldTypeSchema),
});

export type ProducedEvent = z.infer<typeof producedEventSchema>;

// ---------------------------------------------------------------------------
// Fan-in strategies
// ---------------------------------------------------------------------------

/**
 * Quorum strategy params: n-of-M with an optional time window.
 */
export const quorumParamsSchema = z.object({
  /** How many producers must succeed. */
  n: z.number().int().min(1),
  /** Out of how many total producers. */
  of: z.number().int().min(1),
  /** Optional time window in milliseconds. */
  windowMs: z.number().int().min(0).optional(),
});

export type QuorumParams = z.infer<typeof quorumParamsSchema>;

/**
 * Weighted-score strategy params: per-persona weight map.
 */
export const weightedScoreParamsSchema = z.object({
  /**
   * Map of personaName → weight (0–1). Omitting uses equal weights.
   */
  weights: z.record(z.string(), z.number().min(0).max(1)).optional(),
});

export type WeightedScoreParams = z.infer<typeof weightedScoreParamsSchema>;

/**
 * Discriminated union of all fan-in aggregation strategies.
 *
 * - `all_must_pass` — every declared producer must report success.
 * - `any_passes`    — accept on the first success (race).
 * - `quorum`        — N-of-M with an optional time window.
 * - `merge`         — concatenate / union payloads.
 * - `first_wins`    — first producer wins, rest discarded.
 * - `weighted_score`— producers return numeric scores, arbiter averages by weight.
 *
 * Owner: **Ravn**. Consumed by: **Tyr** (fan-in plumbing).
 */
export const fanInStrategySchema = z.discriminatedUnion('strategy', [
  z.object({
    strategy: z.literal('all_must_pass'),
    params: z.record(z.string(), z.unknown()),
  }),
  z.object({
    strategy: z.literal('any_passes'),
    params: z.record(z.string(), z.unknown()),
  }),
  z.object({
    strategy: z.literal('quorum'),
    params: quorumParamsSchema,
  }),
  z.object({
    strategy: z.literal('merge'),
    params: z.record(z.string(), z.unknown()),
  }),
  z.object({
    strategy: z.literal('first_wins'),
    params: z.record(z.string(), z.unknown()),
  }),
  z.object({
    strategy: z.literal('weighted_score'),
    params: weightedScoreParamsSchema,
  }),
]);

export type FanInStrategy = z.infer<typeof fanInStrategySchema>;

// ---------------------------------------------------------------------------
// Persona
// ---------------------------------------------------------------------------

/**
 * A Persona is a YAML template that any number of ravens can bind to.
 * It encodes the agent's role, LLM settings, tool permissions, event wiring,
 * fan-in strategy, and Mímir write-routing.
 *
 * **Canonical owner:** `plugin-ravn` (source of truth lives in
 * `volundr/src/ravn/personas/*.yaml`).
 *
 * **Consumed by:**
 * - `plugin-tyr` — workflow DAG nodes reference personas by name; dispatch
 *   checks LLM alias + confidence thresholds.
 * - `plugin-mimir` — write-routing + ravn-binding screens show the persona
 *   name, role, and mimirWriteRouting field.
 * - `plugin-observatory` — topology canvas labels raven nodes with persona
 *   role-shape.
 */
export const personaSchema = z.object({
  /** Unique human-readable identifier (matches the YAML filename stem). */
  name: z.string().min(1),
  /** Functional role — drives the PersonaAvatar shape. */
  role: personaRoleSchema,
  /** CSS color token or hex used to tint the persona avatar. */
  color: z.string().min(1),
  /** Single uppercase letter rendered inside the PersonaAvatar. */
  letter: z.string().length(1),
  /** One-sentence summary shown in list views. */
  summary: z.string().min(1),
  /** Longer description shown in the persona form. */
  description: z.string(),
  /** LLM settings for this persona. */
  llm: llmConfigSchema,
  /** Tool permission mode applied at the executor level. */
  permissionMode: z.enum(['default', 'safe', 'loose']),
  /** Explicitly allowed tool ids. */
  allowed: z.array(z.string()),
  /** Explicitly forbidden tool ids. */
  forbidden: z.array(z.string()),
  /** The single event this persona emits on completion. */
  produces: producedEventSchema,
  /** Events this persona subscribes to. */
  consumes: z.object({ events: z.array(consumedEventSchema) }),
  /** Fan-in strategy when multiple producers feed this persona. */
  fanIn: fanInStrategySchema.optional(),
  /**
   * Which Mímir mount this persona's writes are routed to.
   * Omit to inherit the global routing default.
   */
  mimirWriteRouting: z.enum(['local', 'shared', 'domain']).optional(),
});

export type Persona = z.infer<typeof personaSchema>;
