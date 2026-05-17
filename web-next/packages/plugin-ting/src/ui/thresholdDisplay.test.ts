import { describe, expect, it } from 'vitest';
import { formatThreshold, normalizeThreshold } from './thresholdDisplay';

describe('thresholdDisplay', () => {
  it('normalizes percentage-style thresholds', () => {
    expect(normalizeThreshold(70)).toBe(0.7);
    expect(formatThreshold(70)).toBe('0.70');
  });

  it('leaves fractional thresholds untouched', () => {
    expect(normalizeThreshold(0.85)).toBe(0.85);
    expect(formatThreshold(0.85)).toBe('0.85');
  });
});
