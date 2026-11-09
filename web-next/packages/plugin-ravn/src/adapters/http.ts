/**
 * HTTP adapter for IPersonaStore.
 *
 * Adapted from web/src/modules/ravn/api/client.ts.
 * Translates snake_case server responses to camelCase domain types.
 *
 * Accepts any HTTP client structurally compatible with ApiClient from
 * @niuulabs/query (i.e. has get/post/put/delete methods).
 */

import type { ApiClient } from '@niuulabs/query';
import type {
  IPersonaStore,
  PersonaSummary,
  PersonaDetail,
  PersonaCreateRequest,
  PersonaForkRequest,
  PersonaFilter,
} from '../ports';

// ---------------------------------------------------------------------------
// Internal raw types (snake_case server responses)
// ---------------------------------------------------------------------------

interface RawPersonaLLM {
  thinking_enabled: boolean;
  max_tokens: number;
}

interface RawPersonaProduces {
  event_type: string;
  schema_def: Record<string, unknown>;
}

interface RawPersonaConsumesEvent {
  name: string;
  injects?: string[];
  trust?: number;
}

interface RawPersonaConsumes {
  events: RawPersonaConsumesEvent[];
  schema_def?: Record<string, unknown>;
}

interface RawPersonaFanIn {
  strategy: string;
  params: Record<string, unknown>;
}

interface RawPersonaSummary {
  name: string;
  role: string;
  letter: string;
  color: string;
  summary: string;
  permission_mode: string;
  iteration_budget: number;
  is_builtin: boolean;
  has_override: boolean;
  produces_event: string;
  outcome_events?: Record<string, string>;
  consumes_events: string[];
}

interface RawPersonaDetail extends RawPersonaSummary {
  description: string;
  system_prompt_template: string;
  llm: RawPersonaLLM & { temperature?: number };
  produces: RawPersonaProduces;
  consumes: RawPersonaConsumes;
  /** Absent when the persona declares no fan-in (the API returns null). */
  fan_in: RawPersonaFanIn | null;
  mimir_write_routing?: string;
  yaml_source: string;
  override_source?: string;
}

// ---------------------------------------------------------------------------
// Transform functions
// ---------------------------------------------------------------------------

function toSummary(raw: RawPersonaSummary): PersonaSummary {
  return {
    name: raw.name,
    role: raw.role as PersonaSummary['role'],
    letter: raw.letter,
    color: raw.color,
    summary: raw.summary,
    permissionMode: raw.permission_mode,
    iterationBudget: raw.iteration_budget,
    isBuiltin: raw.is_builtin,
    hasOverride: raw.has_override,
    producesEvent: raw.produces_event,
    outcomeEvents: raw.outcome_events ?? {},
    consumesEvents: raw.consumes_events,
  };
}

function toDetail(raw: RawPersonaDetail): PersonaDetail {
  return {
    ...toSummary(raw),
    description: raw.description,
    systemPromptTemplate: raw.system_prompt_template,
    llm: {
      thinkingEnabled: raw.llm.thinking_enabled,
      maxTokens: raw.llm.max_tokens,
      temperature: raw.llm.temperature,
    },
    produces: {
      eventType: raw.produces.event_type,
      schemaDef: raw.produces.schema_def as PersonaDetail['produces']['schemaDef'],
    },
    consumes: {
      events: raw.consumes.events.map((e) => ({
        name: e.name,
        injects: e.injects,
        trust: e.trust,
      })),
      schemaDef: (raw.consumes.schema_def ?? {}) as PersonaDetail['consumes']['schemaDef'],
    },
    mimirWriteRouting: raw.mimir_write_routing as PersonaDetail['mimirWriteRouting'],
    fanIn: raw.fan_in
      ? {
          strategy: raw.fan_in.strategy,
          params: raw.fan_in.params,
        }
      : undefined,
    yamlSource: raw.yaml_source,
    overrideSource: raw.override_source,
  };
}

function toRequestBody(req: PersonaCreateRequest): Record<string, unknown> {
  return {
    name: req.name,
    role: req.role,
    letter: req.letter,
    color: req.color,
    summary: req.summary,
    description: req.description,
    system_prompt_template: req.systemPromptTemplate,
    permission_mode: req.permissionMode,
    iteration_budget: req.iterationBudget,
    llm_thinking_enabled: req.llmThinkingEnabled,
    llm_max_tokens: req.llmMaxTokens,
    llm_temperature: req.llmTemperature,
    produces_event_type: req.producesEventType,
    produces_schema: req.producesSchema,
    consumes_events: req.consumesEvents,
    consumes_schema: req.consumesSchema,
    fan_in_strategy: req.fanInStrategy,
    fan_in_params: req.fanInParams,
    mimir_write_routing: req.mimirWriteRouting,
  };
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

/**
 * Build an IPersonaStore backed by the Ravn REST API.
 *
 * @param client - HTTP client scoped to the shared API base (e.g. /api/v1).
 */
export function buildRavnPersonaAdapter(client: ApiClient): IPersonaStore {
  return {
    async listPersonas(filter: PersonaFilter = 'all') {
      const raw = await client.get<RawPersonaSummary[]>(`/personas?source=${filter}`);
      return raw.map(toSummary);
    },

    async getPersona(name: string) {
      const raw = await client.get<RawPersonaDetail>(`/personas/${encodeURIComponent(name)}`);
      return toDetail(raw);
    },

    async getPersonaYaml(name: string) {
      return client.get<string>(`/personas/${encodeURIComponent(name)}/yaml`);
    },

    async createPersona(req: PersonaCreateRequest) {
      const raw = await client.post<RawPersonaDetail>('/personas', toRequestBody(req));
      return toDetail(raw);
    },

    async updatePersona(name: string, req: PersonaCreateRequest) {
      const raw = await client.put<RawPersonaDetail>(
        `/personas/${encodeURIComponent(name)}`,
        toRequestBody(req),
      );
      return toDetail(raw);
    },

    async deletePersona(name: string) {
      await client.delete<void>(`/personas/${encodeURIComponent(name)}`);
    },

    async forkPersona(name: string, req: PersonaForkRequest) {
      const raw = await client.post<RawPersonaDetail>(
        `/personas/${encodeURIComponent(name)}/fork`,
        { new_name: req.newName },
      );
      return toDetail(raw);
    },
  };
}
