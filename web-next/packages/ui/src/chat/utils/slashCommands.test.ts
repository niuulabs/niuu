import { describe, it, expect } from 'vitest';
import { buildCommandList } from './slashCommands';

describe('buildCommandList', () => {
  it('builds command list from slashCommands and skills', () => {
    const result = buildCommandList(['clear', 'compact'], ['summarize']);
    expect(result).toHaveLength(3);
    expect(result[0]).toEqual({ name: 'clear', type: 'command' });
    expect(result[2]).toEqual({ name: 'summarize', type: 'skill' });
  });

  it('returns empty array for empty inputs', () => {
    expect(buildCommandList([], [])).toEqual([]);
  });
});
