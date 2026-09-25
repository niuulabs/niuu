/**
 * Ravens workbench — how the fleet list reads a ravn.
 *
 * One vocabulary for state across the list, the detail header and the health
 * banner, derived from what the backend reports (observed/desired state for
 * managed residents, status otherwise). Nothing here invents a reason: when the
 * backend reports no failing condition, the reason is null and the UI says so.
 */

import type { Ravn, ResidentCondition } from '../domain/ravn';
import type { Session } from '../domain/session';
import { nameForRavn } from '../domain/residentActions';
import { belongsToRavn } from './conversations';

export type RavnLifeState =
  'running' | 'starting' | 'removing' | 'idle' | 'suspended' | 'stopped' | 'failed';

export const RAVN_STATE_LABEL: Record<RavnLifeState, string> = {
  running: 'Running',
  starting: 'Starting',
  removing: 'Removing',
  idle: 'Idle',
  suspended: 'Suspended',
  stopped: 'Stopped',
  failed: 'Failed',
};

/** Order the state groups read in: what needs you first, what is quiet last. */
const STATE_ORDER: RavnLifeState[] = [
  'failed',
  'starting',
  'removing',
  'running',
  'idle',
  'suspended',
  'stopped',
];

export function ravnLifeState(
  ravn: Pick<Ravn, 'status' | 'observedState' | 'desiredState'>,
): RavnLifeState {
  switch (ravn.observedState) {
    case 'active':
      return 'running';
    case 'pending':
    case 'deploying':
      return 'starting';
    case 'deleting':
      return 'removing';
    case 'suspended':
      return 'suspended';
    case 'failed':
      return 'failed';
    default:
      break;
  }
  switch (ravn.status) {
    case 'active':
      return 'running';
    case 'idle':
      return 'idle';
    case 'suspended':
      return 'suspended';
    case 'failed':
      return 'failed';
    case 'completed':
      return 'stopped';
  }
}

/** The first condition the backend reports as not satisfied. */
export function failingCondition(ravn: Pick<Ravn, 'conditions'>): ResidentCondition | null {
  return ravn.conditions?.find((condition) => condition.status !== 'true') ?? null;
}

export function needsAttention(
  ravn: Pick<Ravn, 'status' | 'observedState' | 'desiredState' | 'conditions'>,
): boolean {
  const state = ravnLifeState(ravn);
  if (state === 'failed') return true;
  if (state !== 'running') return false;
  return ravn.conditions?.some((condition) => condition.status === 'false') ?? false;
}

function words(identifier: string): string {
  return identifier
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_.-]+/g, ' ')
    .trim()
    .toLowerCase();
}

/**
 * A condition in words: `BackendReady` + `ReconcileFailed` reads
 * "Backend not ready · reconcile failed".
 */
export function describeCondition(condition: Pick<ResidentCondition, 'type' | 'reason'>): string {
  const type = words(condition.type);
  const subject = type.endsWith(' ready') ? `${type.slice(0, -' ready'.length)} not ready` : type;
  const head = subject.charAt(0).toUpperCase() + subject.slice(1);
  const reason = condition.reason ? words(condition.reason) : '';
  return reason ? `${head} · ${reason}` : head;
}

/** Where the ravn runs, for people: the target's name, else its slug, else the backend. */
export function ravnTarget(
  ravn: Pick<Ravn, 'instanceName' | 'instanceSlug' | 'location' | 'backend' | 'kind'>,
): string {
  const named = ravn.instanceName || ravn.instanceSlug || ravn.location || ravn.backend;
  if (named) return named;
  return ravn.kind === 'session' ? 'this Forge' : 'unassigned';
}

export function isSessionRavn(ravn: Pick<Ravn, 'kind'>): boolean {
  return ravn.kind === 'session';
}

/**
 * The fleet API lists residents; a Forge flock session is a ravn too — its
 * ravn daemons run as processes on a Forge — but it is served only as a live
 * session. Every live session no listed ravn owns becomes a ravn here, so the
 * workbench shows everything you can talk to.
 */
export function sessionBackedRavens(sessions: Session[], ravens: Ravn[]): Ravn[] {
  const owned = (session: Session) => ravens.some((ravn) => belongsToRavn(session, ravn));
  const byRavn = new Map<string, Session>();
  for (const session of sessions) {
    if (owned(session)) continue;
    const key = `${session.instanceId ?? ''}:${session.ravnId}`;
    const current = byRavn.get(key);
    if (!current || (session.status === 'running' && current.status !== 'running')) {
      byRavn.set(key, session);
    }
  }
  return [...byRavn.values()].map((session) => ({
    id: session.ravnId,
    // Forge names the session; which personas it runs is launch configuration.
    personaName: '',
    residentName: session.title || session.personaName,
    kind: 'session',
    managed: false,
    engine: 'ravn',
    status: session.status === 'running' ? 'active' : 'idle',
    // A live flock session that is not running yet is still being created.
    observedState: session.status === 'running' ? 'active' : 'pending',
    model: session.model,
    createdAt: session.createdAt,
    chatEndpoint: session.chatEndpoint ?? null,
    sessionId: session.id,
    ...(session.instanceId && { instanceId: session.instanceId }),
    ...(session.instanceName && { instanceName: session.instanceName }),
    ...(session.flockId && { flockId: session.flockId }),
    ...(session.flockRole && { flockRole: session.flockRole }),
    ...(session.messageCount !== undefined && { messageCount: session.messageCount }),
    ...(session.tokenCount !== undefined && { tokenCount: session.tokenCount }),
    ...(session.costUsd !== undefined && { costUsd: session.costUsd }),
  }));
}

