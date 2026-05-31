import { describe, it, expect } from 'vitest';
import { formatDuration } from './format';

describe('formatDuration', () => {
  it('renders sub-second durations in milliseconds', () => {
    expect(formatDuration(999)).toBe('999ms');
  });

  it('omits trailing seconds when the duration is an exact minute', () => {
    expect(formatDuration(60_000)).toBe('1m');
  });
});
