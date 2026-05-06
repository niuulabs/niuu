import { describe, it, expect } from 'vitest';
import { money, tokens } from './formatters';

describe('money', () => {
  it('formats cents to dollars', () => {
    expect(money(0)).toBe('$0.00');
    expect(money(328)).toBe('$3.28');
    expect(money(100)).toBe('$1.00');
  });

  it('formats large amounts with k suffix', () => {
    expect(money(100_000)).toBe('$1.0k');
    expect(money(150_000)).toBe('$1.5k');
  });
});

describe('tokens', () => {
  it('formats small counts as-is', () => {
    expect(tokens(42)).toBe('42');
    expect(tokens(999)).toBe('999');
  });

  it('formats thousands with k suffix', () => {
    expect(tokens(1_000)).toBe('1.0k');
    expect(tokens(82_400)).toBe('82.4k');
  });

  it('formats millions with M suffix', () => {
    expect(tokens(1_000_000)).toBe('1.00M');
    expect(tokens(2_500_000)).toBe('2.50M');
  });
});
