import { describe, expect, it } from 'vitest';
import type { ChatMessage, MeshEvent, RoomParticipant } from './types';
import { roomWorkers, verdictLabel, verdictTone, workerOutcomes, workerStages } from './workers';

const at = (minute: number) => new Date(Date.UTC(2026, 10, 5, 5, minute));

const participant = (
  peerId: string,
  persona: string,
  consumes: string[],
  emits: string[],
  extra: Partial<RoomParticipant> = {},
): RoomParticipant => ({
  peerId,
  persona,
  participantType: 'ravn',
  subscribesTo: consumes,
  emits,
  ...extra,
});

const room = new Map<string, RoomParticipant>(
  [
    participant('lead', 'Lead', [], [], { participantType: 'skuld' }),
    participant(
      'flock-reviewer',
      'reviewer',
      ['code.changed'],
      ['review.passed', 'review.changes_requested'],
    ),
    participant(
      'flock-coder',
      'coder',
      ['code.requested', 'review.changes_requested'],
      ['code.changed'],
      { status: 'tool_executing' },
    ),
    participant('flock-spec', 'specification-framer', ['spec.requested'], ['code.requested']),
    participant('flock-pub', 'capability-publisher', ['delivery.merged'], ['delivery.completed']),
    participant('human-1', 'Jozef', [], [], { participantType: 'human' }),
  ].map((p) => [p.peerId, p]),
);

const outcomeMessage = (
  id: string,
  peerId: string,
  persona: string,
  minute: number,
  block: string,
): ChatMessage => ({
  id,
  role: 'assistant',
  content: `Done.\n\n---outcome---\n${block}\n---end---`,
  createdAt: at(minute),
  participant: { peerId, persona },
});

describe('verdicts', () => {
  it('reads verdicts as pass, changes, blocked or information', () => {
    expect(verdictTone('pass')).toBe('pass');
    expect(verdictTone('Framed')).toBe('pass');
    expect(verdictTone('needs_changes')).toBe('changes');
    expect(verdictTone('help_needed')).toBe('blocked');
    expect(verdictTone('something-new')).toBe('info');
    expect(verdictTone(undefined)).toBe('info');
  });

  it('names verdicts in words', () => {
    expect(verdictLabel('needs_changes')).toBe('Changes requested');
    expect(verdictLabel('framed')).toBe('Framed');
    expect(verdictLabel('needs_review')).toBe('Needs review');
    expect(verdictLabel(undefined)).toBe('Reported');
  });
});

describe('workerOutcomes', () => {
  it('counts a live outcome and the message carrying the same block once', () => {
    const events: MeshEvent[] = [
      {
        type: 'outcome',
        id: 'e1',
        timestamp: at(10),
        participantId: 'flock-reviewer',
        participant: {},
        persona: 'reviewer',
        eventType: 'review.changes_requested',
        verdict: 'needs_changes',
        summary: 'Freshness reads the wrong timestamp.',
        fields: {},
      },
      {
        type: 'outcome',
        id: 'e2',
        timestamp: at(12),
        participantId: 'flock-coder',
        participant: {},
        persona: 'coder',
        eventType: 'code.changed',
        verdict: 'pass',
        summary: 'Fixed.',
        fields: {},
      },
    ];
    const messages = [
      outcomeMessage(
        'm1',
        'flock-reviewer',
        'reviewer',
        10,
        'verdict: needs_changes\nsummary: Freshness reads the wrong timestamp.',
      ),
    ];
    const outcomes = workerOutcomes(events, messages);
    expect(outcomes.map((o) => [o.peerId, o.tone, o.messageId])).toEqual([
      ['flock-reviewer', 'changes', 'm1'],
      ['flock-coder', 'pass', undefined],
    ]);
  });
});

describe('roomWorkers and workerStages', () => {
  const messages = [
    outcomeMessage(
      'm1',
      'flock-spec',
      'specification-framer',
      1,
      'verdict: framed\nsummary: Three probes.',
    ),
    outcomeMessage('m2', 'flock-coder', 'coder', 3, 'verdict: pass\nsummary: Probes implemented.'),
    outcomeMessage(
      'm3',
      'flock-reviewer',
      'reviewer',
      5,
      'verdict: needs_changes\nsummary: Wrong timestamp.',
    ),
    outcomeMessage('m4', 'flock-coder', 'coder', 7, 'verdict: pass\nsummary: Fixed.'),
  ];

  it('keeps only the workers, with their outcomes, latest verdict and status', () => {
    const workers = roomWorkers(room, [], messages);
    expect(workers.map((w) => w.persona)).toEqual([
      'reviewer',
      'coder',
      'specification-framer',
      'capability-publisher',
    ]);
    const coder = workers.find((w) => w.persona === 'coder')!;
    expect(coder.status).toBe('working');
    expect(coder.outcomes).toHaveLength(2);
    expect(coder.latest?.summary).toBe('Fixed.');
    expect(workers.find((w) => w.persona === 'capability-publisher')?.latest).toBeUndefined();
  });

  it('keeps a worker that took part and left the room, marked as left', () => {
    // The live room lists only who is connected: the reviewer and framer have gone.
    const now = new Map([...room].filter(([id]) => !['flock-reviewer', 'flock-spec'].includes(id)));
    const outcome: MeshEvent = {
      type: 'outcome',
      id: 'o1',
      participantId: 'flock-spec',
      participant: {},
      persona: 'specification-framer',
      eventType: 'code.requested',
      verdict: 'framed',
      timestamp: at(1),
    };
    const workers = roomWorkers(now, [outcome], messages);
    expect(workers.map((w) => [w.persona, w.status])).toEqual([
      ['coder', 'working'],
      ['capability-publisher', 'idle'],
      ['specification-framer', 'left'],
      ['reviewer', 'left'],
    ]);
    expect(workers.find((w) => w.persona === 'reviewer')?.latest?.verdict).toBe('needs_changes');
  });

  it('orders the flow by who listens for whose events, ignoring the loop back', () => {
    const stages = workerStages(roomWorkers(room, [], messages));
    expect(stages.map((s) => s.worker.persona)).toEqual([
      'specification-framer',
      'coder',
      'reviewer',
      'capability-publisher',
    ]);
    const coder = stages.find((s) => s.worker.persona === 'coder')!;
    expect(coder.loops).toBe(1);
    expect(coder.current).toBe(true);
    expect(stages.filter((s) => s.current)).toHaveLength(1);
  });

  it('points at the last outcome that did not pass when nobody is working', () => {
    const idle = new Map(
      [...room].map(([id, p]) => [id, { ...p, status: 'idle' }] as [string, RoomParticipant]),
    );
    const stages = workerStages(roomWorkers(idle, [], messages.slice(0, 3)));
    expect(stages.filter((s) => s.current).map((s) => s.worker.persona)).toEqual(['reviewer']);
  });
});
