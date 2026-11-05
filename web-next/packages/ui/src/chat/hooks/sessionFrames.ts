/**
 * Parsers for the Skuld broker frames that describe the session itself rather than the
 * conversation: the agent's plan, its subagents, the reasoning effort and model choices,
 * token usage, and where a sent message is in delivery.
 *
 * Every parser returns `null` for a frame that is not shaped like the broker's contract
 * (src/skuld/transports/*.py, src/skuld/effort.py, src/skuld/broker.py) so the caller
 * ignores the frame as a whole; none of them invents values for missing fields.
 */
import type {
  SessionAgent,
  SessionEffort,
  SessionPlan,
  SessionPlanTask,
  SessionRuntimeModel,
  SessionRuntimeOptions,
  SessionUsage,
  UserMessageDelivery,
  UserMessageDeliveryState,
} from '../types';

type Frame = Record<string, unknown>;

function isRecord(value: unknown): value is Frame {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined;
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((entry): entry is string => typeof entry === 'string')
    : [];
}

/** `plan` frame: `{tasks: [{content, status, activeForm?}], counts}`. */
export function parsePlanFrame(frame: Frame): SessionPlan | null {
  if (!Array.isArray(frame.tasks)) return null;
  const tasks: SessionPlanTask[] = [];
  for (const raw of frame.tasks) {
    if (!isRecord(raw)) continue;
    const content = str(raw.content);
    const status = str(raw.status);
    if (!content || !status) continue;
    const activeForm = str(raw.activeForm);
    tasks.push({ content, status, ...(activeForm ? { activeForm } : {}) });
  }
  const count = (status: string) => tasks.filter((task) => task.status === status).length;
  return {
    tasks,
    counts: {
      total: tasks.length,
      pending: count('pending'),
      in_progress: count('in_progress'),
      completed: count('completed'),
    },
  };
}

export interface AgentUpdate {
  action: 'started' | 'stopped';
  agent: Partial<SessionAgent> & { id: string };
}

/** `agent_update` frame: `{action, agent: {id, kind, name, status, description?, model?, ...}}`. */
export function parseAgentUpdate(frame: Frame): AgentUpdate | null {
  const { action, agent } = frame;
  if (action !== 'started' && action !== 'stopped') return null;
  if (!isRecord(agent)) return null;
  const id = str(agent.id);
  if (!id) return null;
  const kind = str(agent.kind);
  const name = str(agent.name);
  const description = str(agent.description);
  const model = str(agent.model);
  const startedAt = str(agent.started_at);
  const endedAt = str(agent.ended_at);
  return {
    action,
    agent: {
      id,
      ...(kind ? { kind } : {}),
      ...(name ? { name } : {}),
      // The frame's own action says whether it is running; a stopped agent without an
      // explicit status finished normally.
      status: str(agent.status) ?? (action === 'stopped' ? 'done' : 'running'),
      ...(description ? { description } : {}),
      ...(model ? { model } : {}),
      ...(startedAt ? { startedAt } : {}),
      ...(endedAt ? { endedAt } : {}),
    },
  };
}

/**
 * Upsert by id, keeping finished agents with their status. A frame for an agent the
 * session has not announced needs a kind and name to be shown; without them it is
 * not an agent we can describe, so the list is returned unchanged.
 */
export function upsertAgent(agents: SessionAgent[], update: AgentUpdate): SessionAgent[] {
  const index = agents.findIndex((agent) => agent.id === update.agent.id);
  if (index >= 0) {
    return agents.map((agent, i) => (i === index ? { ...agent, ...update.agent } : agent));
  }
  const { kind, name, status } = update.agent;
  if (!kind || !name || !status) return agents;
  return [...agents, { ...update.agent, kind, name, status }];
}

/** `effort_status` frame: `{current, levels, mutable, applies_to}`. */
export function parseEffortFrame(frame: Frame): SessionEffort | null {
  if (!Array.isArray(frame.levels) || typeof frame.mutable !== 'boolean') return null;
  const appliesTo = str(frame.applies_to);
  return {
    current: str(frame.current) ?? null,
    levels: strings(frame.levels),
    mutable: frame.mutable,
    ...(appliesTo ? { appliesTo } : {}),
  };
}

function parseRuntimeModel(raw: unknown): SessionRuntimeModel | null {
  if (!isRecord(raw)) return null;
  const id = str(raw.id);
  const model = str(raw.model);
  const name = str(raw.name);
  if (!id || !model || !name) return null;
  const description = str(raw.description);
  const defaultEffort = str(raw.default_effort);
  return {
    id,
    model,
    name,
    ...(description ? { description } : {}),
    isDefault: raw.default === true,
    hidden: raw.hidden === true,
    effortLevels: strings(raw.effort_levels),
    ...(defaultEffort ? { defaultEffort } : {}),
  };
}

