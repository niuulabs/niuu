/**
 * Session domain — lifecycle state machine for Völundr dev pods.
 *
 * Canonical lifecycle:
 *   requested → provisioning → ready → running ⇄ idle → terminating → terminated
 *
 * Any pre-terminal state can also transition to `failed`.
 * `failed` can transition to `terminated` (clean-up complete).
 */

export type SessionState =
  | 'requested'
  | 'provisioning'
  | 'ready'
  | 'running'
  | 'idle'
  | 'terminating'
  | 'terminated'
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
  id: string;
  ravnId: string;
  personaName: string;
  sagaId?: string;
  raidId?: string;
  templateId: string;
  clusterId: string;
  state: SessionState;
  startedAt: string;
  readyAt?: string;
  lastActivityAt?: string;
  terminatedAt?: string;
  resources: SessionResources;
  env: Record<string, string>;
  events: SessionEvent[];
  /** Boot progress 0–1, present while state is requested/provisioning. */
  bootProgress?: number;
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
}

/** Legal transitions in the session lifecycle state machine. */
const VALID_TRANSITIONS: Record<SessionState, readonly SessionState[]> = {
  requested: ['provisioning', 'failed'],
  provisioning: ['ready', 'failed'],
  ready: ['running', 'terminating', 'failed'],
  running: ['idle', 'terminating', 'failed'],
  idle: ['running', 'terminating', 'failed'],
  terminating: ['terminated', 'failed'],
  terminated: [],
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
