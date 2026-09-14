import type { DotState } from '@niuulabs/ui';
import type { Session, SessionState } from '../../domain/session';

// ---------------------------------------------------------------------------
// Pod group definitions — maps display labels to session states
// ---------------------------------------------------------------------------

export interface PodGroupDef {
  label: string;
  states: SessionState[];
}

export interface SessionSection {
  label: string;
  sessions: Session[];
}

export const POD_GROUPS: PodGroupDef[] = [
  { label: 'NEEDS YOU', states: ['awaiting_input'] },
  { label: 'ACTIVE', states: ['running'] },
  { label: 'IDLE', states: ['idle'] },
  { label: 'BOOTING', states: ['provisioning', 'requested'] },
  { label: 'ERROR', states: ['failed'] },
  { label: 'STOPPED', states: ['terminated'] },
  { label: 'ARCHIVED', states: ['archived'] },
];

/** States from which a session can still be stopped. */
export const STOPPABLE_STATES: SessionState[] = [
  'running',
  'awaiting_input',
  'idle',
  'provisioning',
  'requested',
  'ready',
  'terminating',
];

// ---------------------------------------------------------------------------
// Session state → dot state mapping
// ---------------------------------------------------------------------------

export const SESSION_DOT: Record<SessionState, DotState> = {
  running: 'running',
  idle: 'idle',
  awaiting_input: 'attention',
  provisioning: 'processing',
  requested: 'queued',
  ready: 'healthy',
  terminating: 'degraded',
  terminated: 'archived',
  archived: 'archived',
  failed: 'failed',
};

export function looksLikeRepoLabel(value: string): boolean {
  return (
    value.includes('#') ||
    value.startsWith('~/') ||
    value.startsWith('/') ||
    value.startsWith('http')
  );
}

export function compactSourceParts(value: string): { label: string; branch?: string } {
  if (value.includes('#')) {
    const [repo, branch] = value.split('#');
    return { label: shortenRepoLabel(repo ?? value), branch: branch || undefined };
  }
  return { label: shortenRepoLabel(value) };
}

/** Collapse a leading "/home/<user>/" (incl. literal "/home/thor/") to "~/". */
function homeToTilde(value: string): string {
  return value.replace(/^\/(?:home|Users)\/[^/]+\//, '~/');
}

export function shortenRepoLabel(value: string): string {
  if (value.startsWith('~/') || value.startsWith('/')) return homeToTilde(value);
  const trimmed = value.replace(/\/+$/, '');
  const slug = trimmed.split('/').pop() ?? trimmed;
  return slug.replace(/\.git$/, '') || value;
}

export function toGroupTestId(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export function sessionActivityTs(session: Session): number {
  return new Date(session.lastActivityAt ?? session.startedAt).getTime();
}

const EXTERNAL_ORIGINS = new Set(['claude', 'codex']);

/** External origins ('claude' / 'codex') get a badge; volundr-native rows do not. */
export function sessionOriginBadge(session: Pick<Session, 'origin'>): string | null {
  if (!session.origin) return null;
  if (!EXTERNAL_ORIGINS.has(session.origin)) return null;
  return session.origin;
}

export function compareSessionsByActivity(a: Session, b: Session): number {
  return sessionActivityTs(b) - sessionActivityTs(a);
}

export function repoGroupLabel(session: Session): string {
  if (session.preview && looksLikeRepoLabel(session.preview)) {
    return compactSourceParts(session.preview).label;
  }
  if (session.personaName.startsWith('~/') || session.personaName.startsWith('/')) {
    return homeToTilde(session.personaName);
  }
  return 'other';
}

export function groupByRepo(sessions: Session[]): SessionSection[] {
  const grouped = new Map<string, Session[]>();

  for (const session of sessions) {
    const label = repoGroupLabel(session);
    const bucket = grouped.get(label);
    if (bucket) {
      bucket.push(session);
    } else {
      grouped.set(label, [session]);
    }
  }

  return [...grouped.entries()]
    .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
    .map(([label, groupedSessions]) => ({
      label,
      sessions: [...groupedSessions].sort(compareSessionsByActivity),
    }));
}

export function forgeGroupLabel(session: Session): string {
  return session.clusterName ?? session.clusterId ?? 'unknown forge';
}

// ---------------------------------------------------------------------------
// Filtering and Simple-mode grouping
// ---------------------------------------------------------------------------

/** The client-side search both session lists use: name, id, repo, forge. */
export function filterSessionsByQuery(sessions: Session[], query: string): Session[] {
  const q = query.trim().toLowerCase();
  if (!q) return sessions;
  return sessions.filter(
    (session) =>
      session.id.toLowerCase().includes(q) ||
      session.name?.toLowerCase().includes(q) ||
      session.personaName.toLowerCase().includes(q) ||
      session.preview?.toLowerCase().includes(q) ||
      session.clusterName?.toLowerCase().includes(q) ||
      session.clusterId?.toLowerCase().includes(q),
  );
}

/** How long a finished session stays in "Done today". */
export const DONE_TODAY_WINDOW_MS = 24 * 60 * 60 * 1000;

const RUNNING_STATES: SessionState[] = [
  'running',
  'provisioning',
  'requested',
  'ready',
  'idle',
  'terminating',
];
const DONE_STATES: SessionState[] = ['terminated', 'failed'];

export interface SimpleSessionGroups {
  needsYou: Session[];
  running: Session[];
  doneToday: Session[];
  older: Session[];
  archived: Session[];
}

/** The five buckets the Simple-mode session list shows, newest first in each. */
export function groupForSimpleMode(sessions: Session[], now = Date.now()): SimpleSessionGroups {
  const groups: SimpleSessionGroups = {
    needsYou: [],
    running: [],
    doneToday: [],
    older: [],
    archived: [],
  };

  for (const session of sessions) {
    if (session.state === 'awaiting_input') {
      groups.needsYou.push(session);
      continue;
    }
    if (RUNNING_STATES.includes(session.state)) {
      groups.running.push(session);
      continue;
    }
    if (DONE_STATES.includes(session.state)) {
      const recent = now - sessionActivityTs(session) < DONE_TODAY_WINDOW_MS;
      (recent ? groups.doneToday : groups.older).push(session);
      continue;
    }
    groups.archived.push(session);
  }

  for (const bucket of Object.values(groups)) {
    bucket.sort(compareSessionsByActivity);
  }
  return groups;
}

export function groupByForge(sessions: Session[]): SessionSection[] {
  const grouped = new Map<string, Session[]>();

  for (const session of sessions) {
    const label = forgeGroupLabel(session);
    const bucket = grouped.get(label);
    if (bucket) {
      bucket.push(session);
    } else {
      grouped.set(label, [session]);
    }
  }

  return [...grouped.entries()]
    .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
    .map(([label, groupedSessions]) => ({
      label,
      sessions: [...groupedSessions].sort(compareSessionsByActivity),
    }));
}
