import { describe, it, expect } from 'vitest';
import { appendExecEntry, updateExecEntry, type ExecEntry } from './exec';

const BASE_ENTRY: ExecEntry = {
  id: 'e1',
  command: 'ls -la',
  output: '',
  status: 'running',
  startedAt: 1000,
};

describe('appendExecEntry', () => {
  it('appends to an empty history', () => {
    const result = appendExecEntry([], BASE_ENTRY);
    expect(result).toHaveLength(1);
    expect(result[0]).toEqual(BASE_ENTRY);
  });

  it('appends to an existing history', () => {
    const existing: ExecEntry[] = [{ ...BASE_ENTRY, id: 'e0' }];
    const result = appendExecEntry(existing, BASE_ENTRY);
    expect(result).toHaveLength(2);
    expect(result[1]).toEqual(BASE_ENTRY);
  });

  it('does not mutate the original array', () => {
    const original: ExecEntry[] = [{ ...BASE_ENTRY, id: 'e0' }];
    const originalRef = original;
    appendExecEntry(original, BASE_ENTRY);
    expect(original).toBe(originalRef);
    expect(original).toHaveLength(1);
  });

  it('preserves existing entries when appending', () => {
    const a: ExecEntry = { ...BASE_ENTRY, id: 'a', command: 'echo a' };
    const b: ExecEntry = { ...BASE_ENTRY, id: 'b', command: 'echo b' };
    const result = appendExecEntry([a], b);
    expect(result[0]).toEqual(a);
    expect(result[1]).toEqual(b);
  });
});

describe('updateExecEntry', () => {
  it('updates the matching entry', () => {
    const history = [BASE_ENTRY, { ...BASE_ENTRY, id: 'e2', command: 'pwd' }];
    const result = updateExecEntry(history, 'e1', { status: 'success', finishedAt: 2000 });
    expect(result[0]).toMatchObject({ id: 'e1', status: 'success', finishedAt: 2000 });
    expect(result[1]).toMatchObject({ id: 'e2', status: 'running' });
  });

  it('does not affect non-matching entries', () => {
    const history = [BASE_ENTRY, { ...BASE_ENTRY, id: 'e2' }];
    const result = updateExecEntry(history, 'e1', { output: 'hello' });
    expect(result[1]).toEqual({ ...BASE_ENTRY, id: 'e2' });
  });

  it('returns same-length array', () => {
    const history = [BASE_ENTRY];
    const result = updateExecEntry(history, 'e1', { output: 'done' });
    expect(result).toHaveLength(1);
  });

  it('does not mutate the original array', () => {
    const history = [{ ...BASE_ENTRY }];
    updateExecEntry(history, 'e1', { output: 'changed' });
    expect(history[0]!.output).toBe('');
  });

  it('handles missing id gracefully — leaves all entries unchanged', () => {
    const history = [BASE_ENTRY];
    const result = updateExecEntry(history, 'nonexistent', { output: 'nope' });
    expect(result[0]).toEqual(BASE_ENTRY);
  });
});
