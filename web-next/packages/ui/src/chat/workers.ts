import type { ChatMessage, MeshEvent, MeshOutcomeEvent, RoomParticipant } from './types';
import { extractOutcomeBlock, parseOutcomeFields } from './components/OutcomeCard';
import { resolveParticipantColor } from './utils/participantColor';

/**
 * A coordinator's workers, as Lexi names them: the agents in a session's room (Ravn peers)
 * that the session leads. Their outcomes, verdicts and the order of the flow they form are
 * derived here once, for the workers strip, the progress lane, the panel and the list.
 */

/** Participant types that lead or watch the room rather than work in it. */
const LEAD_TYPES = new Set(['skuld', 'human', 'workflow']);

export type OutcomeTone = 'pass' | 'changes' | 'blocked' | 'info';

export interface WorkerOutcome {
  peerId: string;
  persona: string;
  eventType: string;
  verdict?: string;
  summary?: string;
  fields: Readonly<Record<string, unknown>>;
  at: Date;
  tone: OutcomeTone;
  /** The message that carried the outcome block, when it came from the transcript. */
  messageId?: string;
}

export type WorkerStatus = 'working' | 'idle' | 'error';

export interface Worker {
  peerId: string;
  name: string;
  persona: string;
  color: string;
  status: WorkerStatus;
  /** Event types the worker listens for, and the ones it emits. */
  consumes: readonly string[];
  emits: readonly string[];
  outcomes: readonly WorkerOutcome[];
  latest?: WorkerOutcome;
}

export interface WorkerStage {
  worker: Worker;
  /** Times the worker ran again after its first outcome (a review sending code back). */
  loops: number;
  /** The stage the flow is on now. */
  current: boolean;
}

const TONES: Record<string, OutcomeTone> = {
  pass: 'pass',
  passed: 'pass',
  approve: 'pass',
  approved: 'pass',
  framed: 'pass',
  published: 'pass',
  merged: 'pass',
  success: 'pass',
  done: 'pass',
  complete: 'pass',
  completed: 'pass',
  needs_changes: 'changes',
  changes_requested: 'changes',
  retry: 'changes',
  needs_review: 'changes',
  fail: 'blocked',
  failed: 'blocked',
  blocked: 'blocked',
  escalate: 'blocked',
  help_needed: 'blocked',
  error: 'blocked',
};

/** How a verdict reads at a glance; an unknown verdict is information, not a pass. */
export function verdictTone(verdict: string | undefined): OutcomeTone {
  if (!verdict) return 'info';
  return TONES[verdict.trim().toLowerCase()] ?? 'info';
}

const LABELS: Record<string, string> = {
  needs_changes: 'Changes requested',
  changes_requested: 'Changes requested',
  pass: 'Passed',
  fail: 'Failed',
  help_needed: 'Needs help',
};