/** The one line under a ravn's name in the list; null when there is nothing to add. */
export function ravnReasonLine(ravn: Ravn): string | null {
  const state = ravnLifeState(ravn);
  if (state === 'starting') return `Deploying on ${ravnTarget(ravn)}…`;
  if (state === 'removing') return `Removing from ${ravnTarget(ravn)}…`;
  if (state !== 'failed' && !needsAttention(ravn)) return null;
  const condition = failingCondition(ravn);
  if (!condition) return 'No failing condition reported';
  return describeCondition(condition);
}

export type RavnFilter = 'all' | 'attention' | 'running' | 'suspended';
export type RavnGrouping = 'state' | 'target' | 'engine' | 'flock';

export const RAVN_FILTERS: Array<{ id: RavnFilter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'attention', label: 'Attention' },
  { id: 'running', label: 'Running' },
  { id: 'suspended', label: 'Suspended' },
];

export const RAVN_GROUPINGS: Array<{ id: RavnGrouping; label: string }> = [
  { id: 'state', label: 'State' },
  { id: 'target', label: 'Target' },
  { id: 'engine', label: 'Engine' },
  { id: 'flock', label: 'Flock' },
];

export function matchesFilter(ravn: Ravn, filter: RavnFilter): boolean {
  switch (filter) {
    case 'all':
      return true;
    case 'attention':
      return needsAttention(ravn);
    case 'running':
      return ravnLifeState(ravn) === 'running';
    case 'suspended':
      return ravnLifeState(ravn) === 'suspended';
  }
}

export function matchesQuery(ravn: Ravn, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [
    nameForRavn(ravn),
    ravn.personaName,
    ravn.engine,
    ravn.backend,
    ravn.model,
    ravnTarget(ravn),
    ravn.flockRole,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
    .includes(needle);
}

export function filterCounts(ravens: Ravn[]): Record<RavnFilter, number> {
  return {
    all: ravens.length,
    attention: ravens.filter(needsAttention).length,
    running: ravens.filter((ravn) => ravnLifeState(ravn) === 'running').length,
    suspended: ravens.filter((ravn) => ravnLifeState(ravn) === 'suspended').length,
  };
}

export interface RavnGroup {
  key: string;
  label: string;
  ravens: Ravn[];
}

function byAttentionThenName(left: Ravn, right: Ravn): number {
  const attention = Number(needsAttention(right)) - Number(needsAttention(left));
  if (attention !== 0) return attention;
  return nameForRavn(left).localeCompare(nameForRavn(right));
}

function groupKey(ravn: Ravn, grouping: RavnGrouping): { key: string; label: string } {
  switch (grouping) {
    case 'state': {
      const state = ravnLifeState(ravn);
      return { key: state, label: RAVN_STATE_LABEL[state] };
    }
    case 'target': {
      const target = ravnTarget(ravn);
      return { key: ravn.instanceId || target, label: target };
    }
    case 'engine':
      return { key: ravn.engine ?? 'persona', label: ravn.engine ?? 'persona runtime' };
    case 'flock':
      return ravn.flockId
        ? { key: ravn.flockId, label: `Flock ${ravn.flockId.slice(0, 8)}` }
        : { key: '', label: 'Standalone' };
  }
}

/** A flock reads as the ravn that coordinates it, when one says so. */
function flockLabel(group: RavnGroup): string {
  const coordinator = group.ravens.find((ravn) => ravn.flockRole === 'coordinator');
  return coordinator ? `Flock · ${nameForRavn(coordinator)}` : group.label;
}

export function groupRavnsBy(ravens: Ravn[], grouping: RavnGrouping): RavnGroup[] {
  const groups = new Map<string, RavnGroup>();
  for (const ravn of ravens) {
    const { key, label } = groupKey(ravn, grouping);
    const group = groups.get(key) ?? { key, label, ravens: [] };
    group.ravens.push(ravn);
    groups.set(key, group);
  }
  const ordered = [...groups.values()].map((group) => ({
    ...group,
    label: grouping === 'flock' && group.key ? flockLabel(group) : group.label,
    ravens: [...group.ravens].sort(byAttentionThenName),
  }));
  if (grouping === 'state') {
    return ordered.sort(
      (left, right) =>
        STATE_ORDER.indexOf(left.key as RavnLifeState) -
        STATE_ORDER.indexOf(right.key as RavnLifeState),
    );
  }
  return ordered.sort((left, right) => {
    if (!left.key) return 1;
    if (!right.key) return -1;
    return left.label.localeCompare(right.label);
  });
}

/** Which ravn a page with no selection opens: whatever needs you, else the first by name. */
export function defaultRavn(ravens: Ravn[]): Ravn | null {
  return [...ravens].sort(byAttentionThenName)[0] ?? null;
}

export interface FleetUsage {
  tokens: number;
  costUsd: number;
  messages: number;
}

export function fleetUsage(ravens: Ravn[]): FleetUsage {
  return ravens.reduce<FleetUsage>(
    (total, ravn) => ({
      tokens: total.tokens + (ravn.tokenCount ?? 0),
      costUsd: total.costUsd + (ravn.costUsd ?? 0),
      messages: total.messages + (ravn.messageCount ?? 0),
    }),
    { tokens: 0, costUsd: 0, messages: 0 },
  );
}

export function formatTokens(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`;
  return String(count);
}

export function formatUsd(amount: number): string {
  if (amount === 0) return '$0';
  if (amount < 0.01) return '<$0.01';
  return `$${amount.toFixed(2)}`;
}