/** `runtime_options` frame: `{models: [...], current: {model, effort}, applies_to}`. */
export function parseRuntimeOptionsFrame(frame: Frame): SessionRuntimeOptions | null {
  if (!Array.isArray(frame.models) || !isRecord(frame.current)) return null;
  const model = str(frame.current.model);
  if (!model) return null;
  const appliesTo = str(frame.applies_to);
  return {
    current: { model, effort: str(frame.current.effort) ?? null },
    models: frame.models.flatMap((entry) => {
      const parsed = parseRuntimeModel(entry);
      return parsed ? [parsed] : [];
    }),
    ...(appliesTo ? { appliesTo } : {}),
  };
}

/**
 * A runtime with a model catalog lists each model's effort levels, so the effort control
 * reads them from the current model instead of asking over the socket.
 */
export function effortFromRuntimeOptions(
  options: SessionRuntimeOptions | null,
): SessionEffort | null {
  if (!options) return null;
  const model = options.models.find((entry) => entry.model === options.current.model);
  if (!model || model.effortLevels.length === 0) return null;
  return {
    current: options.current.effort ?? null,
    levels: model.effortLevels,
    mutable: true,
    ...(options.appliesTo ? { appliesTo: options.appliesTo } : {}),
  };
}

/** Codex splits an input counter into fresh input, cache reads and cache writes. */
function tokensIn(raw: unknown): number | undefined {
  if (!isRecord(raw)) return undefined;
  const { input, cache_read: read, cache_write: write } = raw;
  if (typeof input !== 'number') return undefined;
  return input + (typeof read === 'number' ? read : 0) + (typeof write === 'number' ? write : 0);
}

/** `session_usage` frame: `{model, last, total, window}` where `last`/`total` may be null. */
export function parseUsageFrame(frame: Frame): SessionUsage | null {
  const model = str(frame.model);
  const contextTokens = tokensIn(frame.last);
  const totalInputTokens = tokensIn(frame.total);
  const total = isRecord(frame.total) ? frame.total : undefined;
  const totalOutputTokens = typeof total?.output === 'number' ? total.output : undefined;
  const window = typeof frame.window === 'number' && frame.window > 0 ? frame.window : undefined;
  if (contextTokens === undefined && totalInputTokens === undefined && window === undefined) {
    return null;
  }
  return {
    ...(model ? { model } : {}),
    ...(contextTokens !== undefined ? { contextTokens } : {}),
    ...(window !== undefined ? { window } : {}),
    ...(totalInputTokens !== undefined ? { totalInputTokens } : {}),
    ...(totalOutputTokens !== undefined ? { totalOutputTokens } : {}),
  };
}

/** The broker's `steering_state` on a turn or a `user_confirmed` frame, as a delivery state. */
export function deliveryFromSteeringState(state: unknown): UserMessageDelivery | undefined {
  if (state === 'pending') return { state: 'queued' };
  if (state === 'active') return { state: 'active' };
  if (state === 'failed') return { state: 'failed' };
  return undefined;
}

const DELIVERY_FRAMES: Record<string, UserMessageDeliveryState> = {
  user_active: 'active',
  user_consumed: 'active',
  user_delivered: 'delivered',
  user_delivery_pending: 'unconfirmed',
  user_delivery_failed: 'failed',
};

export interface DeliveryFrame {
  /** The user message's id, and the request id it was sent under (the optimistic id). */
  ids: string[];
  delivery: UserMessageDelivery;
}

/** A delivery frame (`user_active`, `user_consumed`, `user_delivered`, ...) for one message. */
export function parseDeliveryFrame(frame: Frame): DeliveryFrame | null {
  const state = typeof frame.type === 'string' ? DELIVERY_FRAMES[frame.type] : undefined;
  if (!state) return null;
  // user_consumed names the message `msg_id`; every other delivery frame names it `id`.
  const ids = [str(frame.id) ?? str(frame.msg_id), str(frame.request_id)].filter(
    (id): id is string => id !== undefined,
  );
  if (ids.length === 0) return null;
  const error = str(frame.error);
  return { ids, delivery: { state, ...(error ? { error } : {}) } };
}

const DELIVERY_RANK: Record<UserMessageDeliveryState, number> = {
  queued: 0,
  unconfirmed: 1,
  delivered: 2,
  failed: 3,
  active: 4,
};

/**
 * Delivery only moves forward: the agent taking a message is final (the broker likewise
 * leaves an `active` turn active when a late delivery failure arrives), and an earlier
 * state never replaces a later one.
 */
export function advanceDelivery(
  current: UserMessageDelivery | undefined,
  next: UserMessageDelivery,
): UserMessageDelivery {
  if (!current) return next;
  return DELIVERY_RANK[next.state] >= DELIVERY_RANK[current.state] ? next : current;
}
