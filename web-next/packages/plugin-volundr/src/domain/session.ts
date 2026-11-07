/**
 * Session domain — lifecycle state machine for Völundr dev pods.
 *
 * Canonical lifecycle:
 *   requested → provisioning → ready → running ⇄ idle → terminating → terminated
 *
 * Any pre-terminal state can also transition to `failed`.
 * `failed` can transition to `terminated` (clean-up complete).
 */

import type {
  TrackerIssue,
  SessionCoordination,
  SessionReadState,
  SessionSource,
  SessionStartup,
} from '../models/volundr.model';

export type SessionState =
  | 'requested'
  | 'provisioning'
  | 'ready'
  | 'running'
  | 'idle'
  // Blocked waiting on the user (an AskUserQuestion / confirmation / permission).
  | 'awaiting_input'
  | 'terminating'
  | 'terminated'
  | 'archived'
  | 'failed';

export interface SessionResources {
  cpuRequest: number;
  cpuLimit: number;
  cpuUsed: number;
  memRequestMi: number;
  memLimitMi: number;
  memUsedMi: number;
  gpuCount: number;
  diskUsedMi?: number;
  diskLimitMi?: number;
}

export interface SessionFileStats {
  added: number;
  modified: number;
  deleted: number;
}

export interface SessionEvent {
  ts: string;
  kind: string;
  body: string;
}

export type ConnectionType = 'cli' | 'ide' | 'api';

export interface Session {
  model?: string;
  sessionDefinition?: string;
  source?: SessionSource;
  coordination?: SessionCoordination;
  id: string;
  ravnId: string;
  name?: string;
  title?: string;
  personaName: string;
  sagaId?: string;
  runId?: string;
  templateId: string;
  clusterId: string;
  clusterName?: string;
  state: SessionState;
  startedAt: string;
  readyAt?: string;
  lastActivityAt?: string;
  activityStateSince?: string | null;
  turnStartedAt?: string | null;
  terminatedAt?: string;
  resources: SessionResources;
  env: Record<string, string>;
  events: SessionEvent[];
  /** The latest launch's stages, as the runtime backend reported them. */
  startup?: SessionStartup | null;
  /** How the session is being accessed. */
  connectionType?: ConnectionType;
  /** Tokens consumed (input side). */
  tokensIn?: number;
  /** Tokens consumed (output side). */
  tokensOut?: number;
  /** Cost in cents. */
  costCents?: number;
  /** One-line preview of the last message or action (≤80 chars). */
  preview?: string;
  /** File change summary for this session's workspace. */
  files?: SessionFileStats;
  /** Linked tracker issue when the session was launched from a ticket. */
  trackerIssue?: TrackerIssue;
  /** Where the session originated ('volundr' | 'claude' | 'codex'); absent means volundr-native. */
  origin?: string;
  /** Why Forge failed the session, as Forge reported it. */
  error?: string;
  /** The caller's read state for the session's latest reply; absent when the host reports none. */
  readState?: SessionReadState;
  /** The agent's own one-line summary of what the session is doing, when it wrote one. */
  shortDescription?: string;
  /** Seconds Forge worked on the session in the last 24 hours; absent when not recorded. */
  workedSeconds24h?: number;
}

/** A newer read or unread action won; the caller must look at the current state again. */
export class SessionReadStateConflictError extends Error {
  constructor(
    message = 'This session was marked read or unread elsewhere. Review it and try again.',
  ) {
    super(message);
    this.name = 'SessionReadStateConflictError';
  }
}

/**
 * A fork got as far as creating its child session, then a later step (naming or starting it)
 * failed. The child exists and is named here, so the caller can still show and fix it.
 */
export class SessionForkError extends Error {
  constructor(
    readonly childId: string,
    message: string,
  ) {
    super(message);
    this.name = 'SessionForkError';
  }
}

/** Legal transitions in the session lifecycle state machine. */
const VALID_TRANSITIONS: Record<SessionState, readonly SessionState[]> = {
  requested: ['provisioning', 'failed'],
  provisioning: ['ready', 'failed'],
  ready: ['running', 'terminating', 'failed'],
  running: ['idle', 'awaiting_input', 'terminating', 'failed'],
  idle: ['running', 'awaiting_input', 'terminating', 'failed'],
  awaiting_input: ['running', 'idle', 'terminating', 'failed'],
  terminating: ['terminated', 'failed'],
  terminated: [],
  archived: [],
  failed: ['terminated'],
};

/**
 * Returns true when transitioning from `from` → `to` is a legal move in the
 * Völundr session lifecycle state machine.
 */
export function canTransition(from: SessionState, to: SessionState): boolean {
  return (VALID_TRANSITIONS[from] as readonly string[]).includes(to);
}

/**
 * Returns a new Session with the state updated to `to`.
 * Throws an Error when the transition is illegal.
 */
export function transitionSession(session: Session, to: SessionState): Session {
  if (!canTransition(session.state, to)) {
    throw new Error(`Invalid session state transition: ${session.state} → ${to}`);
  }
  return { ...session, state: to };
}

/** Prefer the stamped harness, then imported origin, then the model for legacy sessions. */
export function sessionHarness(
  session: Pick<Session, 'model' | 'sessionDefinition' | 'origin'>,
): 'claude' | 'codex' | undefined {
  const definition = session.sessionDefinition?.toLowerCase();
  if (definition) {
    if (definition.includes('claude')) return 'claude';
    if (definition.includes('codex')) return 'codex';
    return undefined;
  }
  if (session.origin === 'claude' || session.origin === 'codex') return session.origin;
  const model = session.model?.toLowerCase() ?? '';
  if (/^(claude|fable|opus|sonnet|haiku)(-|$)/.test(model)) return 'claude';
  if (/^(gpt-|codex|o[134](?:-|$))/.test(model)) return 'codex';
  return undefined;
}
