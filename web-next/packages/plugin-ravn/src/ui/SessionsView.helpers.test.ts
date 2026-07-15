import { describe, expect, it } from 'vitest';
import {
  buildInitLine,
  buildTranscript,
  deriveAnchorTime,
  derivePersonaKey,
  deriveRelativeAge,
  deriveTimeline,
  deriveTrigger,
  filterTranscript,
  formatCurrency,
  formatShortTime,
  formatTimelineStamp,
  formatTokenCount,
  groupSessionsByRavn,
  normalizeLabel,
  parseEmit,
  pickDefaultSession,
  previewJson,
  shortSessionId,
  stripBraces,
  summarizeSession,
  synthesizeTranscript,
  taskLine,
  titleForSession,
} from './SessionsView';

const baseSession = {
  id: '10000001-0000-4000-8000-000000000001',
  createdAt: '2026-05-01T10:00:00.000Z',
  personaName: 'Ravn',
  personaRole: 'build',
  personaLetter: 'C',
  title: 'Implement login form',
  status: 'completed',
  costUsd: 1.25,
  tokenCount: 1500,
  messageCount: 4,
};

describe('SessionsView helpers', () => {
  it('formats labels, money, tokens, and timestamps', () => {
    expect(normalizeLabel(undefined)).toBe('—');
    expect(normalizeLabel('review_ready-now')).toBe('review ready now');
    expect(formatTokenCount(undefined)).toBe('—');
    expect(formatTokenCount(950)).toBe('950');
    expect(formatTokenCount(1500)).toBe('1.5k');
    expect(formatCurrency(undefined)).toBe('—');
    expect(formatCurrency(1.236)).toBe('$1.24');
    expect(formatShortTime('2026-05-01T10:11:12.000Z')).toBe('10:11:12');
    expect(formatTimelineStamp('2026-05-01T10:11:12.000Z')).toBe('10:11 2026-05-01');
    expect(shortSessionId(baseSession as never)).toBe('s-001');
  });

  it('derives persona keys, triggers, and session titles from role and title', () => {
    expect(
      derivePersonaKey({ ...baseSession, personaRole: 'review', title: 'Review PR #42' } as never),
    ).toBe('review-arbiter');
    expect(
      derivePersonaKey({
        ...baseSession,
        personaRole: 'qa',
        title: 'Integration test sweep',
      } as never),
    ).toBe('verifier');
    expect(
      derivePersonaKey({ ...baseSession, personaRole: 'plan', title: 'Sprint plan' } as never),
    ).toBe('architect');
    expect(derivePersonaKey({ ...baseSession, personaRole: 'knowledge' } as never)).toBe(
      'mimir-curator',
    );

    expect(
      deriveTrigger({ ...baseSession, personaRole: 'review', title: 'Review PR #42' } as never),
    ).toBe('pr-review');
    expect(deriveTrigger({ ...baseSession, personaRole: 'observe' } as never)).toBe('cron.hourly');
    expect(deriveTrigger({ ...baseSession, personaRole: 'knowledge' } as never)).toBe('docs.sync');
    expect(deriveTrigger({ ...baseSession, personaRole: 'report' } as never)).toBe('cron.daily');
    expect(deriveTrigger({ ...baseSession, personaRole: 'qa' } as never)).toBe('qa-suite');
    expect(deriveTrigger({ ...baseSession, personaRole: 'coord' } as never)).toBe(
      'deploy-orchestrator',
    );
    expect(deriveTrigger({ ...baseSession, personaRole: 'plan' } as never)).toBe(
      'planning-request',
    );
    expect(deriveTrigger(baseSession as never)).toBe('manual');

    expect(titleForSession({ ...baseSession, title: undefined } as never)).toBe('Session 10000001');
    expect(buildInitLine('Ravn-A', 'manual')).toContain('raven=Ravn-A');
    expect(taskLine(baseSession as never, 'manual')).toContain('Manual: Implement login form');
    expect(taskLine(baseSession as never, 'qa-suite')).toContain('Triggered by qa-suite');
  });

  it('parses emit payloads and previews JSON strings safely', () => {
    expect(stripBraces('{{ hello }}')).toBe('hello');
    expect(previewJson('{"path":"src/app.ts"}')).toBe('src/app.ts');
    expect(previewJson('{"content":"// note"}')).toBe('note');
    expect(previewJson('{"foo":"bar","count":2}')).toBe('foo=bar count=2');
    expect(previewJson('not-json')).toBe('not-json');

    expect(parseEmit('{"event":"work.completed","payload":{"status":"ok"}}')).toEqual({
      eventName: 'work.completed',
      attrs: ['status: ok'],
    });
    expect(parseEmit('{fallback}')).toEqual({ eventName: 'fallback', attrs: [] });
  });

  it('synthesizes transcripts for running, stopped, failed, and completed sessions', () => {
    expect(
      synthesizeTranscript(
        { ...baseSession, status: 'running' } as never,
        { personaName: 'Ravn' } as never,
        'Coder',
        'manual',
      ).map((entry) => entry.kind),
    ).toEqual(['system', 'user', 'thought', 'tool']);

    expect(
      synthesizeTranscript(
        { ...baseSession, status: 'stopped' } as never,
        { personaName: 'Ravn' } as never,
        'Coder',
        'manual',
      )
        .map((entry) => entry.text ?? entry.eventName)
        .join(' '),
    ).toContain('session closed · read-only');

    expect(
      synthesizeTranscript(
        { ...baseSession, status: 'failed' } as never,
        { personaName: 'Ravn' } as never,
        'Coder',
        'manual',
      )
        .map((entry) => entry.text ?? '')
        .join(' '),
    ).toContain('budget exceeded');

    expect(
      synthesizeTranscript(
        baseSession as never,
        { personaName: 'Ravn' } as never,
        'Coder',
        'manual',
      )
        .map((entry) => entry.eventName)
        .filter(Boolean),
    ).toContain('work.completed');
  });

  it('builds transcript entries, filters them, and summarizes the latest useful content', () => {
    const messages = [
      { id: 'u1', kind: 'user', ts: '2026-05-01T10:01:00.000Z', content: 'Do the thing' },
      {
        id: 't1',
        kind: 'think',
        ts: '2026-05-01T10:02:00.000Z',
        content: 'Persona=Coder. Thinking carefully.',
      },
      {
        id: 'c1',
        kind: 'tool_call',
        ts: '2026-05-01T10:03:00.000Z',
        toolName: 'read',
        content: '{"path":"src/a.ts"}',
      },
      {
        id: 'r1',
        kind: 'tool_result',
        ts: '2026-05-01T10:04:00.000Z',
        toolName: 'read',
        content: '{"content":"// loaded"}',
      },
      { id: 'a1', kind: 'asst', ts: '2026-05-01T10:05:00.000Z', content: 'All set.' },
      {
        id: 'e1',
        kind: 'emit',
        ts: '2026-05-01T10:06:00.000Z',
        content: '{"event":"work.completed","payload":{"status":"ok"}}',
      },
      { id: 's1', kind: 'system', ts: '2026-05-01T10:07:00.000Z', content: 'system note' },
    ];

    const entries = buildTranscript(
      baseSession as never,
      { personaName: 'Ravn' } as never,
      'Coder',
      messages as never,
    );
    expect(entries.map((entry) => entry.kind)).toEqual([
      'system',
      'user',
      'thought',
      'tool',
      'assistant',
      'emit',
      'system',
    ]);
    expect(entries[3]).toMatchObject({ toolName: 'read', args: 'src/a.ts', result: 'loaded' });
    expect(filterTranscript(entries, 'chat').map((entry) => entry.kind)).toEqual([
      'user',
      'thought',
      'assistant',
      'emit',
    ]);
    expect(filterTranscript(entries, 'tools').map((entry) => entry.kind)).toContain('tool');
    expect(filterTranscript(entries, 'system').map((entry) => entry.kind)).toContain('system');
    expect(filterTranscript(entries, 'all')).toHaveLength(entries.length);
    expect(summarizeSession(baseSession as never, entries, 'Coder')).toBe('Thinking carefully.');
    expect(
      summarizeSession(
        { ...baseSession, status: 'running' } as never,
        [{ id: 'u1', kind: 'user', ts: baseSession.createdAt, text: 'Only user' }] as never,
        'Coder',
      ),
    ).toContain('Coder is working through');
    expect(
      summarizeSession(
        { ...baseSession, status: 'completed' } as never,
        [{ id: 'u1', kind: 'user', ts: baseSession.createdAt, text: 'Only user' }] as never,
        'Coder',
      ),
    ).toContain('Coder wrapped');
  });

  it('builds timeline entries and chooses default sessions and anchor ages', () => {
    const entries = [
      { id: 'sys', kind: 'system', ts: baseSession.createdAt, text: 'init' },
      { id: 'usr', kind: 'user', ts: '2026-05-01T10:01:00.000Z', text: 'prompt' },
      { id: 'th', kind: 'thought', ts: '2026-05-01T10:02:00.000Z', text: 'thought' },
      {
        id: 'tool',
        kind: 'tool',
        ts: '2026-05-01T10:03:00.000Z',
        toolName: 'read',
        args: '…',
        result: 'ok',
      },
      { id: 'asst', kind: 'assistant', ts: '2026-05-01T10:04:00.000Z', text: 'answer' },
      { id: 'emit', kind: 'emit', ts: '2026-05-01T10:05:00.000Z', eventName: 'work.completed' },
    ];
    const timeline = deriveTimeline(entries as never, baseSession as never);
    expect(timeline[0]).toMatchObject({ label: 'started · 10:00 2026-05-01', tone: 'info' });
    expect(timeline.slice(1).map((entry) => entry.label)).toEqual([
      'session init',
      'user instruction',
      'reasoning',
      'tool · read',
      'raven answer',
    ]);

    expect(deriveRelativeAge('2026-05-01T10:00:00.000Z', '2026-05-01T10:30:00.000Z')).toBe('30m');
    expect(deriveRelativeAge('2026-05-01T10:00:00.000Z', '2026-05-01T13:00:00.000Z')).toBe('3h');
    expect(deriveAnchorTime([])).toMatch(/T/);
    expect(
      deriveAnchorTime([
        { ...baseSession, createdAt: '2026-05-01T10:00:00.000Z' },
        { ...baseSession, id: 'b', createdAt: '2026-05-01T11:00:00.000Z' },
      ] as never),
    ).toBe('2026-05-01T11:04:00.000Z');

    expect(pickDefaultSession([], null)).toBeNull();
    expect(
      pickDefaultSession(
        [
          { ...baseSession, id: 'old', createdAt: '2026-05-01T09:00:00.000Z', status: 'completed' },
          { ...baseSession, id: 'run', createdAt: '2026-05-01T10:00:00.000Z', status: 'running' },
        ] as never,
        null,
      ),
    ).toBe('run');
    expect(
      pickDefaultSession(
        [
          { ...baseSession, id: 'one', status: 'completed' },
          { ...baseSession, id: 'two', status: 'failed' },
        ] as never,
        'two',
      ),
    ).toBe('two');
  });

  it('groups sessions by their owning ravn and keeps active sessions first', () => {
    const ravens = [
      {
        id: 'aaaaaaaa-0000-4000-8000-000000000001',
        personaName: 'builder',
        status: 'active',
        model: 'model-a',
        createdAt: '2026-05-01T09:00:00.000Z',
      },
      {
        id: 'bbbbbbbb-0000-4000-8000-000000000002',
        personaName: 'reviewer',
        status: 'idle',
        model: 'model-b',
        createdAt: '2026-05-01T09:00:00.000Z',
      },
    ];
    const sessions = [
      {
        ...baseSession,
        id: '10000001-0000-4000-8000-000000000010',
        ravnId: ravens[0]!.id,
        status: 'idle',
      },
      {
        ...baseSession,
        id: '10000001-0000-4000-8000-000000000011',
        ravnId: ravens[1]!.id,
        status: 'idle',
      },
      {
        ...baseSession,
        id: '10000001-0000-4000-8000-000000000012',
        ravnId: ravens[0]!.id,
        status: 'running',
      },
    ];

    const groups = groupSessionsByRavn(sessions as never, ravens as never);

    expect(groups).toHaveLength(2);
    expect(groups[0]?.ravn?.personaName).toBe('builder');
    expect(groups[0]?.sessions.map((session) => session.status)).toEqual(['running', 'idle']);
    expect(groups[1]?.ravn?.personaName).toBe('reviewer');
  });
});
