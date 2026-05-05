import { describe, it, expect } from 'vitest';
import { messageKindSchema, messageSchema } from './message';

// ---------------------------------------------------------------------------
// messageKindSchema
// ---------------------------------------------------------------------------

describe('messageKindSchema', () => {
  it.each(['user', 'asst', 'system', 'tool_call', 'tool_result', 'emit', 'think'])(
    'accepts kind "%s"',
    (k) => {
      expect(messageKindSchema.parse(k)).toBe(k);
    },
  );

  it('rejects an unknown kind', () => {
    expect(() => messageKindSchema.parse('assistant')).toThrow();
  });

  it('rejects empty string', () => {
    expect(() => messageKindSchema.parse('')).toThrow();
  });
});

// ---------------------------------------------------------------------------
// messageSchema
// ---------------------------------------------------------------------------

const validMessage = {
  id: '00000001-0000-4000-8000-000000000001',
  sessionId: 'a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c',
  kind: 'user',
  content: 'Please implement the login form',
  ts: '2026-04-15T09:12:35Z',
} as const;

describe('messageSchema', () => {
  it('round-trips a valid message', () => {
    const result = messageSchema.parse(validMessage);
    expect(result).toMatchObject(validMessage);
  });

  it('accepts an optional toolName for tool_call', () => {
    const result = messageSchema.parse({
      ...validMessage,
      kind: 'tool_call',
      toolName: 'file.read',
    });
    expect(result.toolName).toBe('file.read');
  });

  it('omits toolName when not provided', () => {
    const result = messageSchema.parse(validMessage);
    expect(result.toolName).toBeUndefined();
  });

  it('rejects invalid UUID for id', () => {
    expect(() => messageSchema.parse({ ...validMessage, id: 'bad' })).toThrow();
  });

  it('rejects empty sessionId', () => {
    expect(() => messageSchema.parse({ ...validMessage, sessionId: '' })).toThrow();
  });

  it('rejects invalid kind', () => {
    expect(() => messageSchema.parse({ ...validMessage, kind: 'assistant' })).toThrow();
  });

  it('accepts empty content (tool results can be empty)', () => {
    const result = messageSchema.parse({ ...validMessage, content: '' });
    expect(result.content).toBe('');
  });

  it('rejects malformed ts', () => {
    expect(() => messageSchema.parse({ ...validMessage, ts: 'bad-date' })).toThrow();
  });

  it('accepts all valid kinds', () => {
    for (const kind of [
      'user',
      'asst',
      'system',
      'tool_call',
      'tool_result',
      'emit',
      'think',
    ] as const) {
      expect(messageSchema.parse({ ...validMessage, kind }).kind).toBe(kind);
    }
  });
});
