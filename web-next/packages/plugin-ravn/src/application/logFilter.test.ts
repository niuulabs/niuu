import { describe, it, expect } from 'vitest';
import { applyLogFilter, EMPTY_LOG_FILTER, type LogEntry } from './logFilter';
import type { Message } from '../domain/message';

function entry(overrides: Partial<Message & { ravnId?: string; personaName?: string }>): LogEntry {
  const message: Message = {
    id: overrides.id ?? '00000000-0000-4000-8000-000000000001',
    sessionId: overrides.sessionId ?? 'sess-1',
    kind: overrides.kind ?? 'user',
    content: overrides.content ?? 'hello',
    ts: overrides.ts ?? '2026-04-15T09:00:00Z',
    toolName: overrides.toolName,
  };
  return {
    message,
    ravnId: overrides.ravnId ?? 'ravn-1',
    personaName: overrides.personaName ?? 'coder',
  };
}

describe('applyLogFilter', () => {
  const entries: LogEntry[] = [
    entry({
      id: '1',
      kind: 'user',
      content: 'hello world',
      ravnId: 'ravn-1',
      personaName: 'coder',
    }),
    entry({
      id: '2',
      kind: 'tool_call',
      content: '{"path":"src/main.ts"}',
      ravnId: 'ravn-1',
      personaName: 'coder',
      toolName: 'file.read',
    }),
    entry({
      id: '3',
      kind: 'emit',
      content: '{"event":"code.changed"}',
      ravnId: 'ravn-2',
      personaName: 'reviewer',
    }),
    entry({
      id: '4',
      kind: 'think',
      content: 'reasoning step',
      ravnId: 'ravn-2',
      personaName: 'reviewer',
    }),
  ];

  it('returns all entries with empty filter', () => {
    expect(applyLogFilter(entries, EMPTY_LOG_FILTER)).toHaveLength(4);
  });

  it('filters by ravnId', () => {
    const result = applyLogFilter(entries, { ...EMPTY_LOG_FILTER, ravnId: 'ravn-2' });
    expect(result).toHaveLength(2);
    expect(result.every((e) => e.ravnId === 'ravn-2')).toBe(true);
  });

  it('filters by single kind', () => {
    const result = applyLogFilter(entries, { ...EMPTY_LOG_FILTER, kinds: ['emit'] });
    expect(result).toHaveLength(1);
    expect(result[0]?.message.kind).toBe('emit');
  });

  it('filters by multiple kinds', () => {
    const result = applyLogFilter(entries, { ...EMPTY_LOG_FILTER, kinds: ['user', 'think'] });
    expect(result).toHaveLength(2);
  });

  it('filters by query (content match)', () => {
    const result = applyLogFilter(entries, { ...EMPTY_LOG_FILTER, query: 'hello' });
    expect(result).toHaveLength(1);
    expect(result[0]?.message.content).toBe('hello world');
  });

  it('filters by query (persona match)', () => {
    const result = applyLogFilter(entries, { ...EMPTY_LOG_FILTER, query: 'reviewer' });
    expect(result).toHaveLength(2);
  });

  it('filters by query (tool name match)', () => {
    const result = applyLogFilter(entries, { ...EMPTY_LOG_FILTER, query: 'file.read' });
    expect(result).toHaveLength(1);
    expect(result[0]?.message.toolName).toBe('file.read');
  });

  it('query is case-insensitive', () => {
    const result = applyLogFilter(entries, { ...EMPTY_LOG_FILTER, query: 'HELLO' });
    expect(result).toHaveLength(1);
  });

  it('combines ravnId and kind filters', () => {
    const result = applyLogFilter(entries, {
      ...EMPTY_LOG_FILTER,
      ravnId: 'ravn-1',
      kinds: ['user'],
    });
    expect(result).toHaveLength(1);
    expect(result[0]?.message.kind).toBe('user');
  });

  it('returns empty when nothing matches', () => {
    const result = applyLogFilter(entries, { ...EMPTY_LOG_FILTER, query: 'zzznomatch' });
    expect(result).toHaveLength(0);
  });

  it('does not mutate the input array', () => {
    const copy = [...entries];
    applyLogFilter(entries, { ...EMPTY_LOG_FILTER, kinds: ['emit'] });
    expect(entries).toHaveLength(copy.length);
  });
});
