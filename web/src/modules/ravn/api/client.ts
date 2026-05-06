/**
 * Ravn API client.
 *
 * Wraps the shared ApiClient for Ravn persona endpoints.
 * All persona routes are under /api/v1/ravn.
 */

import { createApiClient } from '@/modules/shared/api/client';
import type {
  PersonaSummary,
  PersonaDetail,
  PersonaCreateRequest,
  PersonaForkRequest,
  PersonaFilter,
} from './types';

// ---------------------------------------------------------------------------
// Internal raw types (snake_case server responses)
// ---------------------------------------------------------------------------

const api = createApiClient('/api/v1/ravn');

interface RawPersonaLLM {
  primary_alias: string;
  thinking_enabled: boolean;
  max_tokens: number;
}

interface RawPersonaProduces {
  event_type: string;
  schema_def: Record<string, unknown>;
}

interface RawPersonaConsumes {
  event_types: string[];
  injects: string[];
}

interface RawPersonaFanIn {
  strategy: string;
  contributes_to: string;
}

interface RawPersonaSummary {
  name: string;
  permission_mode: string;
  allowed_tools: string[];
  iteration_budget: number;
  is_builtin: boolean;
  has_override: boolean;
  produces_event: string;
  consumes_events: string[];
}

interface RawPersonaDetail extends RawPersonaSummary {
  system_prompt_template: string;
  forbidden_tools: string[];
  llm: RawPersonaLLM;
  produces: RawPersonaProduces;
  consumes: RawPersonaConsumes;
  fan_in: RawPersonaFanIn;
  yaml_source: string;
}

// ---------------------------------------------------------------------------
// Transform functions
// ---------------------------------------------------------------------------

function toSummary(raw: RawPersonaSummary): PersonaSummary {
  return {
    name: raw.name,
    permissionMode: raw.permission_mode,
    allowedTools: raw.allowed_tools,
    iterationBudget: raw.iteration_budget,
    isBuiltin: raw.is_builtin,
    hasOverride: raw.has_override,
    producesEvent: raw.produces_event,
    consumesEvents: raw.consumes_events,
  };
}

function toDetail(raw: RawPersonaDetail): PersonaDetail {
  return {
    ...toSummary(raw),
    systemPromptTemplate: raw.system_prompt_template,
    forbiddenTools: raw.forbidden_tools,
    llm: {
      primaryAlias: raw.llm.primary_alias,
      thinkingEnabled: raw.llm.thinking_enabled,
      maxTokens: raw.llm.max_tokens,
    },
    produces: {
      eventType: raw.produces.event_type,
      schemaDef: raw.produces.schema_def,
    },
    consumes: {
      eventTypes: raw.consumes.event_types,
      injects: raw.consumes.injects,
    },
    fanIn: {
      strategy: raw.fan_in.strategy,
      contributesTo: raw.fan_in.contributes_to,
    },
    yamlSource: raw.yaml_source,
  };
}

function toRequestBody(req: PersonaCreateRequest): Record<string, unknown> {
  return {
    name: req.name,
    system_prompt_template: req.systemPromptTemplate,
    allowed_tools: req.allowedTools,
    forbidden_tools: req.forbiddenTools,
    permission_mode: req.permissionMode,
    iteration_budget: req.iterationBudget,
    llm_primary_alias: req.llmPrimaryAlias,
    llm_thinking_enabled: req.llmThinkingEnabled,
    llm_max_tokens: req.llmMaxTokens,
    produces_event_type: req.producesEventType,
    consumes_event_types: req.consumesEventTypes,
    consumes_injects: req.consumesInjects,
    fan_in_strategy: req.fanInStrategy,
    fan_in_contributes_to: req.fanInContributesTo,
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** GET /personas?source= */
export async function listPersonas(filter: PersonaFilter = 'all'): Promise<PersonaSummary[]> {
  const raw = await api.get<RawPersonaSummary[]>(`/personas?source=${filter}`);
  return raw.map(toSummary);
}

/** GET /personas/:name */
export async function getPersona(name: string): Promise<PersonaDetail> {
  const raw = await api.get<RawPersonaDetail>(`/personas/${encodeURIComponent(name)}`);
  return toDetail(raw);
}

/** GET /personas/:name/yaml */
export async function getPersonaYaml(name: string): Promise<string> {
  return api.get<string>(`/personas/${encodeURIComponent(name)}/yaml`);
}

/** POST /personas */
export async function createPersona(req: PersonaCreateRequest): Promise<PersonaDetail> {
  const raw = await api.post<RawPersonaDetail>('/personas', toRequestBody(req));
  return toDetail(raw);
}

/** PUT /personas/:name */
export async function updatePersona(
  name: string,
  req: PersonaCreateRequest
): Promise<PersonaDetail> {
  const raw = await api.put<RawPersonaDetail>(
    `/personas/${encodeURIComponent(name)}`,
    toRequestBody(req)
  );
  return toDetail(raw);
}

/** DELETE /personas/:name */
export async function deletePersona(name: string): Promise<void> {
  await api.delete<void>(`/personas/${encodeURIComponent(name)}`);
}

/** POST /personas/:name/fork */
export async function forkPersona(name: string, req: PersonaForkRequest): Promise<PersonaDetail> {
  const raw = await api.post<RawPersonaDetail>(`/personas/${encodeURIComponent(name)}/fork`, {
    new_name: req.newName,
  });
  return toDetail(raw);
}
