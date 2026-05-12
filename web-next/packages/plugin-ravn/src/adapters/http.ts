/**
 * HTTP adapter for IPersonaStore.
 *
 * Adapted from web/src/modules/ravn/api/client.ts.
 * Translates snake_case server responses to camelCase domain types.
 *
 * Accepts any HTTP client structurally compatible with ApiClient from
 * @niuulabs/query (i.e. has get/post/put/delete methods).
 */

import { openEventStream, type ApiClient } from '@niuulabs/query';
import type { BudgetState, PersonaRole } from '@niuulabs/domain';
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
  IWardenStore,
  WardenSummary,
  WardenCreateRequest,
} from '../ports';
import type { Ravn, RavnStatus } from '../domain/ravn';
import type { Session, SessionStatus } from '../domain/session';
import type { Trigger, TriggerKind } from '../domain/trigger';
import type { Message, MessageKind } from '../domain/message';

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

interface RawWardenFeatures {
  wakefulness_enabled: boolean;
  dream_cycle_enabled: boolean;
  thread_queue_enabled: boolean;
  thread_enricher_enabled: boolean;
  recap_enabled: boolean;
  source_trigger_enabled: boolean;
  staleness_trigger_enabled: boolean;
}

interface RawWardenMimirBinding {
  mount_names: string[];
  write_mount: string;
  read_mount_names?: string[];
  write_mount_names?: string[];
  category_scope: string[];
}

interface RawWardenSchedules {
  dream_cycle_cron_expression: string;
  dream_cycle_poll_interval_seconds: number;
  source_trigger_poll_interval_seconds: number;
  staleness_trigger_schedule_hours: number;
}

interface RawWardenConsole {
  enabled: boolean;
  host: string;
  port: number;
  public_host?: string;
  auth_mode: 'noop' | 'token';
}

interface RawWardenRuntime {
  state?: 'active' | 'idle' | 'offline';
  pages_touched?: number;
  last_started_at?: string;
  last_dream?: {
    id: string;
    timestamp: string;
    ravn: string;
    mounts: string[];
    pages_updated: number;
    entities_created: number;
    lint_fixes: number;
    duration_ms: number;
  } | null;
}

interface RawWardenSupervisor {
  installed: boolean;
  service_label?: string;
  service_file?: string;
  config_file?: string;
  start_command?: string;
  stdout_log?: string;
  stderr_log?: string;
  last_install_at?: string;
  observation?: {
    status: 'running' | 'idle' | 'missing' | 'degraded' | 'unknown';
    detail?: string;
    source?: string;
    checked_at?: string;
    fields?: Array<{ label: string; value: string }>;
  };
}

interface RawWardenOperator {
  rune?: string;
  bio?: string;
  expertise?: string[];
  tools?: string[];
  role?: string;
}

interface RawWarden {
  id: string;
  name: string;
  persona: string;
  profile: string;
  model: string;
  deployment: string;
  deployment_kwargs?: Record<string, unknown>;
  mimir: RawWardenMimirBinding;
  features: RawWardenFeatures;
  schedules: RawWardenSchedules;
  console: RawWardenConsole;
  autostart: boolean;
  created_at: string;
  created_by: string;
  runtime?: RawWardenRuntime;
  supervisor?: RawWardenSupervisor;
  operator?: RawWardenOperator;
}