/** "needs_changes" → "Changes requested"; other verdicts read as words. */
export function verdictLabel(verdict: string | undefined): string {
  if (!verdict) return 'Reported';
  const key = verdict.trim().toLowerCase();
  const known = LABELS[key];
  if (known) return known;
  const words = key.replace(/[_-]+/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function isWorker(participant: RoomParticipant): boolean {
  return !LEAD_TYPES.has(participant.participantType ?? 'ravn');
}

function workerStatus(status: string | undefined): WorkerStatus {
  switch (status) {
    case 'thinking':
    case 'tool_executing':
    case 'busy':
    case 'running':
    case 'active':
      return 'working';
    case 'error':
      return 'error';
    default:
      return 'idle';
  }
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined;
}

function fromEvent(event: MeshOutcomeEvent): WorkerOutcome {
  const verdict = event.verdict ?? text(event.fields?.verdict) ?? text(event.fields?.status);
  return {
    peerId: event.participantId,
    persona: event.persona,
    eventType: event.eventType,
    verdict,
    summary: event.summary ?? text(event.fields?.summary),
    fields: event.fields ?? {},
    at: event.timestamp,
    tone: verdictTone(verdict),
  };
}

function fromMessage(message: ChatMessage): WorkerOutcome | null {
  const peerId = message.participant?.peerId;
  if (!peerId || message.role !== 'assistant') return null;
  const block = extractOutcomeBlock(message.content);
  if (!block) return null;
  const fields = parseOutcomeFields(block.raw);
  const verdict = fields.verdict ?? fields.status;
  return {
    peerId,
    persona: message.participant?.persona ?? peerId,
    eventType: fields.event_type ?? fields.eventType ?? 'outcome',
    verdict,
    summary: fields.summary ?? fields.result,
    fields,
    at: message.createdAt,
    tone: verdictTone(verdict),
    messageId: message.id,
  };
}

/**
 * Every outcome the workers reported, oldest first. A live outcome event and the
 * transcript message that carries the same block are one outcome, not two.
 */
export function workerOutcomes(
  meshEvents: readonly MeshEvent[],
  messages: readonly ChatMessage[],
): WorkerOutcome[] {
  const fromMessages = messages.flatMap((message) => {
    const outcome = fromMessage(message);
    return outcome ? [outcome] : [];
  });
  const seen = new Set(
    fromMessages.map(
      (outcome) => `${outcome.peerId}\u0000${outcome.verdict ?? ''}\u0000${outcome.summary ?? ''}`,
    ),
  );
  const fromEvents = meshEvents
    .filter((event): event is MeshOutcomeEvent => event.type === 'outcome')
    .map(fromEvent)
    .filter(
      (outcome) =>
        !seen.has(`${outcome.peerId}\u0000${outcome.verdict ?? ''}\u0000${outcome.summary ?? ''}`),
    );
  return [...fromMessages, ...fromEvents].sort((a, b) => a.at.getTime() - b.at.getTime());
}

/** The room's workers in participant order, each with its outcomes and status. */
export function roomWorkers(
  participants: ReadonlyMap<string, RoomParticipant>,
  meshEvents: readonly MeshEvent[],
  messages: readonly ChatMessage[],
): Worker[] {
  const outcomes = workerOutcomes(meshEvents, messages);
  return [...participants.values()].filter(isWorker).map((participant) => {
    const own = outcomes.filter((outcome) => outcome.peerId === participant.peerId);
    return {
      peerId: participant.peerId,
      name: participant.displayName || participant.persona,
      persona: participant.persona,
      color: resolveParticipantColor(participant.peerId, participant.color),
      status: workerStatus(participant.status),
      consumes: participant.subscribesTo ?? [],
      emits: participant.emits ?? [],
      outcomes: own,
      latest: own.at(-1),
    };
  });
}

/**
 * The workers in flow order: a worker comes after the ones whose events it listens for.
 * The chain starts at workers that listen for nothing another worker emits; a loop back
 * (review sending code back to the coder) does not move the coder after the reviewer.
 * Entries that feed nobody, and workers the chain never reaches, come last.
 */
export function workerStages(workers: readonly Worker[]): WorkerStage[] {
  const emitted = new Set(workers.flatMap((worker) => worker.emits));
  const ordered: Worker[] = [];
  const placed = new Set<string>();
  const consumed = new Set(workers.flatMap((worker) => worker.consumes));
  // Entries that hand work on start the chain; anything left (an entry that feeds nobody,
  // a worker the chain never reaches) follows in participant order.
  const queue = workers.filter(
    (worker) =>
      !worker.consumes.some((event) => emitted.has(event)) &&
      worker.emits.some((event) => consumed.has(event)),
  );
  while (queue.length > 0) {
    const next = queue.shift()!;
    if (placed.has(next.peerId)) continue;
    placed.add(next.peerId);
    ordered.push(next);
    for (const candidate of workers) {
      if (placed.has(candidate.peerId)) continue;
      if (candidate.consumes.some((event) => next.emits.includes(event))) queue.push(candidate);
    }
  }
  for (const worker of workers) if (!placed.has(worker.peerId)) ordered.push(worker);

  const working = ordered.filter((worker) => worker.status === 'working');
  const latest = ordered
    .flatMap((worker) => (worker.latest ? [worker.latest] : []))
    .sort((a, b) => b.at.getTime() - a.at.getTime())[0];
  const currentIds = new Set(
    working.length > 0
      ? working.map((worker) => worker.peerId)
      : latest && latest.tone !== 'pass'
        ? [latest.peerId]
        : [],
  );
  return ordered.map((worker) => ({
    worker,
    loops: Math.max(0, worker.outcomes.length - 1),
    current: currentIds.has(worker.peerId),
  }));
}
