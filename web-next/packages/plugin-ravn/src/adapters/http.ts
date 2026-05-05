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
import type { BudgetState } from '@niuulabs/domain';
import type {
  IPersonaStore,
  PersonaSummary,
  PersonaDetail,
  PersonaCreateRequest,
  PersonaForkRequest,
  PersonaFilter,
  IRavenStream,
  ISessionStream,
  ITriggerStore,
  IBudgetStream,
} from '../ports';
import type { Ravn, RavnStatus } from '../domain/ravn';
import type { Session, SessionStatus } from '../domain/session';
import type { Trigger, TriggerKind } from '../domain/trigger';
import type { Message, MessageKind } from '../domain/message';

// ---------------------------------------------------------------------------
// Internal raw types (snake_case server responses)
// ---------------------------------------------------------------------------

interface RawPersonaLLM {
  primary_alias: string;
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

interface RawPersonaExecutor {
  adapter: string;
  kwargs?: Record<string, unknown>;
}

interface RawPersonaSummary {
  name: string;
  role: string;
  letter: string;
  color: string;
  summary: string;
  permission_mode: string;
  allowed_tools: string[];
  iteration_budget: number;
  is_builtin: boolean;
  has_override: boolean;
  produces_event: string;
  consumes_events: string[];
}

interface RawPersonaDetail extends RawPersonaSummary {
  description: string;
  system_prompt_template: string;
  forbidden_tools: string[];
  executor?: RawPersonaExecutor;
  llm: RawPersonaLLM & { temperature?: number };
  produces: RawPersonaProduces;
  consumes: RawPersonaConsumes;
  fan_in: RawPersonaFanIn;
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
    description: raw.description,
    systemPromptTemplate: raw.system_prompt_template,
    forbiddenTools: raw.forbidden_tools,
    executor: raw.executor
      ? {
          adapter: raw.executor.adapter,
          kwargs: raw.executor.kwargs ?? {},
        }
      : undefined,
    llm: {
      primaryAlias: raw.llm.primary_alias,
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
    fanIn: {
      strategy: raw.fan_in.strategy,
      params: raw.fan_in.params,
    },
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
    allowed_tools: req.allowedTools,
    forbidden_tools: req.forbiddenTools,
    permission_mode: req.permissionMode,
    executor: req.executor
      ? {
          adapter: req.executor.adapter,
          kwargs: req.executor.kwargs,
        }
      : null,
    iteration_budget: req.iterationBudget,
    llm_primary_alias: req.llmPrimaryAlias,
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

// ---------------------------------------------------------------------------
// Ravns, Sessions, Triggers, Budget — raw wire shapes
// ---------------------------------------------------------------------------

interface RawRavn {
  id: string;
  persona_name: string;
  status: string;
  model: string;
  created_at: string;
  updated_at?: string;
  role?: string;
  letter?: string;
}

interface RawSession {
  id: string;
  ravn_id: string;
  persona_name: string;
  status: string;
  model: string;
  created_at: string;
}

interface RawMessage {
  id: string;
  session_id: string;
  kind: string;
  content: string;
  ts: string;
  tool_name?: string;
}

interface RawTrigger {
  id: string;
  kind: string;
  persona_name: string;
  spec: string;
  enabled: boolean;
  created_at: string;
}

interface RawBudgetState {
  spent_usd: number;
  cap_usd: number;
  warn_at: number;
}

function toRavn(raw: RawRavn): Ravn {
  return {
    id: raw.id,
    personaName: raw.persona_name,
    status: raw.status as RavnStatus,
    model: raw.model,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
    role: raw.role as Ravn['role'],
    letter: raw.letter,
  };
}

function toSession(raw: RawSession): Session {
  return {
    id: raw.id,
    ravnId: raw.ravn_id,
    personaName: raw.persona_name,
    status: raw.status as SessionStatus,
    model: raw.model,
    createdAt: raw.created_at,
  };
}

function toMessage(raw: RawMessage): Message {
  return {
    id: raw.id,
    sessionId: raw.session_id,
    kind: raw.kind as MessageKind,
    content: raw.content,
    ts: raw.ts,
    toolName: raw.tool_name,
  };
}

function toTrigger(raw: RawTrigger): Trigger {
  return {
    id: raw.id,
    kind: raw.kind as TriggerKind,
    personaName: raw.persona_name,
    spec: raw.spec,
    enabled: raw.enabled,
    createdAt: raw.created_at,
  };
}

function toBudgetState(raw: RawBudgetState): BudgetState {
  return {
    spentUsd: raw.spent_usd,
    capUsd: raw.cap_usd,
    warnAt: raw.warn_at,
  };
}

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------

/**
 * Build an IRavenStream backed by the Ravn REST API.
 * @param client - HTTP client scoped to the Ravn base path.
 */
export function buildRavnRavenAdapter(client: ApiClient): IRavenStream {
  return {
    async listRavens() {
      const raw = await client.get<RawRavn[]>('/ravens');
      return raw.map(toRavn);
    },
    async getRaven(id) {
      const raw = await client.get<RawRavn>(`/ravens/${encodeURIComponent(id)}`);
      return toRavn(raw);
    },
  };
}

/**
 * Build an ISessionStream backed by the Ravn REST API.
 */
export function buildRavnSessionAdapter(client: ApiClient): ISessionStream {
  return {
    async listSessions() {
      const raw = await client.get<RawSession[]>('/sessions');
      return raw.map(toSession);
    },
    async getSession(id) {
      const raw = await client.get<RawSession>(`/sessions/${encodeURIComponent(id)}`);
      return toSession(raw);
    },
    async getMessages(sessionId) {
      const raw = await client.get<RawMessage[]>(
        `/sessions/${encodeURIComponent(sessionId)}/messages`,
      );
      return raw.map(toMessage);
    },
  };
}

/**
 * Build an ITriggerStore backed by the Ravn REST API.
 */
export function buildRavnTriggerAdapter(client: ApiClient): ITriggerStore {
  return {
    async listTriggers() {
      const raw = await client.get<RawTrigger[]>('/triggers');
      return raw.map(toTrigger);
    },
    async createTrigger(t) {
      const body = {
        kind: t.kind,
        persona_name: t.personaName,
        spec: t.spec,
        enabled: t.enabled,
      };
      const raw = await client.post<RawTrigger>('/triggers', body);
      return toTrigger(raw);
    },
    async deleteTrigger(id) {
      await client.delete<void>(`/triggers/${encodeURIComponent(id)}`);
    },
  };
}

/**
 * Build an IBudgetStream backed by the Ravn REST API.
 * Not a true push stream — each call fetches the current state.
 */
export function buildRavnBudgetAdapter(client: ApiClient): IBudgetStream {
  return {
    async getBudget(ravnId) {
      const raw = await client.get<RawBudgetState>(`/budget/${encodeURIComponent(ravnId)}`);
      return toBudgetState(raw);
    },
    async getFleetBudget() {
      const raw = await client.get<RawBudgetState>('/budget/fleet');
      return toBudgetState(raw);
    },
  };
}