interface RawWardenLogEntry {
  id: string;
  source: string;
  line_number: number;
  raw: string;
  timestamp?: string;
  level?: string;
  logger?: string;
  message: string;
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

function toWarden(raw: RawWarden): WardenSummary {
  const schedules = raw.schedules ?? {
    dream_cycle_cron_expression: '0 3 * * *',
    dream_cycle_poll_interval_seconds: 60,
    source_trigger_poll_interval_seconds: 60,
    staleness_trigger_schedule_hours: 6,
  };
  const consoleConfig = raw.console ?? {
    enabled: true,
    host: '0.0.0.0',
    port: 8400,
    public_host: '',
    auth_mode: 'noop',
  };
  return {
    id: raw.id,
    name: raw.name,
    persona: raw.persona,
    profile: raw.profile,
    model: raw.model || 'claude-sonnet-4-6',
    deployment: raw.deployment,
    deploymentKwargs: raw.deployment_kwargs ?? {},
    mountNames: raw.mimir.mount_names,
    writeMount: raw.mimir.write_mount,
    readMountNames: raw.mimir.read_mount_names ?? raw.mimir.mount_names,
    writeMountNames:
      raw.mimir.write_mount_names ?? (raw.mimir.write_mount ? [raw.mimir.write_mount] : []),
    categoryScope: raw.mimir.category_scope,
    features: {
      wakefulnessEnabled: raw.features.wakefulness_enabled,
      dreamCycleEnabled: raw.features.dream_cycle_enabled,
      threadQueueEnabled: raw.features.thread_queue_enabled,
      threadEnricherEnabled: raw.features.thread_enricher_enabled,
      recapEnabled: raw.features.recap_enabled,
      sourceTriggerEnabled: raw.features.source_trigger_enabled,
      stalenessTriggerEnabled: raw.features.staleness_trigger_enabled,
    },
    schedules: {
      dreamCycleCronExpression: schedules.dream_cycle_cron_expression,
      dreamCyclePollIntervalSeconds: schedules.dream_cycle_poll_interval_seconds,
      sourceTriggerPollIntervalSeconds: schedules.source_trigger_poll_interval_seconds,
      stalenessTriggerScheduleHours: schedules.staleness_trigger_schedule_hours,
    },
    console: {
      enabled: consoleConfig.enabled,
      host: consoleConfig.host,
      port: consoleConfig.port,
      publicHost: consoleConfig.public_host,
      authMode: consoleConfig.auth_mode,
    },
    autostart: raw.autostart,
    createdAt: raw.created_at,
    createdBy: raw.created_by,
    runtime: raw.runtime
      ? {
          state: raw.runtime.state,
          pagesTouched: raw.runtime.pages_touched,
          lastStartedAt: raw.runtime.last_started_at,
          lastDream: raw.runtime.last_dream
            ? {
                id: raw.runtime.last_dream.id,
                timestamp: raw.runtime.last_dream.timestamp,
                ravn: raw.runtime.last_dream.ravn,
                mounts: raw.runtime.last_dream.mounts,
                pagesUpdated: raw.runtime.last_dream.pages_updated,
                entitiesCreated: raw.runtime.last_dream.entities_created,
                lintFixes: raw.runtime.last_dream.lint_fixes,
                durationMs: raw.runtime.last_dream.duration_ms,
              }
            : (raw.runtime.last_dream ?? null),
        }
      : undefined,
    supervisor: raw.supervisor
      ? {
          installed: raw.supervisor.installed,
          serviceLabel: raw.supervisor.service_label,
          serviceFile: raw.supervisor.service_file,
          configFile: raw.supervisor.config_file,
          startCommand: raw.supervisor.start_command,
          stdoutLog: raw.supervisor.stdout_log,
          stderrLog: raw.supervisor.stderr_log,
          lastInstallAt: raw.supervisor.last_install_at,
          observation: raw.supervisor.observation
            ? {
                status: raw.supervisor.observation.status,
                detail: raw.supervisor.observation.detail,
                source: raw.supervisor.observation.source,
                checkedAt: raw.supervisor.observation.checked_at,
                fields: raw.supervisor.observation.fields?.map((field) => ({
                  label: field.label,
                  value: field.value,
                })),
              }
            : undefined,
        }
      : undefined,
    operator: raw.operator
      ? {
          rune: raw.operator.rune,
          bio: raw.operator.bio,
          expertise: raw.operator.expertise,
          tools: raw.operator.tools,
          role: raw.operator.role as PersonaRole | undefined,
        }
      : undefined,
  };
}

function toWardenCreateBody(req: WardenCreateRequest): Record<string, unknown> {
  return {
    name: req.name,
    persona: req.persona,
    profile: req.profile,
    model: req.model,
    deployment: req.deployment,
    deployment_kwargs: req.deploymentKwargs ?? {},
    mount_names: req.mountNames ?? [],
    write_mount: req.writeMount ?? '',
    read_mount_names: req.readMountNames ?? [],
    write_mount_names: req.writeMountNames ?? [],
    category_scope: req.categoryScope ?? [],
    features: req.features
      ? {
          wakefulness_enabled: req.features.wakefulnessEnabled,
          dream_cycle_enabled: req.features.dreamCycleEnabled,
          thread_queue_enabled: req.features.threadQueueEnabled,
          thread_enricher_enabled: req.features.threadEnricherEnabled,
          recap_enabled: req.features.recapEnabled,
          source_trigger_enabled: req.features.sourceTriggerEnabled,
          staleness_trigger_enabled: req.features.stalenessTriggerEnabled,
        }
      : undefined,
    schedules: req.schedules
      ? {
          dream_cycle_cron_expression: req.schedules.dreamCycleCronExpression,
          dream_cycle_poll_interval_seconds: req.schedules.dreamCyclePollIntervalSeconds,
          source_trigger_poll_interval_seconds: req.schedules.sourceTriggerPollIntervalSeconds,
          staleness_trigger_schedule_hours: req.schedules.stalenessTriggerScheduleHours,
        }
      : undefined,
    console: req.console
      ? {
          enabled: req.console.enabled,
          host: req.console.host,
          port: req.console.port,
          public_host: req.console.publicHost,
          auth_mode: req.console.authMode,
        }
      : undefined,
    autostart: req.autostart,
    created_by: req.createdBy,
  };
}

function toWardenLogEntry(raw: RawWardenLogEntry) {
  return {
    id: raw.id,
    source: raw.source,
    lineNumber: raw.line_number,
    raw: raw.raw,
    timestamp: raw.timestamp,
    level: raw.level,
    logger: raw.logger,
    message: raw.message,
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

/**
 * Build an IWardenStore backed by the Ravn REST API.
 */
export function buildRavnWardenAdapter(client: ApiClient): IWardenStore {
  return {
    async listWardens() {
      const raw = await client.get<RawWarden[]>('/wardens');
      return raw.map(toWarden);
    },
    async getWarden(id) {
      const raw = await client.get<RawWarden>(`/wardens/${encodeURIComponent(id)}`);
      return toWarden(raw);
    },
    async createWarden(req) {
      const raw = await client.post<RawWarden>('/wardens', toWardenCreateBody(req));
      return toWarden(raw);
    },
    subscribeWarden(id, listener) {
      const basePath = client.basePath ?? '';
      const handle = openEventStream(`${basePath}/wardens/${encodeURIComponent(id)}/stream`, {
        onMessage: (raw) => {
          try {
            listener(toWarden(JSON.parse(raw) as RawWarden));
          } catch {
            // Drop malformed frames and wait for the next update.
          }
        },
      });
      return () => {
        handle.close();
      };
    },
    async observeWarden(id) {
      const raw = await client.post<RawWarden>(`/wardens/${encodeURIComponent(id)}/observe`, {});
      return toWarden(raw);
    },
    async installWarden(id) {
      const raw = await client.post<RawWarden>(`/wardens/${encodeURIComponent(id)}/install`, {});
      return toWarden(raw);
    },
    async startWarden(id) {
      const raw = await client.post<RawWarden>(`/wardens/${encodeURIComponent(id)}/start`, {});
      return toWarden(raw);
    },
    async stopWarden(id) {
      const raw = await client.post<RawWarden>(`/wardens/${encodeURIComponent(id)}/stop`, {});
      return toWarden(raw);
    },
    async uninstallWarden(id) {
      const raw = await client.post<RawWarden>(`/wardens/${encodeURIComponent(id)}/uninstall`, {});
      return toWarden(raw);
    },
    async getWardenLogs(id, options) {
      const params = new URLSearchParams();
      if (options?.stream) params.set('stream', options.stream);
      if (options?.limit) params.set('limit', String(options.limit));
      const suffix = params.size > 0 ? `?${params.toString()}` : '';
      const raw = await client.get<RawWardenLogEntry[]>(
        `/wardens/${encodeURIComponent(id)}/logs${suffix}`,
      );
      return raw.map(toWardenLogEntry);
    },
    async getWardenActivity(id, limit) {
      const suffix = limit ? `?limit=${encodeURIComponent(String(limit))}` : '';
      const raw = await client.get<RawWardenLogEntry[]>(
        `/wardens/${encodeURIComponent(id)}/activity${suffix}`,
      );
      return raw.map(toWardenLogEntry);
    },
  };
}
