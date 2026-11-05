import type { SessionAgent, SessionUsage } from '@niuulabs/ui';
import type { SessionState } from '../../domain/session';
import type { VolundrSession } from '../../models/volundr.model';

/** The context ring turns to the warning colour above this share of the window. */
export const CONTEXT_WARN_PERCENT = 80;

export interface ContextMeterData {
  /** Whole percent of the window in use, 0-100 for the ring. */
  percent: number;
  used: number;
  window: number;
}

/**
 * The context ring needs both the tokens in use and the window they fill. A runtime that
 * reports only one of them has no ring to draw, so there is none.
 */
export function contextMeterData(usage: SessionUsage | null | undefined): ContextMeterData | null {
  const used = usage?.contextTokens;
  const window = usage?.window;
  if (used == null || window == null) return null;
  if (!Number.isFinite(used) || !Number.isFinite(window) || window <= 0 || used < 0) return null;
  return { percent: Math.min(100, Math.round((used / window) * 100)), used, window };
}

/** True while the runtime is producing a turn (thinking or running a tool). */
export function isSessionWorking(activityState: VolundrSession['activityState']): boolean {
  return activityState === 'active' || activityState === 'tool_executing';
}

export type StateTone = 'working' | 'attention' | 'critical' | 'muted';

/** How the header pill is coloured for a lifecycle state. */
export function stateTone(state: SessionState): StateTone {
  if (state === 'running') return 'working';
  if (state === 'awaiting_input') return 'attention';
  if (state === 'failed') return 'critical';
  return 'muted';
}

const MS_PER_SECOND = 1000;
const SECONDS_PER_MINUTE = 60;
const MINUTES_PER_HOUR = 60;
const HOURS_PER_DAY = 24;

/** "42s", "4m", "1h 12m", "2d 3h" - the largest two units that carry information. */
export function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / MS_PER_SECOND));
  if (seconds < SECONDS_PER_MINUTE) return `${seconds}s`;
  const minutes = Math.floor(seconds / SECONDS_PER_MINUTE);
  if (minutes < MINUTES_PER_HOUR) return `${minutes}m`;
  const hours = Math.floor(minutes / MINUTES_PER_HOUR);
  if (hours < HOURS_PER_DAY) return `${hours}h ${minutes % MINUTES_PER_HOUR}m`;
  return `${Math.floor(hours / HOURS_PER_DAY)}d ${hours % HOURS_PER_DAY}h`;
}

/** Time an agent has run: to its end, or to `now` while it is still going. */
export function agentDuration(agent: SessionAgent, now: number): string | null {
  const start = Date.parse(agent.startedAt ?? '');
  if (!Number.isFinite(start)) return null;
  const end = agent.endedAt ? Date.parse(agent.endedAt) : now;
  if (!Number.isFinite(end)) return null;
  return formatDuration(end - start);
}

const HARNESS_LABEL = { claude: 'Claude', codex: 'Codex' } as const;

export interface SessionInfoRow {
  id: 'engine' | 'host' | 'workspace' | 'project' | 'started';
  label: string;
  value: string;
  /** Text after the value, in the normal colour ("Claude"). */
  detail?: string;
  harness?: 'claude' | 'codex';
  mono?: boolean;
}

/** The Session section's rows; a fact the session does not have is not a row. */
export function sessionInfoRows(facts: {
  engine?: { model: string; harness?: 'claude' | 'codex' };
  host?: string | null;
  workspace?: string | null;
  project?: string | null;
  startedAt?: string | null;
}): SessionInfoRow[] {
  const rows: SessionInfoRow[] = [];
  if (facts.engine?.model) {
    const { model, harness } = facts.engine;
    rows.push({
      id: 'engine',
      label: 'Engine',
      value: model,
      harness,
      detail: harness ? HARNESS_LABEL[harness] : undefined,
    });
  }
  if (facts.host) rows.push({ id: 'host', label: 'Host', value: facts.host });
  if (facts.workspace) {
    rows.push({ id: 'workspace', label: 'Workspace', value: facts.workspace, mono: true });
  }
  if (facts.project) rows.push({ id: 'project', label: 'Project', value: facts.project });
  const started = Date.parse(facts.startedAt ?? '');
  if (Number.isFinite(started)) {
    const value = new Date(started).toLocaleString([], {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
    rows.push({ id: 'started', label: 'Started', value });
  }
  return rows;
}
