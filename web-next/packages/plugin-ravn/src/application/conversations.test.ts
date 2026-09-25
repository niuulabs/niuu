import { describe, expect, it } from 'vitest';
import {
  belongsToRavn,
  conversationTitle,
  pickConversation,
  sortConversations,
} from './conversations';
import { filterResidentLogs, formatLogFields, normalizeLevel } from './residentLogFilter';
import { makeRavn, makeSession } from '../testing/fixtures';
import type { ResidentLogEntry } from '../ports';

describe('conversations', () => {
  it('matches sessions to their ravn and target', () => {
    const ravn = makeRavn();
    expect(belongsToRavn(makeSession(), ravn)).toBe(true);
    expect(belongsToRavn(makeSession({ ravnId: 'other' }), ravn)).toBe(false);
    expect(belongsToRavn(makeSession({ instanceId: 'target-b' }), ravn)).toBe(false);
    expect(belongsToRavn(makeSession({ instanceId: undefined }), ravn)).toBe(true);
  });

  it('puts live conversations first, then the newest', () => {
    const sorted = sortConversations([
      makeSession({ id: 'old', status: 'idle', createdAt: '2026-09-01T00:00:00Z' }),
      makeSession({ id: 'new', status: 'idle', createdAt: '2026-09-20T00:00:00Z' }),
      makeSession({ id: 'live', status: 'running', createdAt: '2026-08-01T00:00:00Z' }),
    ]);
    expect(sorted.map((session) => session.id)).toEqual(['live', 'new', 'old']);
  });

  it('picks the requested conversation, else the best one', () => {
    const sessions = [makeSession({ id: 'a', status: 'idle' }), makeSession({ id: 'b' })];
    expect(pickConversation(sessions, 'a')?.id).toBe('a');
    expect(pickConversation(sessions, 'missing')?.id).toBe('b');
    expect(pickConversation(sessions, null)?.id).toBe('b');
    expect(pickConversation([], null)).toBeNull();
  });

  it('titles untitled conversations by id', () => {
    expect(conversationTitle(makeSession({ title: '  ' }))).toBe('Conversation 22222222');
    expect(conversationTitle(makeSession())).toBe('Morning check');
  });
});

describe('resident log filter', () => {
  const entry = (level: string, message: string): ResidentLogEntry => ({
    timestampMs: 1,
    level,
    source: 'ravn',
    target: 'ravn.agent',
    message,
    fields: { case: 'c-1' },
  });
  const entries = [entry('INFO', 'turn started'), entry('warning', 'slow'), entry('ERROR', 'boom')];

  it('filters by severity', () => {
    expect(filterResidentLogs(entries, 'all', '')).toHaveLength(3);
    expect(filterResidentLogs(entries, 'warnings', '').map((e) => e.message)).toEqual([
      'slow',
      'boom',
    ]);
    expect(filterResidentLogs(entries, 'errors', '').map((e) => e.message)).toEqual(['boom']);
  });

  it('searches message, target and fields', () => {
    expect(filterResidentLogs(entries, 'all', 'turn')).toHaveLength(1);
    expect(filterResidentLogs(entries, 'all', 'c-1')).toHaveLength(3);
    expect(filterResidentLogs(entries, 'all', 'nope')).toHaveLength(0);
  });

  it('normalizes levels and formats fields', () => {
    expect(normalizeLevel(' WARN ')).toBe('warn');
    expect(normalizeLevel('')).toBe('info');
    expect(formatLogFields({ a: '1', b: '2' })).toBe('a=1 b=2');
  });
});
